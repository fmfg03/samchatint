from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import samchat.assistant.router as assistant_router


@pytest.mark.asyncio
async def test_assistant_canonical_query_allows_supported_read_action(monkeypatch):
    execute_canonical_action = AsyncMock(
        return_value=SimpleNamespace(
            action="budgets.snapshot",
            status="ok",
            data={"ok": True},
            context=SimpleNamespace(to_dict=lambda: {"scope": "test"}),
        )
    )
    monkeypatch.setattr(
        assistant_router,
        "execute_canonical_action",
        execute_canonical_action,
    )

    result = await assistant_router._run_read_tool(
        "assistant_canonical_query",
        {
            "action": "budgets.snapshot",
            "context": {"scope": "test"},
            "payload": {"tournament_key": "beisbol"},
        },
        gastos_session=AsyncMock(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert result == {
        "action": "budgets.snapshot",
        "status": "ok",
        "data": {"ok": True},
        "context": {"scope": "test"},
    }
    execute_canonical_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_assistant_canonical_query_rejects_non_read_action(monkeypatch):
    execute_canonical_action = AsyncMock(
        return_value=SimpleNamespace(
            action="finance.expense_create",
            status="ok",
            data={},
            context=SimpleNamespace(to_dict=lambda: {}),
        )
    )
    monkeypatch.setattr(
        assistant_router,
        "execute_canonical_action",
        execute_canonical_action,
    )

    with pytest.raises(HTTPException) as exc_info:
        await assistant_router._run_read_tool(
            "assistant_canonical_query",
            {
                "action": "finance.expense_create",
                "context": {},
                "payload": {"amount": 100},
            },
            gastos_session=AsyncMock(),
            tournament_key_default=None,
            current_role="admin",
        )

    assert exc_info.value.status_code == 403
    assert "read-only" in exc_info.value.detail
    execute_canonical_action.assert_not_awaited()
