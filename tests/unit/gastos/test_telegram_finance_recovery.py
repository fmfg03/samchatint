"""Regression tests for stale finance notification recovery."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import telegram_outbox_service as outbox


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


@pytest.mark.asyncio
async def test_interrupted_pending_is_recovered_in_place(monkeypatch):
    entry_id = uuid4()
    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result([entry_id])),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    sender = AsyncMock(return_value="sent")
    monkeypatch.setattr(outbox, "_send_finance_outbox_entry", sender, raising=False)
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    stats = await outbox.recover_stale_finance_pending_notifications(session, now=now)
    assert stats == dict(reviewed=1, recovered=1, skipped=0, busy=0, failed=0)
    sender.assert_awaited_once_with(
        session,
        entry_id,
        mode="recovery",
        stale_before=now - timedelta(minutes=5),
        retry_due_before=now,
    )


def entry(**changes):
    values = dict(
        id=uuid4(),
        documento_id=uuid4(),
        status="pending",
        notification_type="finance_pending_payment",
        created_at=datetime(2026, 10, 1),
        updated_at=None,
        retry_count=0,
        next_retry_at=None,
        telegram_chat_id=101,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def sender_session(monkeypatch, row):
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=lambda stmt: Result(
                row.documento_id if "FROM documentos" in str(stmt) else None
            )
        ),
    )
    monkeypatch.setattr(outbox, "_lock_finance_entry", AsyncMock(return_value=row))
    monkeypatch.setattr(outbox, "_finance_skip_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(
        outbox, "rebuild_outbox_message_text", AsyncMock(return_value="body")
    )
    monkeypatch.setattr(outbox, "send_telegram_message", AsyncMock(return_value=True))
    monkeypatch.setattr(outbox, "schedule_outbox_retry", lambda _id: None)
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["sent", "skipped", "failed"])
async def test_pending_sender_preserves_nonpending(monkeypatch, status):
    row = entry(status=status)
    session = sender_session(monkeypatch, row)
    assert await outbox._send_finance_outbox_entry(session, row.id) == (
        "already_sent" if status == "sent" else "skipped"
    )
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_locked_row_is_not_sent(monkeypatch):
    session = sender_session(monkeypatch, None)
    assert await outbox._send_finance_outbox_entry(session, uuid4()) == "busy"
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_age_is_rechecked_under_lock(monkeypatch):
    cutoff = datetime(2026, 10, 2, tzinfo=timezone.utc)
    row = entry(updated_at=cutoff)
    session = sender_session(monkeypatch, row)
    assert (
        await outbox._send_finance_outbox_entry(session, row.id, stale_before=cutoff)
        == "skipped"
    )
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_created_at_and_success_same_row(monkeypatch):
    row = entry()
    session = sender_session(monkeypatch, row)
    assert (
        await outbox._send_finance_outbox_entry(
            session, row.id, stale_before=datetime(2026, 10, 2, tzinfo=timezone.utc)
        )
        == "sent"
    )
    assert row.status == "sent"
    assert row.sent_at is not None
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_ineligible_is_skipped_with_reason(monkeypatch):
    row = entry()
    session = sender_session(monkeypatch, row)
    outbox._finance_skip_reason.return_value = "No longer active"
    assert await outbox._send_finance_outbox_entry(session, row.id) == "skipped"
    assert row.error_message == "No longer active"
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "rebuild", "empty"])
async def test_failure_persists_and_schedules_one_retry(monkeypatch, failure):
    row = entry()
    session = sender_session(monkeypatch, row)
    schedule = []
    monkeypatch.setattr(outbox, "schedule_outbox_retry", schedule.append)
    if failure == "http":
        outbox.send_telegram_message.return_value = False
    elif failure == "empty":
        outbox.rebuild_outbox_message_text.return_value = None
    else:
        outbox.rebuild_outbox_message_text.side_effect = RuntimeError("failure")
    assert await outbox._send_finance_outbox_entry(session, row.id) == "failed"
    assert row.status == "failed"
    assert row.next_retry_at is not None
    assert schedule == [row.id]


@pytest.mark.asyncio
async def test_retry_is_bounded(monkeypatch):
    row = entry(status="failed")
    session = sender_session(monkeypatch, row)
    outbox.send_telegram_message.return_value = False
    assert (
        await outbox._send_finance_outbox_entry(session, row.id, mode="retry")
        == "failed"
    )
    assert row.retry_count == 1
    assert row.next_retry_at is None
    assert (
        await outbox._send_finance_outbox_entry(session, row.id, mode="retry")
        == "skipped"
    )
    assert outbox.send_telegram_message.await_count == 1


@pytest.mark.asyncio
async def test_recovery_isolates_row_error_and_bounds_batch(monkeypatch):
    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result([uuid4(), uuid4()])),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        outbox,
        "_send_finance_outbox_entry",
        AsyncMock(side_effect=[RuntimeError("database failure"), "sent"]),
    )
    stats = await outbox.recover_stale_finance_pending_notifications(
        session, limit=9999
    )
    assert stats["failed"] == stats["recovered"] == 1
    assert session.execute.call_args.args[0]._limit_clause.value == 500
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_preserves_workflow_metrics(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).parents[3] / "scripts/monitor_telegram_workflow_notifications.py"
    )
    spec = importlib.util.spec_from_file_location("finance_monitor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from unittest.mock import MagicMock

    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value="session")
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        module, "get_notification_session_maker", lambda: lambda: context
    )
    workflow = AsyncMock(return_value={"workflow_sent": 2})
    finance = AsyncMock(return_value={"recovered": 1})
    monkeypatch.setattr(module, "monitor_workflow_telegram_notifications", workflow)
    monkeypatch.setattr(module, "recover_stale_finance_pending_notifications", finance)
    assert await module._run(
        SimpleNamespace(env_file="", older_than_minutes=5, limit=10)
    ) == {"workflow_sent": 2, "finance_recovered": 1}
    workflow.assert_awaited_once_with("session", older_than_minutes=5, limit=10)
    finance.assert_awaited_once_with("session", older_than_minutes=5, limit=10)


@pytest.mark.asyncio
async def test_shared_chat_duplicate_is_audited(monkeypatch):
    row = entry()
    session = sender_session(monkeypatch, row)
    session.execute.side_effect = [Result(row.documento_id), Result(uuid4())]
    assert await outbox._send_finance_outbox_entry(session, row.id) == "skipped"
    assert "mismo chat" in row.error_message
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_lock_contention_does_not_consume_retry(monkeypatch):
    row = entry(status="failed")
    session = sender_session(monkeypatch, row)
    session.execute.side_effect = [Result(None), Result(row.documento_id)]
    assert (
        await outbox._send_finance_outbox_entry(session, row.id, mode="retry") == "busy"
    )
    assert row.retry_count == 0
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_exception_records_failed(monkeypatch):
    row = entry()
    session = sender_session(monkeypatch, row)
    outbox._finance_skip_reason.side_effect = RuntimeError("guard failure")
    assert await outbox._send_finance_outbox_entry(session, row.id) == "failed"
    assert row.status == "failed"
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["busy", "skipped", "failed", "already_sent"])
async def test_recovery_accounts_for_outcomes(monkeypatch, outcome):
    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result([uuid4()])),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(
        outbox, "_send_finance_outbox_entry", AsyncMock(return_value=outcome)
    )
    stats = await outbox.recover_stale_finance_pending_notifications(session)
    assert stats["skipped" if outcome == "already_sent" else outcome] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document,proof,recipient,reason",
    [
        (None, None, None, "Solicitud ya no"),
        (
            SimpleNamespace(
                tipo="SOLICITUD",
                estado="aprobado",
                fecha_pago_efectiva=None,
                pagado_en=None,
            ),
            uuid4(),
            None,
            "evidencia de pago",
        ),
        (
            SimpleNamespace(
                tipo="SOLICITUD",
                estado="aprobado",
                fecha_pago_efectiva=None,
                pagado_en=None,
            ),
            None,
            None,
            "Finanzas activo",
        ),
        (
            SimpleNamespace(
                tipo="SOLICITUD",
                estado="aprobado",
                fecha_pago_efectiva=None,
                pagado_en=None,
            ),
            None,
            SimpleNamespace(activo=True, rol="finanzas", telegram_user_id=None),
            "Sin telegram",
        ),
        (
            SimpleNamespace(
                tipo="SOLICITUD",
                estado="aprobado",
                fecha_pago_efectiva=None,
                pagado_en=None,
            ),
            None,
            SimpleNamespace(activo=True, rol="finanzas", telegram_user_id=202),
            None,
        ),
    ],
)
async def test_current_eligibility_and_chat(document, proof, recipient, reason):
    class GuardResult(Result):
        def one_or_none(self):
            return self.value

    row = entry(recipient_empleado_id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                GuardResult(document),
                GuardResult(proof),
                GuardResult(recipient),
            ]
        )
    )
    result = await outbox._finance_skip_reason(session, row)
    if reason is None:
        assert result is None
        assert row.telegram_chat_id == 202
    else:
        assert reason in result


@pytest.mark.asyncio
async def test_initial_finance_send_rebuilds_instead_of_supplied_stale_text(
    monkeypatch,
):
    row = entry()
    session = sender_session(monkeypatch, row)
    outbox.rebuild_outbox_message_text.return_value = "current body"
    assert (
        await outbox._send_finance_outbox_entry(
            session, row.id, mode="delivery", text="stale body"
        )
        == "sent"
    )
    outbox.send_telegram_message.assert_awaited_once_with(
        101, "current body", reply_markup=None
    )


@pytest.mark.asyncio
async def test_document_refresh_is_explicit_for_finance():
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(None)))
    await outbox._load_documento_for_outbox(session, uuid4(), refresh=True)
    stmt = session.execute.call_args.args[0]
    assert stmt.get_execution_options()["populate_existing"] is True
    assert len(stmt._with_options) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,scheduled_count", [("busy", 1), ("failed", 0), ("sent", 0)]
)
async def test_deferred_busy_retry_resumes_without_new_attempt(
    monkeypatch, outcome, scheduled_count
):
    from unittest.mock import MagicMock

    from devnous.gastos.services import documento_telegram

    row = entry(status="failed")
    session = SimpleNamespace(get=AsyncMock(return_value=row))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        documento_telegram, "get_notification_session_maker", lambda: lambda: context
    )
    monkeypatch.setattr(outbox.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        outbox, "_send_finance_outbox_entry", AsyncMock(return_value=outcome)
    )
    scheduled = []
    monkeypatch.setattr(outbox, "schedule_outbox_retry", scheduled.append)
    await outbox._execute_outbox_retry(row.id)
    assert len(scheduled) == scheduled_count
    assert row.retry_count == 0
    outbox.asyncio.sleep.assert_awaited_once_with(outbox.OUTBOX_RETRY_DELAY_SECONDS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seconds,attempts,expected", [(0, 0, "sent"), (1, 0, "skipped"), (0, 1, "skipped")]
)
async def test_durable_retry_rechecks_due_time_and_attempt_limit(
    monkeypatch, seconds, attempts, expected
):
    now = datetime.now(timezone.utc)
    row = entry(
        status="failed",
        retry_count=attempts,
        next_retry_at=now + timedelta(seconds=seconds),
    )
    session = sender_session(monkeypatch, row)
    outcome = await outbox._send_finance_outbox_entry(
        session,
        row.id,
        mode="recovery",
        retry_due_before=now,
        stale_before=now - timedelta(minutes=5),
    )
    assert outcome == expected
    assert outbox.send_telegram_message.await_count == int(expected == "sent")


@pytest.mark.asyncio
async def test_older_sleeping_retry_cannot_ignore_rescheduled_due_time(monkeypatch):
    row = entry(
        status="failed", next_retry_at=datetime.now(timezone.utc) + timedelta(hours=2)
    )
    session = sender_session(monkeypatch, row)
    assert (
        await outbox._send_finance_outbox_entry(session, row.id, mode="retry")
        == "skipped"
    )
    assert row.retry_count == 0
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["sent", "failed"])
async def test_legacy_outbox_marking_preserves_timestamp_compatibility(
    monkeypatch, status
):
    row = entry()
    del row.notification_type
    session = SimpleNamespace(flush=AsyncMock())
    scheduled = []
    monkeypatch.setattr(outbox, "schedule_outbox_retry", scheduled.append)
    if status == "sent":
        await outbox.mark_outbox_entry(session, row, status=status)
        assert row.sent_at.tzinfo is None
        assert row.next_retry_at is None
    else:
        await outbox._mark_outbox_failed(session, row, "delivery failed")
        assert row.next_retry_at.tzinfo is None
        assert scheduled == [row.id]
    assert row.status == status
    assert row.updated_at.tzinfo is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["busy", "already_sent", "skipped"])
async def test_console_finance_nonsend_is_not_reported_failed(monkeypatch, outcome):
    row = entry(error_message=None)
    session = SimpleNamespace(execute=AsyncMock(return_value=Result([row])))
    monkeypatch.setattr(
        outbox, "_send_finance_outbox_entry", AsyncMock(return_value=outcome)
    )
    stats = await outbox.flush_pending_outbox_notifications(session)
    assert stats["failed"] == 0
    assert stats["attempted"] == 0
    assert stats[outcome] == 1


@pytest.mark.asyncio
async def test_explicit_finance_force_resend_really_sends_and_resets_retry(monkeypatch):
    row = entry(status="sent", retry_count=1, sent_at=object(), next_retry_at=object())
    session = sender_session(monkeypatch, row)
    monkeypatch.setattr(outbox, "find_outbox_entry", AsyncMock(return_value=row))
    ok = await outbox.deliver_telegram_notification(
        session,
        notification_type="finance_pending_payment",
        header_text="header",
        text="old body",
        chat_id=101,
        documento_id=row.documento_id,
        recipient_empleado_id=uuid4(),
        force_resend=True,
    )
    assert ok is True
    outbox.send_telegram_message.assert_awaited_once()
    assert row.retry_count == 0
    assert isinstance(row.sent_at, datetime)
    assert row.next_retry_at is None


@pytest.mark.asyncio
async def test_forced_finance_still_revalidates_eligibility(monkeypatch):
    row = entry(status="sent", retry_count=1, sent_at=object())
    session = sender_session(monkeypatch, row)
    old_receipt = row.sent_at
    outbox._finance_skip_reason.return_value = "Solicitud ya pagada"
    assert (
        await outbox._send_finance_outbox_entry(
            session, row.id, mode="delivery", force_resend=True
        )
        == "skipped"
    )
    outbox.send_telegram_message.assert_not_awaited()
    assert row.status == "sent"
    assert row.error_message == "Solicitud ya pagada"
    assert row.retry_count == 1
    assert row.sent_at is old_receipt


@pytest.mark.asyncio
async def test_force_cannot_enable_automatic_resend(monkeypatch):
    row = entry(status="sent")
    session = sender_session(monkeypatch, row)
    assert (
        await outbox._send_finance_outbox_entry(session, row.id, force_resend=True)
        == "already_sent"
    )
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_force_bypasses_shared_chat_dedup(monkeypatch):
    row = entry(status="sent")
    session = sender_session(monkeypatch, row)
    session.execute.side_effect = [Result(row.documento_id)]
    assert (
        await outbox._send_finance_outbox_entry(
            session, row.id, mode="delivery", force_resend=True
        )
        == "sent"
    )
    assert session.execute.await_count == 1
    outbox.send_telegram_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_guard_exception_cannot_reset_sent_receipt(monkeypatch):
    old_receipt = datetime.now(timezone.utc)
    row = entry(status="sent", retry_count=1, sent_at=old_receipt)
    session = sender_session(monkeypatch, row)
    outbox._finance_skip_reason.side_effect = RuntimeError("guard query failed")
    assert (
        await outbox._send_finance_outbox_entry(
            session, row.id, mode="delivery", force_resend=True
        )
        == "failed"
    )
    assert row.status == "sent"
    assert row.sent_at is old_receipt
    assert row.retry_count == 1
    outbox.send_telegram_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_exception_preserves_competing_new_sent_receipt(monkeypatch):
    original = datetime.now(timezone.utc)
    new_receipt = original + timedelta(seconds=1)
    row = entry(status="sent", sent_at=original, updated_at=original, retry_count=1)
    session = sender_session(monkeypatch, row)
    scheduled = []
    monkeypatch.setattr(outbox, "schedule_outbox_retry", scheduled.append)

    async def competing_delivery_during_rollback():
        row.status = "sent"
        row.sent_at = new_receipt
        row.updated_at = new_receipt
        row.retry_count = 0

    session.rollback.side_effect = competing_delivery_during_rollback
    outbox.rebuild_outbox_message_text.side_effect = RuntimeError("rebuild failed")
    assert (
        await outbox._send_finance_outbox_entry(
            session, row.id, mode="delivery", force_resend=True
        )
        == "already_sent"
    )
    assert row.status == "sent"
    assert row.sent_at == row.updated_at == new_receipt
    assert row.retry_count == 0
    assert scheduled == []
    outbox.send_telegram_message.assert_not_awaited()
