"""Deterministic resume guidance for specialist assistant previews."""

from __future__ import annotations

from typing import Any, Dict, Mapping

_BLOCKED_UNTIL = ["human approval", "idempotency key", "audit trail"]


def build_specialist_resume_guidance(
    *,
    diagnostics: Mapping[str, Any],
    continuity_context: Mapping[str, Any],
    memory_context: Mapping[str, Any],
) -> Dict[str, Any]:
    """Recommend the safest next read-only step for a specialist preview."""

    readiness = str(diagnostics.get("readiness") or "unknown")
    has_active_case = bool(continuity_context.get("matched"))
    has_memory = bool(memory_context.get("matched"))
    missing = list(diagnostics.get("missing") or [])

    if readiness == "needs_more_context":
        status = "needs_more_context"
        mode = "collect_context"
        recommendation = "Pedir o seleccionar primero las referencias faltantes antes de preparar acciones."
    elif has_active_case:
        status = "ready_to_continue_active_case"
        mode = "continue_preview"
        active = continuity_context.get("active_case") or {}
        recommendation = (
            "Continuar con preview/diff read-only del caso activo "
            f"{active.get('case_id') or 'sin id'}; cualquier ejecucion sigue bloqueada."
        )
    elif has_memory:
        status = "precedent_only"
        mode = "use_precedent_as_context"
        recommendation = (
            "Usar la memoria como precedente para orientar la revision, "
            "pero pedir confirmacion antes de asumir que aplica al caso actual."
        )
    else:
        status = "ready_for_isolated_preview"
        mode = "continue_preview"
        recommendation = "Continuar con preview/diff read-only de esta solicitud aislada."

    return {
        "source": "deterministic_resume_guidance",
        "authority": "read_only_guidance",
        "status": status,
        "recommended_mode": mode,
        "recommendation": recommendation,
        "missing": missing,
        "blocked_until": list(_BLOCKED_UNTIL),
        "uses_active_case": has_active_case,
        "uses_case_memory": has_memory,
        "writes_attempted": False,
        "primary_action_enabled": False,
    }


def render_specialist_resume_guidance_markdown(guidance: Mapping[str, Any]) -> str:
    """Render deterministic resume guidance for assistant preview messages."""

    lines = ["## Guia de reanudacion", ""]
    lines.append(f"- Estado: {guidance.get('status') or 'unknown'}.")
    lines.append(f"- Recomendacion: {guidance.get('recommendation') or '-'}")
    missing = list(guidance.get("missing") or [])
    if missing:
        lines.append("- Faltantes antes de avanzar:")
        for item in missing[:5]:
            lines.append(f"  - {item}")
    lines.append(
        "- Bloqueo: no ejecutar hasta aprobacion humana, idempotency key y audit trail."
    )
    return "\n".join(lines) + "\n"
