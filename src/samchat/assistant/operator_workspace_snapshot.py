"""Persistible operator workspace snapshot contracts for SamChat assistant."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

SCHEMA_VERSION = "operator_workspace_snapshot.v1"
PERSISTENCE_MEDIUM = "assistant_message_tool_payload"


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def build_operator_workspace_snapshot(
    *,
    conversation_id: Any,
    task_id: str,
    preview_render: Mapping[str, Any],
    business_preview: Mapping[str, Any],
    understood_context: Mapping[str, Any],
    live_context: Mapping[str, Any],
    continuity_context: Mapping[str, Any],
    memory_context: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    evidence_quality_gate: Mapping[str, Any],
    resume_guidance: Mapping[str, Any],
    workspace_cards: list[Mapping[str, Any]],
    step_trace: list[Mapping[str, Any]],
    source_panel: list[Mapping[str, Any]],
) -> Dict[str, Any]:
    preview_id = str(preview_render.get("preview_id") or "")
    stable_scope = {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": str(conversation_id or ""),
        "task_id": str(task_id or ""),
        "preview_id": preview_id,
    }
    workspace_id = f"ows_{_canonical_hash(stable_scope)}"
    authority_boundary = {
        "authority": "human_approval_required",
        "primary_action_enabled": False,
        "safe_to_execute": False,
        "operational_writes": False,
        "provider_called": False,
        "required_before_writes": [
            "preview exacto",
            "aprobacion humana",
            "idempotency key",
            "audit trail",
        ],
    }
    cards = [dict(item) for item in workspace_cards]
    steps = [dict(item) for item in step_trace]
    sources = [dict(item) for item in source_panel]
    return {
        "workspace_id": workspace_id,
        "schema_version": SCHEMA_VERSION,
        "persistence_medium": PERSISTENCE_MEDIUM,
        "status": "persisted_with_message_payload",
        "authority": "read_only_workspace_snapshot",
        "conversation_id": str(conversation_id or ""),
        "task_id": str(task_id or ""),
        "preview_id": preview_id,
        "preview_type": preview_render.get("preview_type"),
        "quality_status": evidence_quality_gate.get("quality_status"),
        "readiness": diagnostics.get("readiness"),
        "resume_status": resume_guidance.get("status"),
        "operational_writes": False,
        "primary_action_enabled": False,
        "safe_to_execute": False,
        "component_counts": {
            "workspace_cards": len(cards),
            "step_trace": len(steps),
            "source_panel": len(sources),
        },
        "components": {
            "preview_render": dict(preview_render),
            "business_preview": dict(business_preview),
            "understood_context": dict(understood_context),
            "live_context": dict(live_context),
            "continuity_context": dict(continuity_context),
            "memory_context": dict(memory_context),
            "diagnostics": dict(diagnostics),
            "evidence_quality_gate": dict(evidence_quality_gate),
            "resume_guidance": dict(resume_guidance),
            "workspace_cards": cards,
            "step_trace": steps,
            "source_panel": sources,
            "authority_boundary": authority_boundary,
        },
    }


def compact_operator_workspace_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    components = _as_dict(snapshot.get("components"))
    cards = _as_list(components.get("workspace_cards"))
    steps = _as_list(components.get("step_trace"))
    sources = _as_list(components.get("source_panel"))
    return {
        "workspace_id": snapshot.get("workspace_id"),
        "schema_version": snapshot.get("schema_version"),
        "persistence_medium": snapshot.get("persistence_medium"),
        "status": snapshot.get("status"),
        "authority": snapshot.get("authority"),
        "conversation_id": snapshot.get("conversation_id"),
        "task_id": snapshot.get("task_id"),
        "preview_id": snapshot.get("preview_id"),
        "preview_type": snapshot.get("preview_type"),
        "quality_status": snapshot.get("quality_status"),
        "readiness": snapshot.get("readiness"),
        "resume_status": snapshot.get("resume_status"),
        "operational_writes": bool(snapshot.get("operational_writes")),
        "primary_action_enabled": bool(snapshot.get("primary_action_enabled")),
        "safe_to_execute": bool(snapshot.get("safe_to_execute")),
        "component_counts": {
            "workspace_cards": len(cards),
            "step_trace": len(steps),
            "source_panel": len(sources),
        },
    }
