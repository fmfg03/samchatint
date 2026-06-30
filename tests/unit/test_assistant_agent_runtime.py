from __future__ import annotations

from samchat.assistant.agent_runtime import (
    build_agent_runtime_trace,
    evaluate_runtime_tool_call,
    is_agent_runtime_enabled,
)
from samchat.assistant.tool_registry import build_tool_registry


def _registry():
    tool_defs = [
        {"type": "function", "function": {"name": "finance_ops_query"}},
        {"type": "function", "function": {"name": "finance_expense_create"}},
    ]
    return build_tool_registry(
        tool_defs=tool_defs,
        read_tools={"finance_ops_query"},
        write_tools={"finance_expense_create"},
        finance_tools={"finance_ops_query", "finance_expense_create"},
        tournament_tools=set(),
        dev_tools=set(),
    )


def test_agent_runtime_flag_defaults_disabled(monkeypatch) -> None:
    monkeypatch.delenv("ASSISTANT_AGENT_RUNTIME_ENABLED", raising=False)

    assert is_agent_runtime_enabled() is False


def test_agent_runtime_flag_accepts_true_values(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_ENABLED", "on")

    assert is_agent_runtime_enabled() is True


def test_build_agent_runtime_trace_records_available_registered_tools() -> None:
    registry = _registry()
    trace = build_agent_runtime_trace(
        route_info={"route": "agentic_write", "domain": "finance"},
        tool_defs=[
            {"type": "function", "function": {"name": "finance_ops_query"}},
            {"type": "function", "function": {"name": "unknown_tool"}},
            {"type": "function", "function": {"name": "finance_expense_create"}},
        ],
        registry=registry,
    )

    payload = trace["assistant_agent_runtime"]
    assert payload["route"] == "agentic_write"
    assert payload["surface"] == "finance"
    assert payload["available_tools"] == [
        "finance_expense_create",
        "finance_ops_query",
    ]


def test_evaluate_runtime_tool_call_records_args_keys_and_policy() -> None:
    decision = evaluate_runtime_tool_call(
        tool_name="finance_expense_create",
        args={"amount": 100, "concepto": "Hospedaje"},
        role="admin",
        registry=_registry(),
    )

    assert decision["decision"] == "confirm"
    assert decision["requires_confirmation"] is True
    assert decision["args_keys"] == ["amount", "concepto"]


def test_evaluate_runtime_tool_call_denies_unknown_tool() -> None:
    decision = evaluate_runtime_tool_call(
        tool_name="unknown_tool",
        args={},
        role="admin",
        registry=_registry(),
    )

    assert decision["decision"] == "deny"
    assert decision["reason"] == "unknown_tool"
    assert decision["tool_name"] == "unknown_tool"
