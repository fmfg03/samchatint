from __future__ import annotations

from samchat.assistant.policy import evaluate_tool_policy, normalize_role
from samchat.assistant.tool_registry import AssistantToolSpec


def _spec(
    *,
    name: str = "finance_ops_query",
    surface: str = "finance",
    operation_type: str = "read",
    requires_confirmation: bool = False,
    allowed_roles: tuple[str, ...] = ("user", "admin", "superadmin"),
) -> AssistantToolSpec:
    return AssistantToolSpec(
        name=name,
        surface=surface,
        operation_type=operation_type,
        risk_level="low" if operation_type == "read" else "high",
        requires_confirmation=requires_confirmation,
        allowed_roles=allowed_roles,
        handler_kind="existing_tool",
    )


def test_normalize_role_maps_super_admin_alias() -> None:
    assert normalize_role("super_admin") == "superadmin"
    assert normalize_role(" admin ") == "admin"
    assert normalize_role(None) == "user"


def test_policy_denies_unknown_tool() -> None:
    decision = evaluate_tool_policy(None, role="admin")

    assert decision.allowed is False
    assert decision.decision == "deny"
    assert decision.reason == "unknown_tool"


def test_policy_allows_registered_read_tool_for_user_role() -> None:
    decision = evaluate_tool_policy(_spec(), role="user")

    assert decision.allowed is True
    assert decision.decision == "allow"
    assert decision.requires_confirmation is False


def test_policy_requires_confirmation_for_unconfirmed_write_tool() -> None:
    decision = evaluate_tool_policy(
        _spec(
            name="finance_expense_create",
            operation_type="write",
            requires_confirmation=True,
            allowed_roles=("admin", "superadmin"),
        ),
        role="admin",
        confirmed=False,
    )

    assert decision.allowed is False
    assert decision.decision == "confirm"
    assert decision.requires_confirmation is True


def test_policy_allows_confirmed_write_tool_for_allowed_role() -> None:
    decision = evaluate_tool_policy(
        _spec(
            name="finance_expense_create",
            operation_type="write",
            requires_confirmation=True,
            allowed_roles=("admin", "superadmin"),
        ),
        role="super_admin",
        confirmed=True,
    )

    assert decision.allowed is True
    assert decision.decision == "allow"


def test_policy_denies_write_tool_for_insufficient_role() -> None:
    decision = evaluate_tool_policy(
        _spec(
            name="dev_file_write",
            surface="dev",
            operation_type="write",
            requires_confirmation=True,
            allowed_roles=("superadmin",),
        ),
        role="admin",
        confirmed=True,
    )

    assert decision.allowed is False
    assert decision.decision == "deny"
    assert decision.reason == "role_not_allowed:admin"
