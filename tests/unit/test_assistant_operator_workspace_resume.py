from __future__ import annotations

from types import SimpleNamespace

import pytest

from samchat.assistant.operator_workspace_resume import (
    build_operator_workspace_resume_response,
    detect_operator_workspace_resume_intent,
    extract_operator_workspace_snapshot_from_payload,
    load_latest_operator_workspace_snapshot,
    render_operator_workspace_resume_markdown,
)
from samchat.assistant.operator_workspace_snapshot import build_operator_workspace_snapshot


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
        diagnostics={
            "authority": "read_only_diagnostic",
            "readiness": "ready_for_read_only_preview",
            "missing": [],
        },
        evidence_quality_gate={
            "authority": "read_only_evidence_gate",
            "quality_status": "supported",
            "safe_to_execute": False,
        },
        resume_guidance={
            "authority": "read_only_guidance",
            "status": "ready_for_isolated_preview",
            "recommendation": "Continuar con preview/diff read-only.",
        },
        workspace_cards=[{"card_id": "understood_context"}, {"card_id": "authority_boundary"}],
        step_trace=[{"step_id": "understand_request"}],
        source_panel=[{"source_id": "user_message"}],
    )


def test_resume_intent_is_explicit_to_avoid_hijacking_generic_sigamos() -> None:
    assert detect_operator_workspace_resume_intent("Retoma el workspace anterior")
    assert detect_operator_workspace_resume_intent("sigamos con el preview anterior")
    assert not detect_operator_workspace_resume_intent("sigamos")
    assert not detect_operator_workspace_resume_intent("continua")


def test_extract_snapshot_requires_read_only_snapshot_contract() -> None:
    snapshot = _snapshot()
    assert extract_operator_workspace_snapshot_from_payload({"operator_workspace_snapshot": snapshot}) == snapshot

    unsafe = dict(snapshot)
    unsafe["primary_action_enabled"] = True
    assert extract_operator_workspace_snapshot_from_payload({"operator_workspace_snapshot": unsafe}) is None

    wrong_schema = dict(snapshot)
    wrong_schema["schema_version"] = "operator_workspace_snapshot.v0"
    assert extract_operator_workspace_snapshot_from_payload({"operator_workspace_snapshot": wrong_schema}) is None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _query):
        return _Result(self.rows)


@pytest.mark.asyncio
async def test_load_latest_snapshot_skips_invalid_payloads_and_returns_compact_copy() -> None:
    snapshot = _snapshot()
    rows = [
        SimpleNamespace(id="msg-invalid", tool_payload={"other": True}),
        SimpleNamespace(id="msg-valid", tool_payload={"operator_workspace_snapshot": snapshot}),
    ]

    resolution = await load_latest_operator_workspace_snapshot(
        session=_FakeSession(rows),
        conversation_id="conv-1",
    )

    assert resolution["matched"] is True
    assert resolution["message_id"] == "msg-valid"
    assert resolution["inspected_messages"] == 2
    assert resolution["compact_snapshot"]["workspace_id"] == snapshot["workspace_id"]
    assert "components" not in resolution["compact_snapshot"]
    assert resolution["writes_attempted"] is False


def test_resume_response_and_markdown_are_read_only() -> None:
    snapshot = _snapshot()
    resume = build_operator_workspace_resume_response(
        {
            "status": "matched",
            "matched": True,
            "message_id": "msg-1",
            "snapshot": snapshot,
        }
    )
    rendered = render_operator_workspace_resume_markdown(resume)

    assert resume["status"] == "ready_to_resume"
    assert resume["authority"] == "read_only_workspace_resume"
    assert resume["provider_called"] is False
    assert resume["writes_attempted"] is False
    assert resume["safe_to_execute"] is False
    assert "Workspace retomado" in rendered
    assert "no ejecuta acciones" in rendered


def test_resume_without_snapshot_fails_closed() -> None:
    resume = build_operator_workspace_resume_response(
        {"status": "no_resumable_workspace_snapshot", "matched": False}
    )
    rendered = render_operator_workspace_resume_markdown(resume)

    assert resume["status"] == "no_resumable_workspace"
    assert resume["safe_to_execute"] is False
    assert "No encontre un workspace" in rendered
    assert "no ejecute acciones" in rendered
