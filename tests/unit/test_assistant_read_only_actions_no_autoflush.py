import pytest

import samchat.assistant.action_router as action_router
from samchat.assistant.context import AssistantContext


class _NoAutoflush:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self.session.no_autoflush_active = True

    def __exit__(self, exc_type, exc, tb):
        self.session.no_autoflush_active = False


class _FakeSession:
    def __init__(self):
        self.no_autoflush_active = False

    @property
    def no_autoflush(self):
        return _NoAutoflush(self)


@pytest.mark.asyncio
async def test_supported_read_action_runs_inside_no_autoflush(monkeypatch):
    observed = {}

    async def read_adapter(session, *, context, payload):
        observed["no_autoflush_active"] = session.no_autoflush_active
        return action_router.AdapterResult(
            action="budgets.snapshot",
            status="completed",
            data={"ok": True},
            context=context,
        )

    monkeypatch.setitem(action_router._ROUTES, "budgets.snapshot", read_adapter)

    result = await action_router.execute_canonical_action(
        "budgets.snapshot",
        session=_FakeSession(),
        context=AssistantContext(),
        payload={},
    )

    assert result.status == "completed"
    assert observed["no_autoflush_active"] is True


@pytest.mark.asyncio
async def test_write_action_does_not_force_no_autoflush(monkeypatch):
    observed = {}

    async def write_adapter(session, *, context, payload):
        observed["no_autoflush_active"] = session.no_autoflush_active
        return action_router.AdapterResult(
            action="expenses.create_manual_expense",
            status="completed",
            data={"ok": True},
            context=context,
        )

    monkeypatch.setitem(
        action_router._ROUTES,
        "expenses.create_manual_expense",
        write_adapter,
    )

    result = await action_router.execute_canonical_action(
        "expenses.create_manual_expense",
        session=_FakeSession(),
        context=AssistantContext(),
        payload={},
    )

    assert result.status == "completed"
    assert observed["no_autoflush_active"] is False
