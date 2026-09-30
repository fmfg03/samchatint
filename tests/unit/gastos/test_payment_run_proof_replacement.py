"""Replacing a proof must never repeat a payment or erase its evidence."""

from datetime import date, datetime, timezone
from pathlib import Path
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
from devnous.gastos.utils.receipt_bytes import (
    DocumentoAdjuntoMeta,
    html_documento_archivos_cell,
    html_documento_archivos_detail,
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
    session.execute.return_value = MagicMock()
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
    session.execute.return_value = MagicMock()
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


def test_historical_document_proof_is_labeled_sustituido():
    document_id = uuid4()
    html = html_documento_archivos_cell(document_id, [DocumentoAdjuntoMeta(
        id=uuid4(), categoria="comprobante_pago", mime_type="application/pdf",
        tipo_archivo="application/pdf", nombre_archivo="old.pdf", activo=False,
    )])
    assert "Comprobante pago (sustituido)" in html


def test_document_detail_offers_audited_correction_for_current_payment_proof():
    document_id, current_id, old_id = uuid4(), uuid4(), uuid4()
    current = DocumentoAdjuntoMeta(
        id=current_id,
        categoria="comprobante_pago",
        mime_type="application/pdf",
        tipo_archivo="application/pdf",
        nombre_archivo="vigente.pdf",
        activo=True,
    )
    old = DocumentoAdjuntoMeta(
        id=old_id,
        categoria="comprobante_pago",
        mime_type="application/pdf",
        tipo_archivo="application/pdf",
        nombre_archivo="anterior.pdf",
        activo=False,
    )
    html = html_documento_archivos_detail(
        document_id,
        [old, current],
        replaceable_adjunto_ids={current_id},
    )
    assert "Corregir comprobante" in html
    assert "Sustituir archivo" in html
    assert f'name="previous_id" value="{current_id}"' in html
    assert f'name="return_to" value="/documentos/{document_id}"' in html
    assert "El archivo anterior no se borra" in html
    assert html.count("Corregir comprobante") == 1
    assert "anterior.pdf" in html
    assert "(sustituido)" in html


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
    assert f"/payment-run/documento/{document_id}/comprobante/{proof_id}" in accounting


def test_paid_document_uses_effective_payment_date():
    row = {
        "id": uuid4(), "status": "pagada", "fecha_pago": date(2026, 9, 20),
        "fecha_pago_efectiva": date(2026, 9, 22),
    }
    html = _render_payment_run_items([row])
    assert '<td data-sort-value="2026-09-22">2026-09-22</td>' in html


def test_paid_loan_row_shows_current_and_prior_proofs():
    loan_id = uuid4()
    row = {
        "id": loan_id, "entity_type": "prestamo", "status": "pagada",
        "numero_referencia": "P-2601", "proof_filename": "nuevo.pdf",
        "proof_history": [{"previous_filename": "anterior.pdf"}],
    }
    html = _render_payment_run_items([row], can_confirm_payment=True)
    assert f"/prestamo/{loan_id}/comprobante/current" in html
    assert f"/prestamo/{loan_id}/comprobante/0" in html
    assert "Sustituir archivo" in html


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


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "validation", "unexpected"])
@pytest.mark.parametrize("return_target", ["document", "other_document", "external"])
async def test_document_replacement_can_return_to_document_detail(
    monkeypatch, outcome, return_target
):
    session = AsyncMock()
    document_id, old_id = uuid4(), uuid4()
    document = SimpleNamespace(
        id=document_id,
        estado="pagado",
        tipo="SOLICITUD",
        pagado_en=datetime.now(timezone.utc),
        fecha_pago_efectiva=None,
        monto_solicitado=100,
        monto_total=100,
        currency="MXN",
        beneficiario_empleado=None,
        proveedor_cliente=None,
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
    replacement = AsyncMock(
        side_effect=(
            SolicitudValidationError("stale", "Comprobante sustituido")
            if outcome == "validation"
            else (
                RuntimeError("Replacement unavailable")
                if outcome == "unexpected"
                else None
            )
        )
    )
    monkeypatch.setattr(admin_routes, "replace_payment_run_proof", replacement)
    target = {
        "document": f"/documentos/{document_id}",
        "other_document": f"/documentos/{uuid4()}",
        "external": "https://example.test/documentos",
    }[return_target]
    response = await admin_routes.admin_payment_run_replace_document_proof(
        documento_id=document_id,
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        previous_id=old_id,
        comprobante_pago=SimpleNamespace(
            filename="nuevo.pdf",
            content_type="application/pdf",
            read=AsyncMock(return_value=b"%PDF-1.4\n"),
        ),
        motivo="Archivo equivocado",
        return_to=target,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    if return_target == "document":
        assert location.startswith(f"/documentos/{document_id}?")
        assert "vista=pagadas" not in location
    else:
        assert location.startswith("/admin/finanzas/payment-run?")
        assert "vista=pagadas" in location
        assert "example.test" not in location
    replacement.assert_awaited_once()
    if outcome == "success":
        assert "success_msg=" in location
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    else:
        assert "error_msg=" in location
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
    assert document.estado == "pagado"
    assert document.monto_total == 100


@pytest.mark.asyncio
async def test_paid_view_loads_only_proof_metadata(monkeypatch):
    document_id, proof_id = uuid4(), uuid4()
    list_docs = AsyncMock(side_effect=[[], [], [{
        "id": document_id, "status": "pagada", "numero_referencia": "S-26",
        "monto": 100,
    }]])
    list_loans = AsyncMock(side_effect=[[], [], []])
    monkeypatch.setattr(admin_routes, "list_payment_run_items", list_docs)
    monkeypatch.setattr(admin_routes, "list_prestamo_payment_run_items", list_loans)
    monkeypatch.setattr(admin_routes, "list_payment_run_closures", AsyncMock(return_value=[]))
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.all.return_value = [
        SimpleNamespace(id=proof_id, documento_id=document_id, nombre_archivo="vigente.pdf")
    ]
    response = await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}), session=session,
        current_empleado=SimpleNamespace(id=uuid4(), rol="contabilidad", nombre="Conta"),
        vista="pagadas", status="pendientes", date_from=None, date_to=None, q=None,
    )
    assert list_docs.await_args_list[2].kwargs["status_filter"] == "pagadas"
    assert list_loans.await_args_list[2].kwargs["status_filter"] == "pagadas"
    assert "vigente.pdf" in response.body.decode()
    selected = session.execute.call_args.args[0]
    assert "ruta_archivo" not in str(selected)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "file", "blocked", "date", "service", "unexpected"])
async def test_document_replacement_rejects_invalid_evidence(monkeypatch, mode):
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id, tipo="SOLICITUD", estado="pagado",
        pagado_en=datetime.now(timezone.utc), fecha_pago_efectiva=datetime(2026, 9, 22).date(),
        monto_solicitado=100, monto_total=100, currency="MXN",
        beneficiario_empleado=None, proveedor_cliente=None,
    )
    session = AsyncMock()
    session.get.return_value = None if mode == "missing" else document
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", lambda _: None)
    monkeypatch.setattr(admin_routes, "_payment_proof_expected_beneficiary", lambda _: "Beneficiario")
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    review = SimpleNamespace(
        status="match", detected_date=None, confirmation_blocked=False,
    )
    if mode == "blocked": review.confirmation_blocked = True
    if mode == "date": review.detected_date = datetime(2026, 9, 23).date()
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: review,
    )
    replace = AsyncMock(side_effect=RuntimeError("db") if mode == "unexpected" else
                        SolicitudValidationError("stale", "Sustituido") if mode == "service" else None)
    monkeypatch.setattr(admin_routes, "replace_payment_run_proof", replace)
    upload = None if mode == "file" else SimpleNamespace(
        filename="proof.pdf", content_type="application/pdf",
        read=AsyncMock(return_value=b"%PDF-1.4"),
    )
    if mode == "missing":
        with pytest.raises(HTTPException) as exc:
            await admin_routes.admin_payment_run_replace_document_proof(
                document_id, session, SimpleNamespace(id=uuid4()), uuid4(), upload, "Corrección"
            )
        assert exc.value.status_code == 404
    else:
        response = await admin_routes.admin_payment_run_replace_document_proof(
            document_id, session, SimpleNamespace(id=uuid4()), uuid4(), upload, "Corrección"
        )
        assert response.status_code == 303
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["denied", "missing", "unpaid", "bad_file", "success", "service"])
async def test_loan_replacement_route_authority_and_history(monkeypatch, mode):
    loan_id = uuid4()
    loan = SimpleNamespace(
        id=loan_id, estado="pagada", comprobante_pago_storage_key="prestamos/old.pdf",
    )
    if mode == "unpaid": loan.estado = "aprobada"
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None if mode == "missing" else loan
    session.execute.return_value = result
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    def confirm(_):
        if mode == "denied": raise admin_routes.PaymentRunPermissionError()
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", confirm)
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    from devnous.gastos.routes import user_routes
    save = AsyncMock(return_value=("new.pdf", "prestamos/new.pdf"))
    monkeypatch.setattr(user_routes, "_save_prestamo_payment_proof_upload", save)
    update = MagicMock(side_effect=admin_routes.PrestamoWorkflowError("bad", "Error") if mode == "service" else None)
    monkeypatch.setattr(admin_routes, "replace_prestamo_payment_proof", update)
    upload = SimpleNamespace(
        filename="new.pdf", content_type="application/pdf",
        read=AsyncMock(return_value=b"%PDF-1.4"), seek=AsyncMock(),
    )
    args = dict(prestamo_id=loan_id, session=session, current_empleado=SimpleNamespace(id=uuid4()),
                comprobante_pago=upload, motivo="Archivo equivocado", previous_id="loan")
    if mode in {"denied", "missing"}:
        with pytest.raises(HTTPException) as exc:
            await admin_routes.admin_payment_run_replace_loan_proof(**args)
        assert exc.value.status_code == (403 if mode == "denied" else 404)
    else:
        if mode == "bad_file": args["comprobante_pago"] = None
        response = await admin_routes.admin_payment_run_replace_loan_proof(**args)
        assert response.status_code == 303
        if mode == "success":
            upload.seek.assert_awaited_once_with(0)
            update.assert_called_once()
            session.commit.assert_awaited_once()
            assert "FOR UPDATE" in str(session.execute.await_args.args[0])
        else:
            session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_document_proof_download_requires_payment_run_access_and_matching_category(monkeypatch):
    document_id, proof_id = uuid4(), uuid4()
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(tipo="SOLICITUD", estado="pagado")
    actor = SimpleNamespace(id=uuid4())
    payload = {
        "categoria": "comprobante_pago", "ruta_archivo": "payload",
        "nombre_archivo": "pago.pdf", "mime_type": "application/pdf",
    }
    fetch = AsyncMock(return_value=payload)
    monkeypatch.setattr(admin_routes, "fetch_adjunto_payload", fetch)
    monkeypatch.setattr(admin_routes, "load_adjunto_payload_bytes", AsyncMock(return_value=(b"%PDF-1.4", "application/pdf", "pago.pdf")))
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    response = await admin_routes.admin_payment_run_download_document_proof(document_id, proof_id, session, actor)
    assert response.body == b"%PDF-1.4"
    assert fetch.await_args.kwargs == {"adjunto_id": proof_id, "documento_id": document_id}
    payload["categoria"] = "contrato"
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_payment_run_download_document_proof(document_id, proof_id, session, actor)
    assert exc.value.status_code == 404
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: (_ for _ in ()).throw(admin_routes.PaymentRunPermissionError()))
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_payment_run_download_document_proof(document_id, proof_id, session, actor)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_loan_proof_download_is_limited_to_recorded_keys(monkeypatch, tmp_path):
    loan_id = uuid4()
    folder = tmp_path / "data" / "gastos" / "prestamos" / str(loan_id)
    folder.mkdir(parents=True)
    (folder / "old.pdf").write_bytes(b"old")
    (folder / "new.pdf").write_bytes(b"new")
    from devnous.gastos.routes import user_routes
    monkeypatch.setattr(user_routes, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    loan = SimpleNamespace(
        estado="pagada", comprobante_pago_storage_key=f"prestamos/{loan_id}/new.pdf",
        comprobante_pago_filename="new.pdf", metadata_json={
            "payment_proof_replacements": [{
                "previous_storage_key": f"prestamos/{loan_id}/old.pdf",
                "previous_filename": "old.pdf",
            }]
        },
    )
    session = AsyncMock()
    session.get.return_value = loan
    actor = SimpleNamespace(id=uuid4())
    current = await admin_routes.admin_payment_run_download_loan_proof(loan_id, "current", session, actor)
    old = await admin_routes.admin_payment_run_download_loan_proof(loan_id, "0", session, actor)
    assert Path(current.path).name == "new.pdf"
    assert Path(old.path).name == "old.pdf"
    with pytest.raises(HTTPException):
        await admin_routes.admin_payment_run_download_loan_proof(loan_id, "1", session, actor)
    loan.comprobante_pago_storage_key = "../../outside.pdf"
    with pytest.raises(HTTPException):
        await admin_routes.admin_payment_run_download_loan_proof(loan_id, "current", session, actor)
