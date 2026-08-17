"""Read-only resume contract for persisted operator workspace snapshots."""

from __future__ import annotations

import re
import unicodedata
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
    decomposed = unicodedata.normalize("NFKD", normalized)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_marks)


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


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _titles(items: list[Any], *, limit: int = 5) -> list[str]:
    titles: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        title = (
            item.get("title")
            or item.get("step_id")
            or item.get("source_id")
            or item.get("card_id")
        )
        if title:
            titles.append(str(title))
        if len(titles) >= limit:
            break
    return titles


def build_operator_workspace_continuity_surface(
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    """Summarize a persisted workspace into a resume-friendly read-only surface."""

    components = _as_mapping(snapshot.get("components"))
    understood = _as_mapping(components.get("understood_context"))
    live_context = _as_mapping(components.get("live_context"))
    diagnostics = _as_mapping(components.get("diagnostics"))
    evidence_gate = _as_mapping(components.get("evidence_quality_gate"))
    resume_guidance = _as_mapping(components.get("resume_guidance"))
    business_preview = _as_mapping(components.get("business_preview"))
    workspace_cards = _as_list(components.get("workspace_cards"))
    step_trace = _as_list(components.get("step_trace"))
    source_panel = _as_list(components.get("source_panel"))

    known_context = {
        "document_refs": _as_list(understood.get("document_refs")),
        "operations_refs": _as_list(understood.get("operations_refs")),
        "uuid_or_prefixes": _as_list(understood.get("uuid_or_prefixes")),
        "account_codes": _as_list(understood.get("account_codes")),
        "domains": _as_list(understood.get("domains")),
        "entities": _as_list(understood.get("entities")),
        "live_documents": len(_as_list(live_context.get("documents"))),
        "live_expenses": len(_as_list(live_context.get("expenses"))),
        "live_cfdis": len(_as_list(live_context.get("cfdis"))),
    }
    missing = _as_list(diagnostics.get("missing")) or _as_list(
        resume_guidance.get("missing")
    )
    findings = _as_list(diagnostics.get("findings"))
    risks = _as_list(diagnostics.get("risks"))
    next_steps = _as_list(diagnostics.get("next_steps"))
    if resume_guidance.get("recommendation"):
        next_steps = [str(resume_guidance.get("recommendation"))] + [
            str(item) for item in next_steps
        ]

    return {
        "source": "operator_workspace_snapshot",
        "authority": "read_only_continuity_surface",
        "status": "ready" if snapshot else "unavailable",
        "what_i_know": known_context,
        "findings": findings[:8],
        "missing": [str(item) for item in missing[:8]],
        "risks": [str(item) for item in risks[:8]],
        "next_steps": [str(item) for item in next_steps[:5]],
        "available_sources": _titles(source_panel, limit=8),
        "available_steps": _titles(step_trace, limit=8),
        "available_cards": _titles(workspace_cards, limit=8),
        "preview_summary": {
            "task_id": snapshot.get("task_id"),
            "preview_id": snapshot.get("preview_id"),
            "preview_type": snapshot.get("preview_type"),
            "business_task_id": business_preview.get("task_id"),
            "quality_status": evidence_gate.get("quality_status")
            or snapshot.get("quality_status"),
            "readiness": diagnostics.get("readiness") or snapshot.get("readiness"),
        },
        "recommended_preview_task_id": snapshot.get("task_id"),
        "provider_called": False,
        "writes_attempted": False,
        "primary_action_enabled": False,
        "safe_to_execute": False,
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

    continuity_surface = (
        build_operator_workspace_continuity_surface(snapshot)
        if matched and snapshot
        else {
            "source": "operator_workspace_snapshot",
            "authority": "read_only_continuity_surface",
            "status": "unavailable",
            "provider_called": False,
            "writes_attempted": False,
            "primary_action_enabled": False,
            "safe_to_execute": False,
        }
    )

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
        "continuity_surface": continuity_surface,
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
    surface = resume.get("continuity_surface")
    if not isinstance(surface, Mapping):
        surface = {}
    what_i_know = (
        surface.get("what_i_know")
        if isinstance(surface.get("what_i_know"), Mapping)
        else {}
    )
    if what_i_know:
        lines.append("- Lo que ya sabe el workspace:")
        for key in (
            "document_refs",
            "operations_refs",
            "uuid_or_prefixes",
            "domains",
            "entities",
        ):
            values = list(what_i_know.get(key) or [])
            if values:
                joined = ", ".join(str(item) for item in values[:5])
                lines.append(f"  - {key}: {joined}")
        live_counts = [
            f"documentos={what_i_know.get('live_documents', 0)}",
            f"gastos={what_i_know.get('live_expenses', 0)}",
            f"CFDI={what_i_know.get('live_cfdis', 0)}",
        ]
        lines.append(f"  - contexto live: {', '.join(live_counts)}")

    findings = list(surface.get("findings") or [])
    if findings:
        lines.append("- Hallazgos:")
        for item in findings[:5]:
            lines.append(f"  - {item}")

    missing = list(surface.get("missing") or resume.get("missing") or [])
    if missing:
        lines.append("- Faltantes antes de avanzar:")
        for item in missing[:5]:
            lines.append(f"  - {item}")

    sources = list(surface.get("available_sources") or [])
    if sources:
        lines.append(f"- Fuentes disponibles: {', '.join(str(item) for item in sources[:6])}.")

    next_steps = list(surface.get("next_steps") or [])
    recommendation = resume.get("recommendation")
    if not next_steps and recommendation:
        next_steps = [str(recommendation)]
    if next_steps:
        lines.append("- Siguiente paso recomendado:")
        for item in next_steps[:3]:
            lines.append(f"  - {item}")

    counts = (
        resume.get("component_counts")
        if isinstance(resume.get("component_counts"), Mapping)
        else {}
    )
    if counts:
        lines.append(
            "- Componentes disponibles: "
            f"cards={counts.get('workspace_cards', 0)}, "
            f"pasos={counts.get('step_trace', 0)}, "
            f"fuentes={counts.get('source_panel', 0)}."
        )
    lines.append("")
    lines.append(
        "Frontera de autoridad: esto solo reanuda lectura/contexto; "
        "no ejecuta acciones, no llama provider y no habilita writes."
    )
    return "\n".join(lines) + "\n"
