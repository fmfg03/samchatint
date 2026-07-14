from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .analyst_intent import AnalystIntent
from .analyst_workbench import AnalystWorkbenchResult


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _route_display(route: Any) -> Optional[str]:
    if isinstance(route, dict):
        label = _compact_text(route.get("label") or route.get("route_id"))
        route_id = _compact_text(route.get("route_id"))
        status = _compact_text(route.get("execution_status"))
        if not label:
            return None
        suffixes = []
        if route_id and route_id != label:
            suffixes.append(route_id)
        if status:
            suffixes.append(status)
        return f"{label} ({', '.join(suffixes)})" if suffixes else label
    text = _compact_text(route)
    return text or None


def render_analyst_result(result: AnalystWorkbenchResult) -> str:
    title = _compact_text(result.title) or "Analyst Workbench"
    answer = str(result.answer or "").strip()
    lines = [title, "", "Respuesta:", answer]
    evidence_lines = []
    for item in result.evidence:
        label = _compact_text(
            item.get("label") or item.get("source_type") or "contexto"
        )
        summary = _compact_text(item.get("summary"))
        if label and summary:
            evidence_lines.append(f"- {label}: {summary}")
        elif summary:
            evidence_lines.append(f"- {summary}")
    if evidence_lines:
        lines.extend(["", "Soporte en evidencia:"])
        lines.extend(evidence_lines)
    caveats = [
        _compact_text(caveat)
        for caveat in result.caveats
        if _compact_text(caveat)
    ]
    if caveats:
        lines.extend(["", "Límites:"])
        for caveat in caveats:
            lines.append(f"- {caveat}")
    questions = [
        _compact_text(question)
        for question in result.next_questions
        if _compact_text(question)
    ]
    if questions:
        lines.extend(["", "Siguientes preguntas:"])
        for question in questions:
            lines.append(f"- {question}")
    routes = [
        route_text
        for route_text in (
            _route_display(route) for route in result.suggested_routes
        )
        if route_text
    ]
    if routes:
        lines.extend(["", "Ruta sugerida:"])
        for route in routes:
            lines.append(f"- {route}")
    return "\n".join(lines).strip()


def build_analyst_trace(
    *,
    intent: AnalystIntent,
    result: AnalystWorkbenchResult,
) -> List[Dict[str, Any]]:
    evidence_types = [
        str(item.get("source_type") or "unknown")
        for item in result.evidence
    ]
    evidence_labels = [
        str(item.get("label") or item.get("source_type") or "contexto")
        for item in result.evidence
    ]
    evidence_rank_scores = [
        int(item.get("rank_score") or 0)
        for item in result.evidence
    ]
    evidence_rank_reasons = [
        list(item.get("rank_reasons") or [])
        for item in result.evidence
    ]
    answer_contract = result.answer_contract or {}
    conflict_resolution = intent.conflict_resolution or {}
    selected_route = str(conflict_resolution.get("selected_route") or "")
    conflict_reason = str(conflict_resolution.get("reason") or "")
    answer_contract_status = str(answer_contract.get("status") or "")
    coverage_reasons = list(answer_contract.get("coverage_reasons") or [])
    next_question_count = int(answer_contract.get("next_question_count") or 0)
    suggested_route_count_value = answer_contract.get("suggested_route_count")
    suggested_route_count = (
        len(result.suggested_routes)
        if suggested_route_count_value is None
        else int(suggested_route_count_value)
    )
    suggested_routes = list(
        answer_contract.get("suggested_routes") or result.suggested_routes
    )
    evidence_diagnostics = list(
        answer_contract.get("evidence_diagnostics") or []
    )
    evidence_diagnostic_count_value = answer_contract.get(
        "evidence_diagnostic_count"
    )
    evidence_diagnostic_count = (
        len(evidence_diagnostics)
        if evidence_diagnostic_count_value is None
        else int(evidence_diagnostic_count_value)
    )
    return [
        {
            "analyst_workbench_live_wiring": {
                "stage": "analyst_workbench",
                "analyst_intent": intent.analyst_intent,
                "status": result.status,
                "selected_route": selected_route,
                "conflict_reason": conflict_reason,
                "evidence_count": len(result.evidence),
                "evidence_types": evidence_types,
                "evidence_labels": evidence_labels,
                "evidence_rank_scores": evidence_rank_scores,
                "evidence_rank_reasons": evidence_rank_reasons,
                "evidence_diagnostics": evidence_diagnostics,
                "evidence_diagnostic_count": evidence_diagnostic_count,
                "coverage_level": result.coverage_level,
                "overclaim_guard_applied": bool(
                    answer_contract.get("overclaim_guard_applied")
                ),
                "answer_contract_version": str(
                    answer_contract.get("version") or ""
                ),
                "answer_contract_status": answer_contract_status,
                "coverage_reasons": coverage_reasons,
                "next_question_count": next_question_count,
                "suggested_route_count": suggested_route_count,
                "suggested_routes": suggested_routes,
                "provider_called": result.provider_called,
                "actions_executed": result.actions_executed,
                "writes_attempted": False,
                "requires_operational_route": (
                    intent.requires_operational_route
                ),
                "operational_route_hint": intent.operational_route_hint,
            },
            "analyst_intent": intent.to_dict(),
            "result": {
                "status": result.status,
                "title": result.title,
                "evidence_count": len(result.evidence),
                "evidence_types": evidence_types,
                "evidence_labels": evidence_labels,
                "evidence_rank_scores": evidence_rank_scores,
                "evidence_rank_reasons": evidence_rank_reasons,
                "evidence_diagnostic_count": evidence_diagnostic_count,
                "coverage_level": result.coverage_level,
                "overclaim_guard_applied": bool(
                    answer_contract.get("overclaim_guard_applied")
                ),
                "answer_contract_version": str(
                    answer_contract.get("version") or ""
                ),
                "answer_contract_status": answer_contract_status,
                "coverage_reasons": coverage_reasons,
                "next_question_count": next_question_count,
                "suggested_route_count": suggested_route_count,
                "suggested_routes": suggested_routes,
                "exportable": False,
            },
        }
    ]
