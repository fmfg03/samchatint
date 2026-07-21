from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from .finance_query_intent import FinanceComparisonIntent
from .finance_query_service import (
    FinanceRowsProvider,
    run_read_only_comparison,
)
from .request_intent import OperationalRequestIntent
from .request_router import RequestRoute


@dataclass(frozen=True)
class RequestReportResult:
    status: str
    title: str
    summary: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    caveats: List[str]
    exportable: bool
    provider_called: bool
    actions_executed: List[str]
    canonical_action: Optional[str] = None
    raw_result: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


ReadOnlyActionExecutor = Callable[
    [str, Dict[str, Any]],
    Awaitable[Dict[str, Any]],
]


PUBLIC_ACTION_TITLES = {
    "receipts.pending_payment_overview": "Pagos pendientes",
    "receipts.cfdi_matching_overview": "CFDIs y gastos candidatos",
    "operations.tournament_soul_snapshot": "Estado operativo del torneo",
    "executive.realtime_report": "Reporte ejecutivo",
}


def _public_summary(
    action: Optional[str], data: Mapping[str, Any], fallback: str
) -> str:
    if action == "receipts.pending_payment_overview":
        pending = int(data.get("pending_count") or 0)
        total = float(data.get("total_pendiente") or 0)
        third_party = int(data.get("solicitud_terceros") or 0)
        personal = int(data.get("solicitud_personal") or 0)
        return (
            f"Hay {pending} solicitudes pendientes por ${total:,.2f}: "
            f"{third_party} de terceros y {personal} personales."
        )
    return fallback


def _finance_intent_from_operational(
    intent: OperationalRequestIntent,
) -> FinanceComparisonIntent:
    return FinanceComparisonIntent(
        metric=str(intent.slots.get("metric") or "gasto"),
        years=list(intent.slots.get("years") or []),
        group_by=str(intent.slots.get("group_by") or "concepto"),
        comparison=str(intent.slots.get("comparison") or "year_over_year"),
        raw_text=intent.raw_text,
    )


def _payload_for_intent(intent: OperationalRequestIntent) -> Dict[str, Any]:
    slots = intent.slots or {}
    filters = slots.get("filters") or {}
    if intent.domain == "finance":
        return {
            "question": intent.raw_text,
            "title": "Reporte financiero read-only",
            "group_by": slots.get("group_by") or "proyecto",
            "top_n": 12,
        }
    if intent.domain == "cfdi":
        view = "unlinked" if intent.intent == "list_unlinked" else "pending"
        return {"view": view, "limit": 50}
    if intent.domain == "payments":
        return {"actor_id": filters.get("actor_id"), "limit": 50}
    if intent.domain == "tournament":
        return {
            "tournament_name": filters.get("tournament_name"),
            "tournament_slug": filters.get("tournament_slug"),
            "include_communications": False,
            "include_media": False,
            "limit": 100,
        }
    if intent.domain == "executive":
        return {"question": intent.raw_text, "limit": 50}
    return {}


def _rows_from_mapping(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    for key in (
        "rows",
        "items",
        "pending",
        "matches",
        "documents",
        "tournaments",
    ):
        value = data.get(key)
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            return [dict(item) for item in value]
    breakdown = data.get("breakdown")
    if isinstance(breakdown, Mapping):
        items = breakdown.get("items")
        if isinstance(items, list) and all(isinstance(item, Mapping) for item in items):
            return [dict(item) for item in items]
    return []


def _columns(rows: List[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    for row in rows[:5]:
        for key in row.keys():
            if key not in columns:
                columns.append(str(key))
    return columns[:8]


async def run_read_only_report(
    *,
    intent: OperationalRequestIntent,
    route: RequestRoute,
    session: Any = None,
    finance_rows_provider: Optional[FinanceRowsProvider] = None,
    action_executor: Optional[ReadOnlyActionExecutor] = None,
) -> RequestReportResult:
    if route.type == "clarification":
        missing = ", ".join(intent.missing_fields or ["más contexto"])
        return RequestReportResult(
            status="needs_clarification",
            title="Necesito un dato más",
            summary=f"Necesito confirmar: {missing}. No ejecuté cambios.",
            columns=[],
            rows=[],
            caveats=[],
            exportable=False,
            provider_called=False,
            actions_executed=[],
            canonical_action=route.canonical_action,
            raw_result={"missing_fields": intent.missing_fields},
        )

    if route.type != "read_only_report":
        return RequestReportResult(
            status="unsupported",
            title="Solicitud no soportada",
            summary=(
                "No tengo una ruta determinística segura para esta solicitud. "
                "No ejecuté cambios."
            ),
            columns=[],
            rows=[],
            caveats=[route.reason],
            exportable=False,
            provider_called=False,
            actions_executed=[],
            canonical_action=route.canonical_action,
            raw_result={"reason": route.reason},
        )

    if route.canonical_action == "finance.read_only_comparison":
        finance_result = await run_read_only_comparison(
            intent=_finance_intent_from_operational(intent),
            session=session,
            rows_provider=finance_rows_provider,
        )
        status = (
            "data_source_unavailable"
            if finance_result.status == "unavailable"
            else finance_result.status
        )
        years = intent.slots.get("years") or []
        columns = ["concepto"]
        if len(years) == 2:
            columns.extend([f"gasto_{years[1]}", f"gasto_{years[0]}"])
        columns.extend(["diferencia", "variacion_pct"])
        rows = []
        for row in finance_result.rows:
            output_row: Dict[str, Any] = {
                "concepto": row.get("label"),
                "diferencia": row.get("difference"),
                "variacion_pct": row.get("variation_pct"),
            }
            if len(years) == 2:
                output_row[f"gasto_{years[1]}"] = row.get("amount_base_year")
                output_row[f"gasto_{years[0]}"] = row.get("amount_compare_year")
            rows.append(output_row)
        return RequestReportResult(
            status=status,
            title=(
                (
                    "Comparación de gasto por "
                    f"{intent.slots.get('group_by')}, "
                    f"{years[0]} vs {years[1]}"
                )
                if len(years) == 2
                else "Comparación de gasto"
            ),
            summary=finance_result.message,
            columns=columns,
            rows=rows,
            caveats=[finance_result.caveat] if finance_result.caveat else [],
            exportable=finance_result.exportable and bool(rows),
            provider_called=False,
            actions_executed=[],
            canonical_action=route.canonical_action,
            raw_result=finance_result.to_dict(),
        )

    if action_executor is None:
        return RequestReportResult(
            status="data_source_unavailable",
            title="Fuente read-only no disponible",
            summary=(
                "La solicitud fue reconocida, pero no hay un executor "
                "read-only disponible para consultar la fuente canónica. "
                "No ejecuté cambios."
            ),
            columns=[],
            rows=[],
            caveats=[route.reason],
            exportable=False,
            provider_called=False,
            actions_executed=[],
            canonical_action=route.canonical_action,
            raw_result={"reason": "read_only_executor_missing"},
        )

    try:
        executed = await action_executor(
            route.canonical_action or "",
            _payload_for_intent(intent),
        )
    except Exception as exc:
        return RequestReportResult(
            status="data_source_unavailable",
            title="Fuente read-only no disponible",
            summary=(
                f"La ruta read-only no pudo responder: {exc}. " "No ejecuté cambios."
            ),
            columns=[],
            rows=[],
            caveats=[route.reason],
            exportable=False,
            provider_called=False,
            actions_executed=[],
            canonical_action=route.canonical_action,
            raw_result={"error": str(exc)},
        )

    data = dict(executed.get("data") or executed)
    rows = _rows_from_mapping(data)
    status = "success" if rows or data else "empty"
    public_title = str(
        data.get("title")
        or PUBLIC_ACTION_TITLES.get(route.canonical_action or "")
        or "Reporte de operación"
    )
    fallback_summary = str(
        data.get("summary") or executed.get("summary") or "Reporte generado."
    )
    return RequestReportResult(
        status=status,
        title=public_title,
        summary=_public_summary(
            route.canonical_action,
            data,
            fallback_summary,
        ),
        columns=_columns(rows),
        rows=rows,
        caveats=[],
        exportable=status == "success" and bool(rows),
        provider_called=False,
        actions_executed=[],
        canonical_action=route.canonical_action,
        raw_result=data,
    )
