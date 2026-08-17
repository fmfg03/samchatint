from __future__ import annotations

from samchat.assistant.operator_workspace_snapshot import (
    SCHEMA_VERSION,
    compact_operator_workspace_snapshot,
    build_operator_workspace_snapshot,
)


def _snapshot():
    return build_operator_workspace_snapshot(
        conversation_id="conv-1",
        task_id="SAMCHAT-CXC-COLLECTION-001",
        preview_render={
            "preview_id": "preview-1",
            "task_id": "SAMCHAT-CXC-COLLECTION-001",
            "preview_type": "accounts_receivable_collection",
            "primary_action_enabled": False,
            "execution_status": "not_executed",
        },
        business_preview={"task_id": "SAMCHAT-CXC-COLLECTION-001"},
        understood_context={"authority": "context_hint_only"},
        live_context={"authority": "read_only_context", "matched": True},
        continuity_context={"authority": "read_only_continuity", "matched": False},
        memory_context={"authority": "read_only_memory", "matched": False},
        diagnostics={"authority": "read_only_diagnostic", "readiness": "ready_for_read_only_preview"},
        evidence_quality_gate={
            "authority": "read_only_evidence_gate",
            "quality_status": "supported",
            "safe_to_execute": False,
        },
        resume_guidance={"authority": "read_only_guidance", "status": "ready_for_isolated_preview"},
        workspace_cards=[{"card_id": "understood_context"}, {"card_id": "authority_boundary"}],
        step_trace=[{"step_id": "understand_request"}],
        source_panel=[{"source_id": "user_message"}],
    )


def test_operator_workspace_snapshot_has_stable_read_only_contract() -> None:
    first = _snapshot()
    second = _snapshot()

    assert first["workspace_id"] == second["workspace_id"]
    assert first["workspace_id"].startswith("ows_")
    assert first["schema_version"] == SCHEMA_VERSION
    assert first["persistence_medium"] == "assistant_message_tool_payload"
    assert first["authority"] == "read_only_workspace_snapshot"
    assert first["operational_writes"] is False
    assert first["primary_action_enabled"] is False
    assert first["safe_to_execute"] is False
    assert first["component_counts"] == {
        "workspace_cards": 2,
        "step_trace": 1,
        "source_panel": 1,
    }
    assert first["components"]["authority_boundary"]["authority"] == "human_approval_required"
    assert first["components"]["authority_boundary"]["safe_to_execute"] is False


def test_compact_operator_workspace_snapshot_omits_heavy_components() -> None:
    snapshot = _snapshot()
    compact = compact_operator_workspace_snapshot(snapshot)

    assert compact["workspace_id"] == snapshot["workspace_id"]
    assert compact["task_id"] == "SAMCHAT-CXC-COLLECTION-001"
    assert compact["quality_status"] == "supported"
    assert compact["component_counts"]["workspace_cards"] == 2
    assert compact["primary_action_enabled"] is False
    assert compact["safe_to_execute"] is False
    assert "components" not in compact
