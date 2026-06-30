from __future__ import annotations

from samchat.assistant.router import (
    DEV_WRITE_TOOLS,
    FINANCE_WRITE_TOOLS,
    READ_TOOLS,
    TOURNAMENT_WRITE_TOOLS,
    WRITE_TOOLS,
    _assistant_tool_registry,
)
from samchat.assistant.tool_registry import (
    build_tool_registry,
    filter_tool_defs_by_policy,
)


def test_assistant_tool_registry_marks_known_writes_as_confirmed() -> None:
    registry = _assistant_tool_registry()

    for tool_name in sorted(WRITE_TOOLS):
        spec = registry[tool_name]
        assert spec.operation_type == "write"
        assert spec.requires_confirmation is True


def test_assistant_tool_registry_marks_known_reads_as_read_only() -> None:
    registry = _assistant_tool_registry()

    for tool_name in sorted(READ_TOOLS):
        spec = registry[tool_name]
        assert spec.operation_type == "read"
        assert spec.requires_confirmation is False


def test_assistant_tool_registry_assigns_surfaces_for_write_groups() -> None:
    registry = _assistant_tool_registry()

    assert registry["dev_file_write"].surface == "dev"
    assert registry["finance_expense_create"].surface == "finance"
    assert registry["tournament_schedule_create"].surface == "tournament"
    assert registry["assistant_canonical_action"].surface == "cross_domain"
    assert registry["db_write_universal"].surface == "database"
    assert registry["db_read_universal"].allowed_roles == ("admin", "superadmin")
    assert registry["db_write_universal"].allowed_roles == ("superadmin",)

    assert DEV_WRITE_TOOLS <= set(registry)
    assert FINANCE_WRITE_TOOLS <= set(registry)
    assert TOURNAMENT_WRITE_TOOLS <= set(registry)


def test_filter_tool_defs_by_policy_rejects_unknown_tools() -> None:
    tool_defs = [
        {"type": "function", "function": {"name": "known_read"}},
        {"type": "function", "function": {"name": "unknown_tool"}},
    ]
    registry = build_tool_registry(
        tool_defs=tool_defs,
        read_tools={"known_read"},
        write_tools=set(),
        finance_tools={"known_read"},
        tournament_tools=set(),
        dev_tools=set(),
    )

    filtered = filter_tool_defs_by_policy(tool_defs, registry)

    assert [item["function"]["name"] for item in filtered] == ["known_read"]
