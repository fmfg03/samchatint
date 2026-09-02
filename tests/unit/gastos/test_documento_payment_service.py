from datetime import date
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

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, value) -> None:
        self.refreshed.append(value)


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

    async def fake_amex_posting(*args, **kwargs):
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
    )

    assert result.documento.estado == "pagado"
    assert result.aprobacion.aprobador_id == actor_id
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
