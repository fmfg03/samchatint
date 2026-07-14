from __future__ import annotations

from typing import Any, Dict, List

from .analyst_intent import AnalystIntent
from .analyst_workbench import AnalystWorkbenchResult


def render_analyst_result(result: AnalystWorkbenchResult) -> str:
    lines = [result.title, "", "Respuesta:", result.answer]
    if result.evidence:
        lines.extend(["", "Soporte en evidencia:"])
        for item in result.evidence:
            label = str(
                item.get("label") or item.get("source_type") or "contexto"
            )
            summary = str(item.get("summary") or "")
            lines.append(f"- {label}: {summary}")
    if result.caveats:
        lines.extend(["", "Límites:"])
        for caveat in result.caveats:
            lines.append(f"- {caveat}")
    if result.next_questions:
        lines.extend(["", "Siguientes preguntas:"])
        for question in result.next_questions:
            lines.append(f"- {question}")
    if result.suggested_routes:
        lines.extend(["", "Ruta sugerida:"])
        for route in result.suggested_routes:
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
                "exportable": False,
            },
        }
    ]
