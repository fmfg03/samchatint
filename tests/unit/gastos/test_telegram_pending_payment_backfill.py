from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_telegram


class _RecipientsResult:
    def __init__(self, recipients: list[Any]) -> None:
        self._recipients = recipients

    def scalars(self) -> "_RecipientsResult":
        return self

    def all(self) -> list[Any]:
        return self._recipients


class _RecipientSession:
    def __init__(self, recipients: list[Any]) -> None:
        self.execute = AsyncMock(return_value=_RecipientsResult(recipients))


class _SingleResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


def _approved_solicitud() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tipo="SOLICITUD",
        estado="aprobado",
        pago_urgente=False,
    )


@pytest.mark.asyncio
async def test_pending_payment_skips_message_build_when_outbox_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento = _approved_solicitud()
    sent_recipient = SimpleNamespace(id=uuid4(), telegram_user_id=101)
    skipped_recipient = SimpleNamespace(id=uuid4(), telegram_user_id=None)
    session = _RecipientSession([sent_recipient, skipped_recipient])
    build_context = AsyncMock()
    deliver = AsyncMock()

    async def fake_find(_session: Any, **kwargs: Any) -> Any:
        if kwargs["recipient_empleado_id"] == sent_recipient.id:
            return SimpleNamespace(status="sent")
        return SimpleNamespace(status="skipped")

    monkeypatch.setattr(documento_telegram, "find_outbox_entry", fake_find)
    monkeypatch.setattr(
        documento_telegram, "build_documento_telegram_context", build_context
    )
    monkeypatch.setattr(
        documento_telegram, "deliver_telegram_notification", deliver
    )

    sent = await documento_telegram.notify_finance_pending_payment_on_solicitud_approve(
        session, documento
    )

    assert sent == 0
    build_context.assert_not_awaited()
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_payment_retries_skipped_entry_after_telegram_linking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento = _approved_solicitud()
    recipient = SimpleNamespace(id=uuid4(), telegram_user_id=101)
    session = _RecipientSession([recipient])
    delivered = AsyncMock(return_value=True)

    monkeypatch.setattr(
        documento_telegram,
        "find_outbox_entry",
        AsyncMock(return_value=SimpleNamespace(status="skipped")),
    )
    monkeypatch.setattr(
        documento_telegram,
        "build_documento_telegram_context",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        documento_telegram,
        "format_documento_resumen_es",
        lambda *_args, **_kwargs: "Solicitud",
    )
    monkeypatch.setattr(
        documento_telegram, "deliver_telegram_notification", delivered
    )

    sent = await documento_telegram.notify_finance_pending_payment_on_solicitud_approve(
        session, documento
    )

    assert sent == 1
    assert delivered.await_args.kwargs["chat_id"] == 101
    assert delivered.await_args.kwargs["recipient_empleado_id"] == recipient.id


@pytest.mark.asyncio
async def test_pending_payment_reserves_shared_chat_id_when_first_entry_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento = _approved_solicitud()
    sent_recipient = SimpleNamespace(id=uuid4(), telegram_user_id=101)
    second_recipient = SimpleNamespace(id=uuid4(), telegram_user_id=101)
    session = _RecipientSession([sent_recipient, second_recipient])
    build_context = AsyncMock()
    deliver = AsyncMock()

    async def fake_find(_session: Any, **kwargs: Any) -> Any:
        if kwargs["recipient_empleado_id"] == sent_recipient.id:
            return SimpleNamespace(status="sent")
        return None

    monkeypatch.setattr(documento_telegram, "find_outbox_entry", fake_find)
    monkeypatch.setattr(
        documento_telegram, "build_documento_telegram_context", build_context
    )
    monkeypatch.setattr(
        documento_telegram, "deliver_telegram_notification", deliver
    )

    sent = await documento_telegram.notify_finance_pending_payment_on_solicitud_approve(
        session, documento
    )

    assert sent == 0
    build_context.assert_not_awaited()
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_payment_preserves_sent_entry_after_telegram_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento = _approved_solicitud()
    recipient = SimpleNamespace(id=uuid4(), telegram_user_id=None)
    session = _RecipientSession([recipient])
    build_context = AsyncMock()
    deliver = AsyncMock()

    monkeypatch.setattr(
        documento_telegram,
        "find_outbox_entry",
        AsyncMock(return_value=SimpleNamespace(status="sent")),
    )
    monkeypatch.setattr(
        documento_telegram, "build_documento_telegram_context", build_context
    )
    monkeypatch.setattr(
        documento_telegram, "deliver_telegram_notification", deliver
    )

    sent = await documento_telegram.notify_finance_pending_payment_on_solicitud_approve(
        session, documento
    )

    assert sent == 0
    build_context.assert_not_awaited()
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_reloads_document_with_canonical_telegram_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_documento = SimpleNamespace(id=uuid4())
    loaded_documento = _approved_solicitud()
    session = object()
    load = AsyncMock(return_value=loaded_documento)
    notify = AsyncMock(return_value=2)
    monkeypatch.setattr(documento_telegram, "load_documento_for_telegram", load)
    monkeypatch.setattr(
        documento_telegram,
        "notify_finance_pending_payment_on_solicitud_approve",
        notify,
    )

    sent = await documento_telegram.ensure_finance_pending_payment_notifications(
        session, stale_documento
    )

    assert sent == 2
    load.assert_awaited_once_with(session, stale_documento.id)
    notify.assert_awaited_once_with(session, loaded_documento)


@pytest.mark.asyncio
async def test_backfill_stops_when_canonical_telegram_document_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_documento = SimpleNamespace(id=uuid4())
    session = object()
    load = AsyncMock(return_value=None)
    notify = AsyncMock()
    monkeypatch.setattr(documento_telegram, "load_documento_for_telegram", load)
    monkeypatch.setattr(
        documento_telegram,
        "notify_finance_pending_payment_on_solicitud_approve",
        notify,
    )

    sent = await documento_telegram.ensure_finance_pending_payment_notifications(
        session, stale_documento
    )

    assert sent == 0
    load.assert_awaited_once_with(session, stale_documento.id)
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_detail_denies_before_payment_or_telegram_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento_id = uuid4()
    documento = SimpleNamespace(empleado_id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_SingleResult(documento)),
        commit=AsyncMock(),
    )
    ensure_pending = AsyncMock()
    monkeypatch.setattr(
        user_routes,
        "ensure_finance_pending_payment_notifications",
        ensure_pending,
    )
    monkeypatch.setattr(
        user_routes,
        "_render_documento_access_denied_page",
        lambda _empleado: "denied",
    )

    result = await user_routes.ver_documento(
        documento_id=documento_id,
        request=SimpleNamespace(query_params={}, headers={}),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4(), rol="empleado"),
    )

    assert result == "denied"
    session.commit.assert_not_awaited()
    ensure_pending.assert_not_awaited()
