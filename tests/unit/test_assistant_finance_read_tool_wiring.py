from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest

from samchat.assistant import router


def _tool_names(tool_defs: list[dict]) -> set[str]:
    return {
        str((tool_def.get("function") or {}).get("name") or "")
        for tool_def in tool_defs
    }


def _assistant_finance_read_function() -> dict:
    tool_defs = router._tool_defs()
    for tool_def in tool_defs:
        function = tool_def.get("function") or {}
        if function.get("name") == "assistant_finance_read":
            return function
    raise AssertionError("assistant_finance_read tool definition not found")


def _assistant_finance_read_schema() -> dict:
    return _assistant_finance_read_function()["parameters"]


def test_assistant_finance_read_is_registered_as_finance_read_only_tool() -> None:
    registry = router._assistant_tool_registry()
    spec = registry["assistant_finance_read"]

    assert "assistant_finance_read" in router.READ_TOOLS
    assert "assistant_finance_read" in router.FINANCE_READ_TOOLS
    assert "assistant_finance_read" not in router.WRITE_TOOLS
    assert "assistant_finance_read" not in router.FINANCE_WRITE_TOOLS
    assert spec.operation_type == "read"
    assert spec.surface == "finance"
    assert spec.requires_confirmation is False


def test_assistant_finance_read_schema_is_bounded_to_approved_intents() -> None:
    schema = _assistant_finance_read_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["intent"]
    assert schema["properties"]["intent"]["enum"] == [
        "ar.summary",
        "ar.prematching",
        "cashflow.summary",
        "cashflow.statement",
        "budget.snapshot",
        "budget.vs_actual",
        "finance.platform",
        "finance.exports",
    ]
    assert schema["properties"]["month"]["minimum"] == 1
    assert schema["properties"]["month"]["maximum"] == 12
    assert schema["properties"]["horizon_months"]["maximum"] == 24
    assert schema["properties"]["limit"]["maximum"] == 500


def test_finance_messages_expose_assistant_finance_read_tool() -> None:
    route = router._assistant_classify_request(
        "Dame el cashflow planning y la CxC AR de la copa"
    )
    route["domain"] = "finance"
    tools = router._assistant_tool_defs_for_message(route, "cashflow planning CxC AR")

    assert "assistant_finance_read" in _tool_names(tools)


@pytest.mark.asyncio
async def test_run_read_tool_executes_assistant_finance_read_adapter() -> None:
    adapter = AsyncMock(
        return_value={
            "ok": True,
            "read_only": True,
            "intent": "cashflow.summary",
            "payload": {"summary": {"forecast_net": 100}},
        }
    )
    session = object()

    with patch("samchat.assistant.router.run_finance_read_adapter", new=adapter):
        result = await router._run_read_tool(
            "assistant_finance_read",
            {
                "intent": "cashflow.summary",
                "budget_version_id": "version-1",
                "year": 2026,
                "month": 1,
                "horizon_months": 3,
                "limit": 50,
            },
            gastos_session=session,
            tournament_key_default=None,
            current_role="finanzas",
        )

    adapter.assert_awaited_once_with(
        session,
        intent="cashflow.summary",
        budget_version_id="version-1",
        year=2026,
        month=1,
        horizon_months=3,
        limit=50,
    )
    assert result["read_only"] is True
    assert result["intent"] == "cashflow.summary"


@pytest.mark.asyncio
async def test_run_read_tool_executes_budget_snapshot_as_read_only_intent() -> None:
    adapter = AsyncMock(
        return_value={
            "ok": True,
            "read_only": True,
            "intent": "budget.snapshot",
            "payload": {"summary": {"budget_total": 100}},
        }
    )
    session = object()

    with patch("samchat.assistant.router.run_finance_read_adapter", new=adapter):
        result = await router._run_read_tool(
            "assistant_finance_read",
            {
                "intent": "budget.snapshot",
                "budget_version_id": "version-1",
                "tournament_id": "tournament-1",
                "tournament_code": "COPA",
                "year": 2026,
            },
            gastos_session=session,
            tournament_key_default=None,
            current_role="finanzas",
        )

    adapter.assert_awaited_once_with(
        session,
        intent="budget.snapshot",
        budget_version_id="version-1",
        tournament_id="tournament-1",
        tournament_code="COPA",
        year=2026,
    )
    assert result["read_only"] is True
    assert result["intent"] == "budget.snapshot"


@pytest.mark.asyncio
async def test_run_read_tool_executes_finance_platform_as_read_only_intent() -> None:
    adapter = AsyncMock(
        return_value={
            "ok": True,
            "read_only": True,
            "intent": "finance.platform",
            "payload": {"summary": {"open_actions": 3}},
        }
    )
    session = object()

    with patch("samchat.assistant.router.run_finance_read_adapter", new=adapter):
        result = await router._run_read_tool(
            "assistant_finance_read",
            {
                "intent": "finance.platform",
                "year": 2026,
                "month": 4,
                "limit": 30,
            },
            gastos_session=session,
            tournament_key_default=None,
            current_role="finanzas",
        )

    adapter.assert_awaited_once_with(
        session,
        intent="finance.platform",
        year=2026,
        month=4,
        limit=30,
    )
    assert result["read_only"] is True
    assert result["intent"] == "finance.platform"


@pytest.mark.asyncio
async def test_run_read_tool_executes_finance_exports_as_read_only_intent() -> None:
    adapter = AsyncMock(
        return_value={
            "ok": True,
            "read_only": True,
            "intent": "finance.exports",
            "payload": {"exports": []},
        }
    )
    session = object()

    with patch("samchat.assistant.router.run_finance_read_adapter", new=adapter):
        result = await router._run_read_tool(
            "assistant_finance_read",
            {"intent": "finance.exports"},
            gastos_session=session,
            tournament_key_default=None,
            current_role="finanzas",
        )

    adapter.assert_awaited_once_with(session, intent="finance.exports")
    assert result["read_only"] is True
    assert result["intent"] == "finance.exports"


def test_assistant_finance_read_does_not_use_write_or_legacy_surfaces() -> None:
    source = json.dumps(_assistant_finance_read_function(), ensure_ascii=False)
    read_tool_source = inspect.getsource(router._run_read_tool)
    branch_start = read_tool_source.index('if tool_name == "assistant_finance_read":')
    branch_end = read_tool_source.index('if tool_name == "finance_strategy_snapshot":')
    source = source + "\n" + read_tool_source[branch_start:branch_end]

    assert "/admin/contabilidad/cash-flow" not in source
    assert "db_read_universal" not in source
    assert "db_write_universal" not in source
    assert "pending_confirmation" not in source


def test_assistant_scoped_gate_includes_finance_read_tool_wiring_test() -> None:
    with open(
        ".github/workflows/assistant-scoped-gate.yml",
        encoding="utf-8",
    ) as workflow:
        content = workflow.read()

    test_path = "tests/unit/test_assistant_finance_read_tool_wiring.py"
    assert content.count(test_path) == 2
