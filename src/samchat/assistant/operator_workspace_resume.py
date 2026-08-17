"""Read-only resume contract for persisted operator workspace snapshots."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import select

from devnous.gastos.models import AssistantMessage

from .operator_workspace_snapshot import (
    SCHEMA_VERSION,
    compact_operator_workspace_snapshot,
)

_RESUME_PHRASES = (
    "retoma el workspace",
    "retomar el workspace",
    "reanuda el workspace",
    "reanudar el workspace",
    "workspace anterior",
    "retoma el preview",
    "retomar el preview",
    "reanuda el preview",
    "reanudar el preview",
    "preview anterior",
    "continua el preview",
    "continuar el preview",
    "sigue con el preview",
    "sigamos con el preview",
    "donde ibamos con el preview",
    "donde ibamos con el workspace",
)


def _normalize(text: str) -> str:
    normalized = (text or "").strip().lower()
    normalized = normalized.translate(str.maketrans("???????", "aeiouun"))
    return re.sub(r"\s+", " ", normalized)


def detect_operator_workspace_resume_intent(raw_message: str) -> bool:
    """Detect an explicit request to resume a prior specialist workspace."""

    normalized = _normalize(raw_message)
    return any(phrase in normalized for phrase in _RESUME_PHRASES)


def extract_operator_workspace_snapshot_from_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Return a valid persisted workspace snapshot from an assistant payload."""

    if not isinstance(payload, Mapping):
        return None
    snapshot = payload.get("operator_workspace_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        return None
    if snapshot.get("authority") != "read_only_workspace_snapshot":
        return None
    if not snapshot.get("workspace_id"):
        return None
    if bool(snapshot.get("safe_to_execute")) or bool(snapshot.get("primary_action_enabled")):
        return None
    return dict(snapshot)


async def load_latest_operator_workspace_snapshot(
    *,
    session: Any,
    conversation_id: Any,
    limit: int = 30,
) -> Dict[str, Any]:
    """Load the latest read-only operator workspace snapshot in a conversation."""

    try:
        rows = (
            await session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    except Exception as exc:  # pragma: no cover - defensive fail-closed path
        return {
            "status": "snapshot_lookup_failed",
            "matched": False,
            "error": exc.__class__.__name__,
            "writes_attempted": False,
        }

    inspected = 0
    for message in rows:
        inspected += 1
        snapshot = extract_operator_workspace_snapshot_from_payload(
            getattr(message, "tool_payload", None)
        )
        if snapshot is None:
            continue
        return {
            "status": "matched",
            "matched": True,
            "message_id": str(getattr(message, "id", "") or ""),
            "inspected_messages": inspected,
            "snapshot": snapshot,
            "compact_snapshot": compact_operator_workspace_snapshot(snapshot),
            "writes_attempted": False,
        }

    return {
        "status": "no_resumable_workspace_snapshot",
        "matched": False,
        "inspected_messages": inspected,
        "writes_attempted": False,
    }


def build_operator_workspace_resume_response(
    resolution: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a deterministic read-only resume response object."""

    matched = bool(resolution.get("matched"))
    snapshot = resolution.get("snapshot") if isinstance(resolution.get("snapshot"), Mapping) else {}
    compact = (
        dict(resolution.get("compact_snapshot"))
        if isinstance(resolution.get("compact_snapshot"), Mapping)
        else (compact_operator_workspace_snapshot(snapshot) if snapshot else {})
    )
    components = snapshot.get("components") if isinstance(snapshot, Mapping) else {}
    if not isinstance(components, Mapping):
        components = {}
    resume_guidance = components.get("resume_guidance")
    if not isinstance(resume_guidance, Mapping):
        resume_guidance = {}
    diagnostics = components.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}

    return {
        "source": "operator_workspace_resume",
        "status": "ready_to_resume" if matched else "no_resumable_workspace",
        "matched": matched,
        "authority": "read_only_workspace_resume",
        "workspace_id": compact.get("workspace_id"),
        "task_id": compact.get("task_id"),
        "preview_id": compact.get("preview_id"),
        "quality_status": compact.get("quality_status"),
        "readiness": compact.get("readiness"),
        "resume_status": compact.get("resume_status"),
        "recommendation": resume_guidance.get("recommendation"),
        "missing": list(diagnostics.get("missing") or resume_guidance.get("missing") or []),
        "component_counts": dict(compact.get("component_counts") or {}),
        "compact_snapshot": compact,
        "lookup": {
            "status": resolution.get("status"),
            "message_id": resolution.get("message_id"),
            "inspected_messages": resolution.get("inspected_messages"),
        },
        "operational_writes": False,
        "primary_action_enabled": False,
        "safe_to_execute": False,
        "provider_called": False,
        "writes_attempted": False,
    }


def render_operator_workspace_resume_markdown(resume: Mapping[str, Any]) -> str:
    """Render a compact human-facing resume note for the assistant UI."""

    lines = ["## Workspace retomado", ""]
    if not resume.get("matched"):
        lines.extend(
            [
                "No encontre un workspace/preview especialista persistido en esta conversacion.",
                "Para continuar sin inventar contexto, crea primero un preview especialista o dime la referencia concreta que quieres revisar.",
                "",
                "Frontera de autoridad: no ejecute acciones ni intente writes.",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.append(f"- Workspace: `{resume.get('workspace_id') or '-'}`")
    lines.append(f"- Tarea: `{resume.get('task_id') or '-'}`")
    lines.append(f"- Preview: `{resume.get('preview_id') or '-'}`")
    lines.append(f"- Calidad de evidencia: {resume.get('quality_status') or 'unknown'}")
    lines.append(f"- Readiness: {resume.get('readiness') or 'unknown'}")
    recommendation = resume.get("recommendation")
    if recommendation:
        lines.append(f"- Siguiente paso sugerido: {recommendation}")
    missing = list(resume.get("missing") or [])
    if missing:
        lines.append("- Faltantes antes de avanzar:")
        for item in missing[:5]:
            lines.append(f"  - {item}")
    counts = resume.get("component_counts") if isinstance(resume.get("component_counts"), Mapping) else {}
    if counts:
        lines.append(
            "- Componentes disponibles: "
            f"cards={counts.get('workspace_cards', 0)}, "
            f"pasos={counts.get('step_trace', 0)}, "
            f"fuentes={counts.get('source_panel', 0)}."
        )
    lines.append("")
    lines.append("Frontera de autoridad: esto solo reanuda lectura/contexto; no ejecuta acciones, no llama provider y no habilita writes.")
    return "\n".join(lines) + "\n"
