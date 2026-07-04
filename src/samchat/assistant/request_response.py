from __future__ import annotations

from typing import Any, Dict, List

from .request_intent import OperationalRequestIntent
from .request_reports import RequestReportResult
from .request_router import RequestRoute, build_request_contract


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if abs(value) >= 100:
            return f"{value:,.2f}"
        return f"{value:.2f}"
    return str(value)


def _markdown_table(columns: List[str], rows: List[Dict[str, Any]]) -> str:
    if not columns or not rows:
        return ""
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows[:25]:
        lines.append("| " + " | ".join(_format_value(row.get(col)) for col in columns) + " |")
    return "\n".join(lines)


def render_request_report(
    *,
    intent: OperationalRequestIntent,
    route: RequestRoute,
    result: RequestReportResult,
) -> str:
    if result.status == "success":
        parts = [result.title.strip(), ""]
        table = _markdown_table(result.columns, result.rows)
        if table:
            parts.append(table)
        else:
            parts.append(result.summary)
        if result.caveats:
            parts.extend(["", *[item for item in result.caveats if item]])
        return "\n".join(part for part in parts if part != "")

    if result.status == "empty":
        return f"{result.summary}\nNo ejecuté cambios."

    if result.status == "needs_clarification":
        return result.summary

    if result.status == "data_source_unavailable":
        return result.summary

    if result.status == "unsupported":
        return result.summary

    return "No pude resolver esta solicitud de forma determinística. No ejecuté cambios."


def build_request_trace(
    *,
    intent: OperationalRequestIntent,
    route: RequestRoute,
    result: RequestReportResult,
) -> List[Dict[str, Any]]:
    trace_result: Dict[str, Any] = {
        "status": result.status,
        "title": result.title,
        "row_count": len(result.rows),
        "exportable": result.exportable,
        "columns": result.columns,
    }
    if result.exportable and result.rows:
        trace_result["rows"] = result.rows
    return [
        {
            "request_intelligence_live_wiring": {
                "stage": "deterministic_request_routing",
                "domain": intent.domain,
                "intent": intent.intent,
                "confidence": intent.confidence,
                "canonical_action": route.canonical_action,
                "status": result.status,
                "provider_called": result.provider_called,
                "actions_executed": result.actions_executed,
                "writes_attempted": False,
            },
            "request_contract": build_request_contract(intent=intent, route=route),
            "tool": route.canonical_action or "request_intelligence",
            "result": trace_result,
        }
    ]
