"""Deterministic evidence quality gate for specialist previews.

The gate is read-only and non-authoritative. It explains whether a specialist
preview is backed by current-case evidence, only informed by precedent, or still
missing support. It never enables execution.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


SUPPORTED = "supported"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _non_empty_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _compact(value)
        if text:
            result.append(text)
    return result


def _proposed_changes(business_preview: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [item for item in _as_list(business_preview.get("proposed_changes")) if isinstance(item, Mapping)]


def _found_evidence(business_preview: Mapping[str, Any]) -> List[str]:
    return _non_empty_strings(_as_list(business_preview.get("found_evidence")))


def _missing_evidence(business_preview: Mapping[str, Any]) -> List[str]:
    return _non_empty_strings(_as_list(business_preview.get("missing_evidence")))


def _unresolved_live_refs(live_context: Mapping[str, Any]) -> List[str]:
    unresolved = live_context.get("unresolved") or {}
    if not isinstance(unresolved, Mapping):
        return []
    result: List[str] = []
    for key, values in sorted(unresolved.items()):
        if isinstance(values, list):
            for value in values:
                text = _compact(value)
                if text:
                    result.append(f"{key}:{text}")
        else:
            text = _compact(values)
            if text:
                result.append(f"{key}:{text}")
    return result


def build_specialist_evidence_quality_gate(
    *,
    business_preview: Mapping[str, Any],
    live_context: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    memory_context: Mapping[str, Any] | None = None,
    continuity_context: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Classify the evidence posture for a specialist preview.

    The function is deliberately conservative. A preview can continue as a
    read-only working surface with partial evidence, but execution remains
    blocked until a future human approval receipt boundary exists.
    """

    memory = _as_dict(memory_context)
    continuity = _as_dict(continuity_context)
    changes = _proposed_changes(business_preview)
    supported_changes = [item for item in changes if _compact(item.get("evidence_id"))]
    unbound_changes = [
        {
            "field": _compact(item.get("field")) or "unknown",
            "reason": "proposed_change_without_evidence_id",
        }
        for item in changes
        if not _compact(item.get("evidence_id"))
    ]
    found = _found_evidence(business_preview)
    missing = _missing_evidence(business_preview)
    diagnostic_missing = _non_empty_strings(_as_list(diagnostics.get("missing")))
    unresolved = _unresolved_live_refs(live_context)
    precedent_count = len(_as_list(memory.get("snippets")))
    current_case_matched = bool(live_context.get("matched")) or bool(
        continuity.get("matched")
    )

    execution_blockers: List[str] = []
    if missing:
        execution_blockers.append("business_preview_missing_evidence")
    if diagnostic_missing:
        execution_blockers.append("diagnostics_missing_context")
    if unresolved:
        execution_blockers.append("live_context_unresolved_references")
    if unbound_changes:
        execution_blockers.append("proposed_changes_without_bound_evidence")
    if live_context.get("status") == "lookup_error":
        execution_blockers.append("live_context_lookup_error")
    if not changes and not found and not current_case_matched:
        execution_blockers.append("no_current_case_evidence")

    caveats: List[str] = []
    if precedent_count:
        caveats.append(
            "La memoria de casos informa como precedente; no prueba hechos del caso actual."
        )
    if current_case_matched:
        caveats.append(
            "Hay contexto de caso actual, pero sigue siendo lectura read-only."
        )
    if unbound_changes:
        caveats.append(
            "Hay cambios propuestos sin evidencia vinculada explicitamente."
        )
    if missing or diagnostic_missing or unresolved:
        caveats.append("Hay evidencia o referencias pendientes antes de cualquier ejecucion.")

    if supported_changes and not execution_blockers:
        quality_status = SUPPORTED
        safe_to_continue_preview = True
    elif supported_changes or found or current_case_matched or precedent_count:
        quality_status = PARTIAL
        safe_to_continue_preview = True
    else:
        quality_status = INSUFFICIENT
        safe_to_continue_preview = False

    next_steps: List[str] = []
    if quality_status == SUPPORTED:
        next_steps.append(
            "Continuar con revision read-only del preview; ejecucion sigue bloqueada."
        )
    else:
        next_steps.append(
            "Resolver faltantes de evidencia antes de convertir el preview en accion aprobable."
        )
    if unbound_changes:
        next_steps.append("Vincular evidence_id explicito a cada cambio propuesto.")
    if unresolved:
        next_steps.append("Resolver referencias live no encontradas en SamChat.")
    if precedent_count and not current_case_matched:
        next_steps.append("Pedir una referencia actual; el precedente no basta para ejecutar.")

    return {
        "source": "deterministic_specialist_evidence_quality_gate",
        "authority": "read_only_evidence_gate",
        "quality_status": quality_status,
        "safe_to_continue_preview": safe_to_continue_preview,
        "safe_to_execute": False,
        "primary_action_enabled": False,
        "supported_change_count": len(supported_changes),
        "unbound_change_count": len(unbound_changes),
        "found_evidence_count": len(found),
        "missing_evidence_count": len(missing) + len(diagnostic_missing) + len(unresolved),
        "precedent_count": precedent_count,
        "current_case_matched": current_case_matched,
        "supported_changes": [dict(item) for item in supported_changes],
        "unbound_changes": unbound_changes,
        "found_evidence": found,
        "missing_evidence": missing,
        "diagnostic_missing": diagnostic_missing,
        "unresolved_references": unresolved,
        "execution_blockers": sorted(set(execution_blockers)),
        "caveats": caveats,
        "next_steps": next_steps,
        "writes_attempted": False,
    }


def render_specialist_evidence_quality_gate_markdown(
    gate: Mapping[str, Any]
) -> str:
    """Render a compact human-readable quality gate section."""

    status = gate.get("quality_status") or "unknown"
    lines = ["## Calidad de evidencia", ""]
    lines.append(f"- Estado: {status}.")
    lines.append(
        "- Cambios soportados: "
        f"{gate.get('supported_change_count', 0)}; "
        "sin evidencia vinculada: "
        f"{gate.get('unbound_change_count', 0)}."
    )
    lines.append(
        "- Evidencia encontrada: "
        f"{gate.get('found_evidence_count', 0)}; "
        "faltantes: "
        f"{gate.get('missing_evidence_count', 0)}; "
        "precedentes: "
        f"{gate.get('precedent_count', 0)}."
    )
    blockers = _non_empty_strings(_as_list(gate.get("execution_blockers")))
    if blockers:
        lines.append("- Bloqueos de ejecucion:")
        for blocker in blockers:
            lines.append(f"  - {blocker}")
    caveats = _non_empty_strings(_as_list(gate.get("caveats")))
    if caveats:
        lines.append("- Caveats:")
        for caveat in caveats:
            lines.append(f"  - {caveat}")
    next_steps = _non_empty_strings(_as_list(gate.get("next_steps")))
    if next_steps:
        lines.append("- Siguiente paso:")
        for step in next_steps:
            lines.append(f"  - {step}")
    lines.append("- Alcance: compuerta read-only; no autoriza ni ejecuta cambios.")
    return "\n".join(lines) + "\n"
