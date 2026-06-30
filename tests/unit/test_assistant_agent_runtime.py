from __future__ import annotations

from samchat.assistant.agent_runtime import (
    build_agent_runtime_trace,
    build_agent_shadow_trace,
    evaluate_runtime_tool_call,
    evaluate_shadow_activation,
    evaluate_shadow_tool_call,
    is_agent_runtime_enabled,
    is_agent_shadow_enabled,
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


def test_agent_shadow_flag_is_independent_from_real_runtime(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")

    assert is_agent_runtime_enabled() is False
    assert is_agent_shadow_enabled() is True


def test_shadow_activation_disabled_when_env_off(monkeypatch) -> None:
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_ENABLED", raising=False)
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", "emp-1")

    activation = evaluate_shadow_activation(employee_id="emp-1")

    assert activation.enabled is False
    assert activation.decision == "SHADOW_DISABLED"


def test_shadow_activation_requires_non_empty_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_TENANT_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_SUBJECTS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMAILS", raising=False)

    activation = evaluate_shadow_activation(employee_id="emp-1")

    assert activation.enabled is False
    assert activation.decision == "SHADOW_ALLOWLIST_EMPTY"


def test_shadow_activation_allows_employee_id(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", "emp-1,emp-2")
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_TENANT_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_SUBJECTS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMAILS", raising=False)

    activation = evaluate_shadow_activation(employee_id="emp-2")

    assert activation.enabled is True
    assert activation.decision == "SHADOW_ALLOWED_EMPLOYEE_ID"


def test_shadow_activation_allows_tenant_employee_subject(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_SUBJECTS", "tenant-a:emp-1")
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_TENANT_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMAILS", raising=False)

    activation = evaluate_shadow_activation(
        employee_id="emp-1",
        tenant_id="tenant-a",
    )

    assert activation.enabled is True
    assert activation.decision == "SHADOW_ALLOWED_TENANT_EMPLOYEE"


def test_shadow_employee_id_denial_cannot_be_overridden_by_email(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", "emp-1")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_EMAILS", "internal@example.com")
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_TENANT_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_SUBJECTS", raising=False)

    activation = evaluate_shadow_activation(
        employee_id="emp-2",
        email="internal@example.com",
    )

    assert activation.enabled is False
    assert activation.decision == "SHADOW_SUBJECT_NOT_ALLOWED"


def test_shadow_email_fallback_only_when_employee_id_absent(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_EMAILS", "Internal@Example.com")
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_TENANT_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_SUBJECTS", raising=False)

    activation = evaluate_shadow_activation(email="internal@example.com")

    assert activation.enabled is True
    assert activation.decision == "SHADOW_ALLOWED_EMAIL_FALLBACK"


def test_shadow_trace_redacts_raw_email(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_SHADOW_EMAILS", "internal@example.com")
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_TENANT_IDS", raising=False)
    monkeypatch.delenv("ASSISTANT_AGENT_SHADOW_SUBJECTS", raising=False)
    activation = evaluate_shadow_activation(email="internal@example.com")

    trace = build_agent_shadow_trace(
        activation=activation,
        route_info={"route": "lookup_sql", "domain": "finance"},
        tool_defs=[],
        registry={},
    )["assistant_agent_shadow"]

    assert "internal@example.com" not in str(trace)
    assert trace["subject"]["email_hash"].startswith("sha256:")


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


def test_shadow_unknown_tool_blocks_without_handler() -> None:
    trace = evaluate_shadow_tool_call(
        tool_name="unknown_tool",
        args={},
        role="admin",
        registry=_registry(),
    )["assistant_agent_shadow_policy"]

    assert trace["outcome"] == "BLOCK"
    assert trace["handler_invoked"] is False
    assert trace["side_effects_allowed"] is False


def test_shadow_unauthorized_read_blocks_without_handler() -> None:
    registry = build_tool_registry(
        tool_defs=[{"type": "function", "function": {"name": "db_read_universal"}}],
        read_tools={"db_read_universal"},
        write_tools=set(),
        finance_tools=set(),
        tournament_tools=set(),
        dev_tools=set(),
    )

    trace = evaluate_shadow_tool_call(
        tool_name="db_read_universal",
        args={"table": "expenses"},
        role="user",
        registry=registry,
    )["assistant_agent_shadow_policy"]

    assert trace["outcome"] == "BLOCK"
    assert trace["policy"]["reason"] == "role_not_allowed:user"
    assert trace["handler_invoked"] is False


def test_shadow_write_without_confirmation_is_pending_without_handler() -> None:
    trace = evaluate_shadow_tool_call(
        tool_name="finance_expense_create",
        args={"amount": 100},
        role="admin",
        registry=_registry(),
    )["assistant_agent_shadow_policy"]

    assert trace["outcome"] == "PENDING"
    assert trace["policy"]["decision"] == "confirm"
    assert trace["handler_invoked"] is False
