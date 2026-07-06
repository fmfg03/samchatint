import pytest

import samchat.assistant.router as assistant_router


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
async def test_finance_read_tool_runs_inside_no_autoflush(monkeypatch):
    observed = {}

    async def finance_ops_query(session, **_kwargs):
        observed["no_autoflush_active"] = session.no_autoflush_active
        return {"ok": True}

    monkeypatch.setattr(assistant_router, "finance_ops_query", finance_ops_query)

    result = await assistant_router._run_read_tool(
        "finance_ops_query",
        {},
        gastos_session=_FakeSession(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert result == {"ok": True}
    assert observed["no_autoflush_active"] is True
