from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_reconciliation_notification_service as svc


class _ScalarResult:
    def __init__(self, values=None):
        self.values = values or []

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_notify_amex_reconciliation_validated_routes_benjamin_and_awareness(
    monkeypatch,
):
    expenses = [
        SimpleNamespace(gasto_cantidad=100, cfdi_report_id=uuid4()),
        SimpleNamespace(gasto_cantidad=25.50, cfdi_report_id=None),
    ]
    benjamin = SimpleNamespace(
        id=uuid4(),
        nombre="Benjamín Jiménez",
        correo="benjamin@example.com",
        departamento="AyF",
        telegram_user_id=111,
    )
    lao = SimpleNamespace(
        id=uuid4(),
        nombre="Luis Angel Ortiz",
        correo="lao@example.com",
        departamento="Dirección",
        telegram_user_id=222,
    )
    fgv = SimpleNamespace(
        id=uuid4(),
        nombre="Federico Gonzalez V",
        correo="fgv@example.com",
        departamento="DG",
        telegram_user_id=None,
    )
    other = SimpleNamespace(
        id=uuid4(),
        nombre="Alicia",
        correo="alicia@example.com",
        departamento="Finanzas",
        telegram_user_id=333,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(expenses),
                _ScalarResult([benjamin, lao, fgv, other]),
            ]
        )
    )
    actor = SimpleNamespace(id=uuid4(), nombre="Finanzas", rol="finanzas")
    card = SimpleNamespace(
        card_label="AMEX FGV 45007",
        cardholder_key="FGV",
        last4="5007",
    )
    calls = []

    async def fake_deliver(session_arg, **kwargs):
        calls.append(kwargs)
        return kwargs["chat_id"] is not None

    monkeypatch.setattr(svc, "deliver_telegram_notification", fake_deliver)

    result = await svc.notify_amex_reconciliation_validated(
        session,
        year=2026,
        month=8,
        actor=actor,
        card_account=card,
    )

    assert result.summary.charge_count == 2
    assert result.summary.linked_charge_count == 1
    assert result.summary.pending_charge_count == 1
    assert result.authorization_recipients == [benjamin]
    assert {emp.id for emp in result.awareness_recipients} == {fgv.id, lao.id}
    assert result.delivered == 2
    assert result.skipped == 1
    assert calls[0]["recipient_empleado_id"] == benjamin.id
    assert {call["recipient_empleado_id"] for call in calls[1:]} == {fgv.id, lao.id}
    assert (
        calls[0]["notification_type"]
        == "amex_reconciliation_validation_authorization_2026_08_5007"
    )
    assert (
        calls[1]["notification_type"]
        == "amex_reconciliation_validation_awareness_2026_08_5007"
    )
    assert "autorización" in calls[0]["header_text"].lower()
    assert "2026-08" in calls[0]["text"]
    assert "AMEX FGV 45007" in calls[0]["text"]
    assert "****5007" in calls[0]["text"]


@pytest.mark.asyncio
async def test_notify_amex_reconciliation_validated_requires_finance_role():
    session = SimpleNamespace(execute=AsyncMock())
    actor = SimpleNamespace(id=uuid4(), nombre="Usuario", rol="empleado")

    with pytest.raises(svc.AmexReconciliationNotificationError):
        await svc.notify_amex_reconciliation_validated(
            session,
            year=2026,
            month=8,
            actor=actor,
        )

    session.execute.assert_not_called()


def test_amex_reconciliation_notification_type_is_card_scoped():
    assert (
        svc.amex_reconciliation_notification_type(
            year=2026,
            month=8,
            kind="authorization",
            card_last4="5007",
        )
        == "amex_reconciliation_validation_authorization_2026_08_5007"
    )
    assert (
        svc.amex_reconciliation_notification_type(
            year=2026,
            month=8,
            kind="authorization",
            card_last4="1234",
        )
        == "amex_reconciliation_validation_authorization_2026_08_1234"
    )
    assert (
        svc.amex_reconciliation_notification_type(
            year=2026,
            month=8,
            kind="authorization",
        )
        == "amex_reconciliation_validation_authorization_2026_08"
    )
