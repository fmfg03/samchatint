from datetime import date
from unittest.mock import AsyncMock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.services import documento_payment_service
from devnous.gastos.services.documento_payment_service import (
    DocumentoPaymentValidationError,
    register_document_payment,
)


class FakeSession:
    def __init__(self) -> None:
        self.added = []
        self.committed = False
        self.refreshed = []
        self.flushed = False

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, value) -> None:
        self.refreshed.append(value)

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_document_payment_write_flushes_when_the_caller_owns_the_transaction() -> (
    None
):
    session = FakeSession()
    documento = SimpleNamespace()
    aprobacion = SimpleNamespace()

    await documento_payment_service._finalize_document_payment_write(
        session,
        documento=documento,
        aprobacion=aprobacion,
        commit=False,
    )

    assert session.flushed is True
    assert session.committed is False


@pytest.mark.asyncio
async def test_document_payment_write_commits_and_refreshes_by_default() -> None:
    session = FakeSession()
    documento = SimpleNamespace()
    aprobacion = SimpleNamespace()

    await documento_payment_service._finalize_document_payment_write(
        session,
        documento=documento,
        aprobacion=aprobacion,
        commit=True,
    )

    assert session.committed is True
    assert session.refreshed == [documento, aprobacion]


@pytest.mark.asyncio
async def test_register_document_payment_uses_authenticated_actor_permissions(
    monkeypatch,
) -> None:
    documento_id = uuid4()
    actor_id = uuid4()
    documento = SimpleNamespace(
        id=documento_id,
        tipo="SOLICITUD",
        estado="aprobado",
        gasto_generado_id=None,
        monto_solicitado=100,
        fecha_pago=date(2026, 8, 31),
        metodo_pago="TRANSFERENCIA",
    )
    actor = SimpleNamespace(
        id=actor_id,
        rol="finanzas",
        departamento="Finanzas",
        permissions={"contabilidad.pagos.marcar_pagado"},
    )

    monkeypatch.setattr(
        documento_payment_service,
        "_load_documento_for_payment",
        lambda session, doc_id: _async_value(documento),
    )
    load_actor_called = False

    async def fail_if_load_actor(session, loaded_actor_id):
        nonlocal load_actor_called
        load_actor_called = True
        return None

    monkeypatch.setattr(
        documento_payment_service,
        "_load_actor",
        fail_if_load_actor,
    )
    monkeypatch.setattr(
        documento_payment_service,
        "parse_amex_payment_card_id",
        lambda documento: uuid4(),
    )

    payment_dates = []

    async def fake_amex_posting(*args, **kwargs):
        payment_dates.append(kwargs["payment_date"])
        return SimpleNamespace(status="ready")

    monkeypatch.setattr(
        documento_payment_service,
        "ensure_amex_payment_posting",
        fake_amex_posting,
    )
    monkeypatch.setattr(
        documento_payment_service,
        "_schedule_solicitud_paid_telegram_notifications",
        lambda **kwargs: None,
    )

    result = await register_document_payment(
        FakeSession(),
        documento_id=documento_id,
        actor_id=actor_id,
        actor=actor,
        fecha_pago_efectiva=date(2026, 9, 2),
        payment_proof_review_status="conflict_resolved",
        payment_proof_resolution_reason="Confirmado contra instrucción bancaria autorizada",
        payment_proof_evidence_source="local_pdf_text",
        payment_proof_template_id="spei_transferencia_v1",
    )

    assert result.documento.estado == "pagado"
    assert result.aprobacion.aprobador_id == actor_id
    assert documento.fecha_pago_efectiva == date(2026, 9, 2)
    assert payment_dates == [date(2026, 9, 2)]
    assert "conflict_resolved" in result.aprobacion.comentario
    assert "instrucción bancaria autorizada" in result.aprobacion.comentario
    assert "local_pdf_text" in result.aprobacion.comentario
    assert "spei_transferencia_v1" in result.aprobacion.comentario
    assert not load_actor_called


@pytest.mark.asyncio
async def test_register_document_payment_rejects_actor_mismatch(
    monkeypatch,
) -> None:
    documento_id = uuid4()
    actor_id = uuid4()
    monkeypatch.setattr(
        documento_payment_service,
        "_load_documento_for_payment",
        lambda session, doc_id: _async_value(SimpleNamespace(id=documento_id)),
    )

    with pytest.raises(DocumentoPaymentValidationError) as exc:
        await register_document_payment(
            FakeSession(),
            documento_id=documento_id,
            actor_id=actor_id,
            actor=SimpleNamespace(id=uuid4()),
        )

    assert exc.value.code == "actor_mismatch"


async def _async_value(value):
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("posting_status", ["created", "pending", "bank_pending"])
async def test_operator_advance_uses_debtor_posting_without_expense_or_budget(
    monkeypatch,
    posting_status,
) -> None:
    operator_id = uuid4()
    actor_id = uuid4()
    documento = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="en_proceso_pago",
        gasto_generado_id=None,
        monto_solicitado=17100,
        fecha_pago=date(2026, 9, 24),
        metodo_pago="TRANSFERENCIA",
        proveedor_cliente_id=operator_id,
        beneficiario_proveedor_cliente_id=operator_id,
        beneficiario_empleado_id=None,
        cuenta_gastos_id=uuid4(),
        empleado=SimpleNamespace(id=uuid4(), nombre="Solicitante"),
        budget_concept_id=None,
    )
    actor = SimpleNamespace(
        id=actor_id,
        rol="finanzas",
        departamento="Finanzas",
        permissions={"contabilidad.pagos.marcar_pagado"},
    )
    monkeypatch.setattr(
        documento_payment_service,
        "_load_documento_for_payment",
        lambda *_: _async_value(documento),
    )
    monkeypatch.setattr(
        documento_payment_service, "parse_amex_payment_card_id", lambda *_: None
    )
    posting = AsyncMock(
        return_value=SimpleNamespace(
            status=posting_status,
            reason=(
                "missing_operator_debtor_account"
                if posting_status == "pending"
                else (
                    "missing_santander_account"
                    if posting_status == "bank_pending"
                    else None
                )
            ),
        )
    )
    provider = AsyncMock(
        side_effect=AssertionError("provider posting is not an advance")
    )
    expense = AsyncMock(
        side_effect=AssertionError("advance must not create an expense")
    )
    monkeypatch.setattr(
        documento_payment_service, "ensure_debtor_payment_posting_for_document", posting
    )
    monkeypatch.setattr(
        documento_payment_service, "ensure_provider_payment_posting", provider
    )
    monkeypatch.setattr(documento_payment_service, "create_expense_from_data", expense)
    session = FakeSession()

    if posting_status != "created":
        with pytest.raises(DocumentoPaymentValidationError) as exc:
            await register_document_payment(
                session,
                documento_id=documento.id,
                actor_id=actor_id,
                actor=actor,
                notify=False,
            )
        if posting_status == "pending":
            assert exc.value.code == "missing_operator_debtor_account"
            assert "cuenta de deudores activa" in exc.value.message
        else:
            assert exc.value.code == "accounting_posting_pending"
            assert "missing_santander_account" in exc.value.message
        assert documento.estado == "en_proceso_pago"
        assert not session.committed
    else:
        result = await register_document_payment(
            session,
            documento_id=documento.id,
            actor_id=actor_id,
            actor=actor,
            notify=False,
        )
        assert result.documento.estado == "pagado"
        assert result.expense is None
        assert session.committed

    assert posting.await_args.kwargs["empleado"] is documento.empleado
    assert posting.await_args.kwargs["require_employee_beneficiary"] is False
    provider.assert_not_awaited()
    expense.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["beneficiary_mismatch", "missing_requester"])
async def test_operator_advance_rejects_invalid_payment_identity(monkeypatch, invalid):
    operator_id, actor_id = uuid4(), uuid4()
    documento = SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="en_proceso_pago",
        gasto_generado_id=None,
        monto_solicitado=17100,
        fecha_pago=date(2026, 9, 24),
        metodo_pago="TRANSFERENCIA",
        proveedor_cliente_id=(
            uuid4() if invalid == "beneficiary_mismatch" else operator_id
        ),
        beneficiario_proveedor_cliente_id=operator_id,
        beneficiario_empleado_id=None,
        cuenta_gastos_id=uuid4(),
        empleado=(
            None if invalid == "missing_requester" else SimpleNamespace(id=uuid4())
        ),
    )
    monkeypatch.setattr(
        documento_payment_service,
        "_load_documento_for_payment",
        lambda *_: _async_value(documento),
    )
    monkeypatch.setattr(
        documento_payment_service, "parse_amex_payment_card_id", lambda *_: None
    )
    posting = AsyncMock(side_effect=AssertionError("invalid identity must not post"))
    monkeypatch.setattr(
        documento_payment_service, "ensure_debtor_payment_posting_for_document", posting
    )
    actor = SimpleNamespace(
        id=actor_id,
        rol="finanzas",
        departamento="Finanzas",
        permissions={"contabilidad.pagos.marcar_pagado"},
    )

    with pytest.raises(DocumentoPaymentValidationError) as exc:
        await register_document_payment(
            FakeSession(),
            documento_id=documento.id,
            actor_id=actor_id,
            actor=actor,
            notify=False,
        )
    assert exc.value.code == (
        "operator_beneficiary_mismatch"
        if invalid == "beneficiary_mismatch"
        else "missing_empleado"
    )
    posting.assert_not_awaited()
