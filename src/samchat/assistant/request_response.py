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
        values = [_format_value(row.get(col)) for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_request_report(
    *,
    intent: OperationalRequestIntent,
    route: RequestRoute,
    result: RequestReportResult,
) -> str:
    if (
        result.status == "success"
        and result.canonical_action == "finance.read_only_comparison"
    ):
        years = intent.slots.get("years") or []
        current_year, base_year = years[0], years[1]
        lines = [
            (
                f"Comparación de gasto por {intent.slots.get('group_by')}, "
                f"{current_year} vs {base_year}"
            ),
            "",
            f"| Concepto | {base_year} | {current_year} | Dif. | Var. % |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in result.rows:
            variation_pct = row.get("variacion_pct")
            variation = (
                "N/A" if variation_pct is None else f"{variation_pct:.2f}%"
            )
            lines.append(
                "| {label} | ${base:,.2f} | ${current:,.2f} | "
                "${diff:,.2f} | {variation} |".format(
                    label=row.get("concepto") or "(sin concepto)",
                    base=float(row.get(f"gasto_{base_year}") or 0),
                    current=float(row.get(f"gasto_{current_year}") or 0),
                    diff=float(row.get("diferencia") or 0),
                    variation=variation,
                )
            )
        if result.caveats:
            lines.extend(["", *[item for item in result.caveats if item]])
        return "\n".join(lines)

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

    return (
        "No pude resolver esta solicitud de forma determinística. "
        "No ejecuté cambios."
    )


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
    trace: Dict[str, Any] = {
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
        "request_contract": build_request_contract(
            intent=intent,
            route=route,
        ),
        "tool": route.canonical_action or "request_intelligence",
        "result": trace_result,
    }
    if route.canonical_action == "finance.read_only_comparison":
        raw_result = result.raw_result or {}
        legacy_status = raw_result.get("status") or result.status
        trace["finance_query_live_wiring"] = {
            "stage": "deterministic_read_only_comparison",
            "metric": intent.slots.get("metric"),
            "years": intent.slots.get("years") or [],
            "group_by": intent.slots.get("group_by"),
            "comparison": intent.slots.get("comparison"),
            "status": legacy_status,
            "source": raw_result.get("source", "request_intelligence"),
            "row_count": len(result.rows),
            "provider_called": False,
            "writes_attempted": False,
        }
    return [trace]
