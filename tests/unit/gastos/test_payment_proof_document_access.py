"""Paid proof correction remains reachable under canonical Accounting authority."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from devnous.gastos.models import Documento, Empleado
from devnous.gastos.routes import user_routes
from devnous.gastos.services.payment_run_service import PaymentRunPermissionError
from devnous.gastos.utils.receipt_bytes import DocumentoAdjuntoMeta


def _document(kind="terceros", **kwargs):
    values = dict(
        id=uuid4(),
        empleado_id=uuid4(),
        tipo="SOLICITUD",
        estado="pagado",
        pagado_en=datetime.now(timezone.utc),
        numero_referencia="SOL-TEST",
        proyecto_otro="Proyecto",
        monto_solicitado=100,
        currency="MXN",
        creado_en=datetime.now(timezone.utc),
        proveedor_cliente_id=uuid4() if kind == "terceros" else None,
        cuenta_gastos_id=uuid4() if kind == "reembolso" else None,
        beneficiario_empleado_id=uuid4() if kind == "personal" else None,
    )
    values.update(kwargs)
    return Documento(**values)


def _actor(**kwargs):
    values = dict(id=uuid4(), rol="contabilidad", departamento="", permisos=[])
    values.update(kwargs)
    permisos = values.pop("permisos")
    actor = Empleado(**values)
    actor.effective_permissions = permisos
    return actor


def _meta(*, activo=True, categoria="comprobante_pago", id=None):
    return DocumentoAdjuntoMeta(
        id=id or uuid4(),
        categoria=categoria,
        activo=activo,
        mime_type="application/pdf",
        tipo_archivo="application/pdf",
        nombre_archivo="proof.pdf",
    )


@pytest.mark.parametrize("kind", ["terceros", "personal", "reembolso"])
def test_replacement_all_paid_solicitudes_preserves_delete_restriction(kind):
    documento, actor = _document(kind), _actor()
    active, old, support = _meta(), _meta(activo=False), _meta(categoria="supporting")
    assert user_routes._replaceable_solicitud_comprobante_ids(
        documento, actor, [active, old, support]
    ) == {active.id}
    assert (
        user_routes._removable_solicitud_adjunto_ids(
            documento, actor, [active, old, support]
        )
        == set()
    )


@pytest.mark.parametrize(
    "values",
    [
        {"tipo": "INFORME"},
        {"estado": "aprobado"},
        {"pagado_en": None},
    ],
)
def test_replacement_rejects_nonpaid_document(values):
    assert not user_routes._can_finance_replace_comprobante_pago(
        _document(**values), _actor()
    )


def test_replacement_rejects_cancelled_and_unauthorized():
    assert not user_routes._can_finance_replace_comprobante_pago(
        _document(), _actor(), solicitud_cancelada=True
    )
    assert not user_routes._can_finance_replace_comprobante_pago(
        _document(), _actor(rol="empleado")
    )
    assert (
        user_routes._replaceable_solicitud_comprobante_ids(
            _document(), _actor(rol="empleado"), [_meta()]
        )
        == set()
    )


@pytest.mark.parametrize(
    "guard",
    [
        "require_payment_run_access",
        "require_payment_run_payment_confirmation",
    ],
)
def test_replacement_honors_both_canonical_guards(monkeypatch, guard):
    def denied(_):
        raise PaymentRunPermissionError()

    monkeypatch.setattr(user_routes, guard, denied)
    assert not user_routes._can_finance_replace_comprobante_pago(_document(), _actor())


def test_replacement_skips_invalid_attachment_id():
    invalid = SimpleNamespace(id=None, activo=True, categoria="comprobante_pago")
    assert not user_routes._replaceable_solicitud_comprobante_ids(
        _document(), _actor(), [invalid]
    )


def _result(value=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = []
    result.first.return_value = None
    return result


def _session(documento):
    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    owner = Empleado(id=documento.empleado_id, nombre="Solicitante", rol="empleado")
    calls = 0

    async def execute(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _result(documento if calls == 1 else owner if calls == 2 else None)

    session.execute.side_effect = execute
    return session


def _request():
    return Request({"type": "http", "query_string": b"", "headers": []})


def _stub_detail_io(monkeypatch, documento, adjuntos):
    stubs = {
        "fetch_expense_ids_with_archivo_data": set(),
        "fetch_gasto_adjuntos_meta_batch": {},
        "fetch_documento_adjuntos_meta_batch": {documento.id: adjuntos},
        "documento_workflow_locked_reason": None,
        "_render_document_authorization_strategy_evidence": "",
        "_render_document_authorization_route_warning": "",
        "_render_document_authorization_pre_send_preview": "",
        "_load_related_document_project_context": (None, None),
    }
    for name, value in stubs.items():
        monkeypatch.setattr(user_routes, name, AsyncMock(return_value=value))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["terceros", "personal", "reembolso"])
@pytest.mark.parametrize(
    "actor_kwargs",
    [
        {"rol": "contabilidad"},
        {"rol": "empleado", "permisos": ["contabilidad.pagos.marcar_pagado"]},
    ],
)
async def test_actual_detail_renders_paid_correction_without_delete(
    monkeypatch, kind, actor_kwargs
):
    documento, actor = _document(kind), _actor(**actor_kwargs)
    active, old = _meta(), _meta(activo=False)
    _stub_detail_io(monkeypatch, documento, [active, old])
    session = _session(documento)
    html = await user_routes.ver_documento(
        documento.id, _request(), session, actor, next=None
    )
    assert "Corregir comprobante" in html
    assert html.count("Corregir comprobante") == 1
    assert f'name="previous_id" value="{active.id}"' in html
    assert f'name="previous_id" value="{old.id}"' not in html
    assert "/eliminar" not in html
    assert f'action="/documentos/{documento.id}/adjuntos"' not in html
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"estado": "aprobado"},
        {"tipo": "INFORME"},
        {"pagado_en": None},
    ],
)
async def test_actual_detail_does_not_grant_accounting_unpaid_or_informe(
    monkeypatch, values
):
    documento = _document(**values)
    session = _session(documento)
    monkeypatch.setattr(
        user_routes, "_render_documento_access_denied_page", lambda _: "denied"
    )
    assert (
        await user_routes.ver_documento(
            documento.id, _request(), session, _actor(), next=None
        )
        == "denied"
    )
    assert session.execute.await_count == 1
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard",
    [
        "require_payment_run_access",
        "require_payment_run_payment_confirmation",
    ],
)
async def test_actual_detail_rejects_canonical_permission_denial(monkeypatch, guard):
    def denied(_):
        raise PaymentRunPermissionError()

    monkeypatch.setattr(user_routes, guard, denied)
    monkeypatch.setattr(
        user_routes, "_render_documento_access_denied_page", lambda _: "denied"
    )
    documento = _document()
    session = _session(documento)
    assert (
        await user_routes.ver_documento(
            documento.id, _request(), session, _actor(), next=None
        )
        == "denied"
    )
    assert session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_paid_proof_download_uses_same_canonical_authority(monkeypatch, allowed):
    documento = _document("reembolso")
    actor = _actor(rol="contabilidad" if allowed else "empleado")
    session = _session(documento)
    payload = AsyncMock(
        return_value={"ruta_archivo": "data", "nombre_archivo": "proof.pdf"}
    )
    monkeypatch.setattr(user_routes, "fetch_adjunto_payload", payload)
    monkeypatch.setattr(
        user_routes,
        "load_adjunto_payload_bytes",
        AsyncMock(return_value=(b"%PDF-1.7", "application/pdf", "proof.pdf")),
    )
    if allowed:
        response = await user_routes.descargar_documento_adjunto(
            documento.id, uuid4(), session, actor
        )
        assert response.status_code == 200
        assert response.body == b"%PDF-1.7"
        payload.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as exc:
            await user_routes.descargar_documento_adjunto(
                documento.id, uuid4(), session, actor
            )
        assert exc.value.status_code == 403
        payload.assert_not_awaited()
    session.commit.assert_not_awaited()
