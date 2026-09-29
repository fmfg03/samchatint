"""Replacing a proof must never repeat a payment or erase its evidence."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes
from devnous.gastos.routes.admin_routes import _render_payment_run_items
from devnous.gastos.services.documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
)
from devnous.gastos.services.loan_request_service import (
    PrestamoWorkflowPermissionError,
    replace_prestamo_payment_proof,
)
from devnous.gastos.services.payment_proof_replacement_service import (
    replace_payment_run_proof,
)


@pytest.mark.asyncio
async def test_paid_document_proof_replacement_keeps_old_and_audits_actor():
    document_id, old_id, actor_id = uuid4(), uuid4(), uuid4()
    old = SimpleNamespace(
        id=old_id, nombre_archivo="anterior.pdf", activo=True,
        sustituido_en=None, sustituido_por_adjunto_id=None,
        sustituido_por_empleado_id=None,
    )
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=MagicMock())
    session.execute.return_value.scalar_one_or_none.return_value = old
    proof = await replace_payment_run_proof(
        session,
        documento=SimpleNamespace(
            id=document_id, tipo="SOLICITUD", estado="pagado", pagado_en=datetime.now(timezone.utc)
        ),
        previous_id=old_id, actor_id=actor_id,
        attachment=SolicitudTercerosAttachment(
            raw_bytes=b"%PDF-1.4\n1 0 obj\n", filename="correcto.pdf",
            mime_type="application/pdf", categoria="comprobante_pago",
        ),
        reason="El archivo anterior correspondía a otro pago",
    )
    assert old.activo is False
    assert old.sustituido_por_adjunto_id == proof.id
    assert old.sustituido_por_empleado_id == actor_id
    assert proof.activo is True
    assert proof.documento_id == document_id
    assert any(
        call.args[0].accion == "sustituir_comprobante_pago"
        for call in session.add.call_args_list
        if hasattr(call.args[0], "accion")
    )


@pytest.mark.asyncio
async def test_stale_or_unpaid_document_cannot_replace_proof():
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=MagicMock())
    session.execute.return_value.scalar_one_or_none.return_value = None
    paid = SimpleNamespace(id=uuid4(), tipo="SOLICITUD", estado="pagado", pagado_en=datetime.now(timezone.utc))
    attachment = SolicitudTercerosAttachment(
        raw_bytes=b"%PDF-1.4", filename="proof.pdf", mime_type="application/pdf",
        categoria="comprobante_pago",
    )
    with pytest.raises(SolicitudValidationError):
        await replace_payment_run_proof(
            session, documento=paid, previous_id=uuid4(), actor_id=uuid4(),
            attachment=attachment, reason="Corrección",
        )
    paid.estado = "en_proceso_pago"
    with pytest.raises(SolicitudValidationError):
        await replace_payment_run_proof(
            session, documento=paid, previous_id=uuid4(), actor_id=uuid4(),
            attachment=attachment, reason="Corrección",
        )
    session.add.assert_not_called()


def test_loan_replacement_preserves_history_and_payment_state(monkeypatch):
    actor = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "devnous.gastos.services.loan_request_service.can_confirm_payment_run_payment",
        lambda user: user is actor,
    )
    loan = SimpleNamespace(
        estado="pagada", comprobante_pago_filename="anterior.pdf",
        comprobante_pago_storage_key="prestamos/old.pdf", metadata_json={},
        pagado_en=datetime.now(timezone.utc),
    )
    original_payment_date = loan.pagado_en
    replace_prestamo_payment_proof(
        loan, actor, filename="nuevo.pdf", storage_key="prestamos/new.pdf",
        reason="Archivo equivocado",
    )
    assert loan.pagado_en == original_payment_date
    assert loan.estado == "pagada"
    assert loan.metadata_json["payment_proof_replacements"][0]["previous_storage_key"] == "prestamos/old.pdf"
    assert loan.metadata_json["payment_proof_replacements"][0]["actor_id"] == str(actor.id)
    with pytest.raises(PrestamoWorkflowPermissionError):
        replace_prestamo_payment_proof(
            loan, SimpleNamespace(id=uuid4()), filename="x.pdf",
            storage_key="prestamos/x.pdf", reason="Otro",
        )


def test_paid_rows_offer_replacement_only_to_accounting():
    document_id, proof_id = uuid4(), uuid4()
    row = {"id": document_id, "numero_referencia": "S-2601", "status": "pagada"}
    proof = SimpleNamespace(id=proof_id, nombre_archivo="testigo.pdf")
    readonly = _render_payment_run_items([row], current_proofs={document_id: [proof]})
    accounting = _render_payment_run_items(
        [row], current_proofs={document_id: [proof]}, can_confirm_payment=True
    )
    assert "testigo.pdf" in readonly
    assert "sustituir-comprobante" not in readonly
    assert f'value="{proof_id}"' in accounting
    assert "Sustituir archivo" in accounting


@pytest.mark.asyncio
async def test_document_replacement_route_requires_accounting_before_reading_file(monkeypatch):
    session = AsyncMock()
    actor = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    def reject(_):
        raise admin_routes.PaymentRunPermissionError("Solo Contabilidad")
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", reject)
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_payment_run_replace_document_proof(
            documento_id=uuid4(), session=session, current_empleado=actor,
            previous_id=uuid4(), comprobante_pago=None, motivo="Cambio",
        )
    assert exc.value.status_code == 403
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_replacement_route_never_registers_another_payment(monkeypatch):
    session = AsyncMock()
    document_id, old_id = uuid4(), uuid4()
    document = SimpleNamespace(
        id=document_id, estado="pagado", tipo="SOLICITUD",
        pagado_en=datetime.now(timezone.utc), fecha_pago_efectiva=None,
        monto_solicitado=100, monto_total=100, currency="MXN",
        beneficiario_empleado=None, proveedor_cliente=None,
    )
    session.get.return_value = document
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", lambda _: None)
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(admin_routes, "_payment_proof_expected_beneficiary", lambda _: "Beneficiario")
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: SimpleNamespace(status="match", detected_date=None, confirmation_blocked=False),
    )
    replacement = AsyncMock()
    monkeypatch.setattr(admin_routes, "replace_payment_run_proof", replacement)
    response = await admin_routes.admin_payment_run_replace_document_proof(
        documento_id=document_id, session=session,
        current_empleado=SimpleNamespace(id=uuid4()), previous_id=old_id,
        comprobante_pago=SimpleNamespace(
            filename="nuevo.pdf", content_type="application/pdf",
            read=AsyncMock(return_value=b"%PDF-1.4\n"),
        ),
        motivo="Archivo incorrecto",
    )
    assert response.status_code == 303
    assert "vista=pagadas" in response.headers["location"]
    replacement.assert_awaited_once()
    session.commit.assert_awaited_once()
    assert document.estado == "pagado"
    assert document.monto_total == 100
