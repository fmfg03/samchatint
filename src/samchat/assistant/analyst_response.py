from __future__ import annotations

from typing import Any, Dict, List

from .analyst_intent import AnalystIntent
from .analyst_workbench import AnalystWorkbenchResult


def render_analyst_result(result: AnalystWorkbenchResult) -> str:
    lines = [result.title, "", result.answer]
    if result.evidence:
        lines.extend(["", "Evidencia usada:"])
        for item in result.evidence:
            label = str(
                item.get("label") or item.get("source_type") or "contexto"
            )
            summary = str(item.get("summary") or "")
            lines.append(f"- {label}: {summary}")
    if result.caveats:
        lines.extend(["", "Caveats:"])
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
    evidence_rank_scores = [
        int(item.get("rank_score") or 0)
        for item in result.evidence
    ]
    evidence_rank_reasons = [
        list(item.get("rank_reasons") or [])
        for item in result.evidence
    ]
    return [
        {
            "analyst_workbench_live_wiring": {
                "stage": "analyst_workbench",
                "analyst_intent": intent.analyst_intent,
                "status": result.status,
                "evidence_count": len(result.evidence),
                "evidence_types": evidence_types,
                "evidence_rank_scores": evidence_rank_scores,
                "evidence_rank_reasons": evidence_rank_reasons,
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
                "evidence_rank_scores": evidence_rank_scores,
                "evidence_rank_reasons": evidence_rank_reasons,
                "exportable": False,
            },
        }
    ]
