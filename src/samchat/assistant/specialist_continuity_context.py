"""Read-only continuity context for specialist assistant previews."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping

CASE_ID_PATTERN = re.compile(r"^analyst_case_[0-9a-f]{32}$")


def _metadata(conversation: Any) -> Mapping[str, Any]:
    value = getattr(conversation, "metadata_", None) or {}
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_specialist_preview_continuity_context(conversation: Any) -> Dict[str, Any]:
    """Expose current conversation case continuity without mutating state."""

    metadata = _metadata(conversation)
    base: Dict[str, Any] = {
        "source": "conversation_metadata",
        "lookup_performed": True,
        "authority": "read_only_continuity",
        "matched": False,
        "status": "no_active_case",
        "module_key": _string(metadata.get("module_key")),
        "module_label": _string(metadata.get("module_label")),
        "tournament_key": _string(getattr(conversation, "tournament_key", None)),
        "active_case": None,
    }
    if not metadata:
        base["status"] = "no_conversation_metadata"
        return base

    pointer = metadata.get("active_tournament_goal_case")
    if pointer is None:
        return base
    if not isinstance(pointer, Mapping):
        base["status"] = "invalid_active_case_pointer"
        return base

    case_id = _string(pointer.get("case_id"))
    if not case_id or not CASE_ID_PATTERN.fullmatch(case_id):
        base["status"] = "invalid_active_case_pointer"
        return base

    try:
        case_version = int(pointer.get("case_version") or 0)
    except (TypeError, ValueError):
        case_version = 0
    if case_version < 1:
        base["status"] = "invalid_active_case_pointer"
        return base

    base["matched"] = True
    base["status"] = "active_case_found"
    base["active_case"] = {
        "kind": "tournament_goal_case",
        "case_id": case_id,
        "case_version": case_version,
        "status": _string(pointer.get("status")) or "unknown",
    }
    return base


def render_specialist_continuity_context_markdown(
    continuity_context: Mapping[str, Any]
) -> str:
    """Render active case continuity for specialist preview messages."""

    lines = ["## Continuidad del caso", ""]
    active = continuity_context.get("active_case")
    if isinstance(active, Mapping):
        lines.append(
            "- Caso activo: "
            f"{active.get('case_id')} v{active.get('case_version')} "
            f"estado {active.get('status') or 'unknown'}."
        )
    else:
        lines.append("- No hay caso activo ligado a esta conversacion.")
    module = continuity_context.get("module_label") or continuity_context.get("module_key")
    tournament = continuity_context.get("tournament_key")
    if module:
        lines.append(f"- Modulo: {module}.")
    if tournament:
        lines.append(f"- Torneo/scope: {tournament}.")
    lines.append("- Alcance: continuidad read-only; no autoriza ejecucion ni cambios.")
    return "\n".join(lines) + "\n"
