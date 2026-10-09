from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import admin_routes
from devnous.gastos.services import documento_payment_service, payment_run_service
from devnous.gastos.services import customer_success_audit


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"run_date": date(2026, 10, 8), "closed_at": datetime(2026, 10, 9)}, date(2026, 10, 8)),
        ({"closed_at": datetime(2026, 10, 9, tzinfo=timezone.utc)}, date(2026, 10, 9)),
        ({"run_date": "2026-10-08"}, date(2026, 10, 8)),
        ({"fecha_pago": date(2026, 10, 1)}, None),
    ],
)
def test_cutoff_date_uses_recorded_cutoff_not_scheduled_date(row, expected):
    assert payment_run_service.payment_run_cutoff_date(row) == expected


def _document(**overrides):
    return SimpleNamespace(
        **{
            "id": uuid4(), "numero_referencia": "S-260001",
            "tipo": "SOLICITUD", "estado": "en_proceso_pago",
            "pagado_en": None, "fecha_pago": date(2026, 10, 1),
            "fecha_pago_efectiva": None,
            **overrides,
        }
    )


def _session(cutoff):
    result = MagicMock()
    result.mappings.return_value.first.return_value = cutoff
    return AsyncMock(execute=AsyncMock(return_value=result))


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, "2026-10-07"])
async def test_accounting_confirmation_uses_cutoff_or_override_with_audit(monkeypatch, override):
    cutoff = {"closure_id": uuid4(), "run_date": date(2026, 10, 8)}
    session = _session(cutoff)
    document = _document()
    actor = SimpleNamespace(id=uuid4(), rol="contabilidad")
    audit = AsyncMock()
    monkeypatch.setattr(payment_run_service, "record_customer_success_audit_event", audit)

    selected = await payment_run_service.prepare_payment_run_confirmation_date(
        session, documento=document, actor=actor, fecha_pago_efectiva=override,
    )

    assert selected == date(2026, 10, 7 if override else 8)
    assert document.fecha_pago_efectiva == selected
    assert document.fecha_pago == date(2026, 10, 1)
    assert document.estado == "en_proceso_pago"
    assert cutoff["run_date"] == date(2026, 10, 8)
    metadata = audit.await_args.kwargs["metadata"]
    assert metadata["before_fecha_pago_efectiva"] is None
    assert metadata["after_fecha_pago_efectiva"] == selected.isoformat()
    assert metadata["date_source"] == ("accounting" if override else "cutoff")
    assert audit.await_args.kwargs["actor_empleado_id"] == actor.id
    assert audit.await_args.kwargs["strict"] is True
    session.commit.assert_not_awaited()
    assert "UPDATE payment_run" not in str(session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_missing_cutoff_requires_accounting_date(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(payment_run_service, "record_customer_success_audit_event", audit)
    document = _document()
    actor = SimpleNamespace(id=uuid4(), rol="contabilidad")
    with pytest.raises(payment_run_service.PaymentRunValidationError, match="No hay fecha"):
        await payment_run_service.prepare_payment_run_confirmation_date(
            _session(None), documento=document, actor=actor,
        )
    assert document.fecha_pago == date(2026, 10, 1)
    audit.assert_not_awaited()
    assert await payment_run_service.prepare_payment_run_confirmation_date(
        _session(None), documento=document, actor=actor, fecha_pago_efectiva="2026-10-09",
    ) == date(2026, 10, 9)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["aprobado", "pagado", "rechazado"])
async def test_confirmation_date_rejects_invalid_states(state):
    session = _session(None)
    with pytest.raises(payment_run_service.PaymentRunValidationError):
        await payment_run_service.prepare_payment_run_confirmation_date(
            session, documento=_document(estado=state),
            actor=SimpleNamespace(id=uuid4(), rol="contabilidad"), fecha_pago_efectiva="2026-10-09",
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_date_rejects_finance_without_accounting_authority():
    session = _session(None)
    with pytest.raises(payment_run_service.PaymentRunPermissionError):
        await payment_run_service.prepare_payment_run_confirmation_date(
            session, documento=_document(),
            actor=SimpleNamespace(id=uuid4(), rol="finanzas"), fecha_pago_efectiva="2026-10-09",
        )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_override_never_changes_document(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(payment_run_service, "record_customer_success_audit_event", audit)
    document = _document()
    with pytest.raises(payment_run_service.PaymentRunValidationError):
        await payment_run_service.prepare_payment_run_confirmation_date(
            _session({"run_date": date(2026, 10, 8)}), documento=document,
            actor=SimpleNamespace(id=uuid4(), rol="contabilidad"), fecha_pago_efectiva="invalid",
        )
    assert document.fecha_pago == date(2026, 10, 1)
    audit.assert_not_awaited()


def test_proof_form_defaults_to_cutoff_and_shows_missing_cutoff():
    document = _document()
    row = {
        "id": document.id, "numero_referencia": document.numero_referencia,
        "can_upload_payment_proof": True, "fecha_pago": date(2026, 10, 1),
        "confirmation_date": date(2026, 10, 8),
    }
    html = admin_routes._render_payment_run_items(
        [row], can_confirm_payment=True, payment_proof_selection=True,
    )
    assert 'name="fecha_pago_efectiva" value="2026-10-08"' in html
    assert f'data-payment-date-document="{document.id}"' in html
    row.update(confirmation_date=None, cutoff_date_missing=True)
    html = admin_routes._render_payment_run_items([row], can_confirm_payment=True)
    assert "Sin fecha de corte vinculada" in html
    assert 'name="fecha_pago_efectiva" value=""' in html


@pytest.mark.asyncio
async def test_single_proof_applies_selected_date_before_registering_payment(monkeypatch):
    document = _document()
    actor = SimpleNamespace(id=uuid4(), rol="contabilidad")
    session = _session({"closure_id": uuid4(), "run_date": date(2026, 10, 8)})
    session.get.return_value = document
    monkeypatch.setattr(payment_run_service, "record_customer_success_audit_event", AsyncMock())
    monkeypatch.setattr(admin_routes, "add_solicitud_documento_adjuntos", AsyncMock())
    from devnous.gastos.services import payment_proof_review_service
    review = SimpleNamespace(
        status="revision_required", detected_date=None, reasons=[],
        evidence_source="none", detected_amount=None, detected_currency=None,
        detected_beneficiary=None, template_id=None,
    )
    monkeypatch.setattr(
        payment_proof_review_service, "review_payment_proof", lambda **k: review
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    observed_dates = []

    async def register(*args, **kwargs):
        observed_dates.append(document.fecha_pago_efectiva)
        assert document.fecha_pago == date(2026, 10, 1)
        return SimpleNamespace(documento=document)

    monkeypatch.setattr(documento_payment_service, "register_document_payment", register)
    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        document.id, SimpleNamespace(headers={}, query_params={}), session, actor,
        SimpleNamespace(filename="proof.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"%PDF")),
        fecha_pago_efectiva="2026-10-07",
    )
    assert response.status_code == 303
    assert "success_msg" in response.headers["location"]
    assert observed_dates == [date(2026, 10, 7)]


@pytest.mark.asyncio
@pytest.mark.parametrize("second_date", ["2026-10-09", "invalid"])
async def test_bulk_dates_are_per_document_and_invalid_date_rolls_back_batch(monkeypatch, second_date):
    first, second = _document(), _document()
    documents = {first.id: first, second.id: second}
    actor = SimpleNamespace(id=uuid4(), rol="contabilidad")
    session = _session({"closure_id": uuid4(), "run_date": date(2026, 10, 8)})
    session.get.side_effect = lambda model, key: documents[key]
    audit = AsyncMock()
    monkeypatch.setattr(payment_run_service, "record_customer_success_audit_event", audit)
    attachments = AsyncMock()
    monkeypatch.setattr(admin_routes, "add_solicitud_documento_adjuntos", attachments)
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    from devnous.gastos.services import payment_proof_review_service
    review = SimpleNamespace(
        status="revision_required", detected_date=None, reasons=[],
        evidence_source="none", detected_amount=None, detected_currency=None,
        detected_beneficiary=None, template_id=None,
    )
    monkeypatch.setattr(
        payment_proof_review_service, "review_payment_proof", lambda **k: review
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    observed_dates = []

    async def register(*args, documento_id, **kwargs):
        observed_dates.append((documento_id, documents[documento_id].fecha_pago_efectiva))
        assert kwargs["commit"] is False
        assert documents[documento_id].fecha_pago == date(2026, 10, 1)
        return SimpleNamespace(documento=documents[documento_id])

    notifications = []
    monkeypatch.setattr(documento_payment_service, "register_document_payment", register)
    monkeypatch.setattr(documento_payment_service, "_schedule_solicitud_paid_telegram_notifications", lambda **kwargs: notifications.append(kwargs))
    upload = SimpleNamespace(filename="proof.pdf", content_type="application/pdf", read=AsyncMock(return_value=b"%PDF"))
    request = SimpleNamespace(headers={}, query_params={})
    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=request, session=session, current_empleado=actor,
        selected_document_ids=[str(first.id), str(second.id)],
        proof_document_ids=[], comprobantes_pago=[upload], apply_one_to_all=True,
        effective_payment_dates=["2026-10-07", second_date],
        payment_proof_resolution_reasons=[],
    )
    assert response.status_code == 303
    if second_date == "invalid":
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()
        attachments.assert_not_awaited()
        assert observed_dates == []
        assert notifications == []
    else:
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        assert observed_dates == [(first.id, date(2026, 10, 7)), (second.id, date(2026, 10, 9))]
        assert attachments.await_count == 2
        assert audit.await_count == 2
        assert [call.kwargs["metadata"]["after_fecha_pago_efectiva"] for call in audit.await_args_list] == ["2026-10-07", "2026-10-09"]
        assert len(notifications) == 2


@pytest.mark.asyncio
async def test_payment_date_audit_failure_stops_confirmation(monkeypatch):
    monkeypatch.setattr(customer_success_audit, "ensure_customer_success_audit_schema", AsyncMock())
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("audit unavailable")
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await customer_success_audit.record_customer_success_audit_event(
            session, action="payment_run.confirmation_date_selected", strict=True,
        )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
