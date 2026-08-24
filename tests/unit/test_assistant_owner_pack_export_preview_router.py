from __future__ import annotations

import pytest

from samchat.assistant.router import (
    FINANCE_READ_TOOLS,
    READ_TOOLS,
    TOURNAMENT_READ_TOOLS,
    _assistant_tool_registry,
    _run_read_tool,
)


@pytest.mark.asyncio
async def test_owner_pack_export_preview_router_tool_is_read_only() -> None:
    payload = await _run_read_tool(
        "assistant_owner_pack_export_preview",
        {"scope": "entity_folder", "tournament_slug": "Copa Telmex", "entity_name": "Jalisco"},
        gastos_session=None,
        tournament_key_default=None,
        current_role="admin",
    )

    assert payload["schema_version"] == "samchat.owner_pack_export_preview.v1"
    assert payload["audit_language"] == "owner_pack_export_preview_only"
    assert payload["execution_status"] == "not_executed"
    assert payload["writes_attempted"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["safety_summary"]["writes_enabled"] is False
    assert payload["html_preview"].startswith("<!doctype html>")
    assert payload["excel_index"]["media_type"] == "text/csv; charset=utf-8"
    assert payload["formats"]["pdf"]["route"].endswith("export-preview.pdf")


def test_owner_pack_export_preview_tool_registry_is_read_only() -> None:
    registry = _assistant_tool_registry()
    spec = registry["assistant_owner_pack_export_preview"]

    assert "assistant_owner_pack_export_preview" in READ_TOOLS
    assert "assistant_owner_pack_export_preview" in FINANCE_READ_TOOLS
    assert "assistant_owner_pack_export_preview" in TOURNAMENT_READ_TOOLS
    assert spec.surface == "assistant"
    assert spec.operation_type == "read"
    assert spec.requires_confirmation is False
