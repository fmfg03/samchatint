from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from .document_confirmation import AsyncActionRouterExecutor
from .finance_query_intent import FinanceComparisonIntent
from .finance_query_service import FinanceRowsProvider, run_read_only_comparison
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


ReadOnlyActionExecutor = AsyncActionRouterExecutor


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
        return {"actor_id": (slots.get("filters") or {}).get("actor_id"), "limit": 50}
    if intent.domain == "tournament":
        return {
            "tournament_name": (slots.get("filters") or {}).get("tournament_name"),
            "tournament_slug": (slots.get("filters") or {}).get("tournament_slug"),
            "include_communications": False,
            "include_media": False,
            "limit": 100,
        }
    if intent.domain == "executive":
        return {"question": intent.raw_text, "limit": 50}
    return {}


def _rows_from_mapping(data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    for key in ("rows", "items", "pending", "matches", "documents", "tournaments"):
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


def _format_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _payment_pending_overview_result(
    *,
    data: Mapping[str, Any],
    route: RequestRoute,
) -> Optional[RequestReportResult]:
    if route.canonical_action != "receipts.pending_payment_overview":
        return None
    if "pending_count" not in data and "total_pendiente" not in data:
        return None

    pending_count = int(data.get("pending_count") or 0)
    terceros = int(data.get("solicitud_terceros") or 0)
    personal = int(data.get("solicitud_personal") or 0)
    total_pendiente = data.get("total_pendiente") or 0

    if pending_count <= 0:
        return RequestReportResult(
            status="empty",
            title="Pagos pendientes",
            summary="No encontré pagos pendientes. No ejecuté cambios.",
            columns=[],
            rows=[],
            caveats=[],
            exportable=False,
            provider_called=False,
            actions_executed=[],
            canonical_action=route.canonical_action,
            raw_result=dict(data),
        )

    return RequestReportResult(
        status="success",
        title="Pagos pendientes",
        summary=(
            f"Encontré {pending_count} pagos pendientes. "
            f"Total pendiente: {_format_money(total_pendiente)}. No ejecuté cambios."
        ),
        columns=["tipo", "cantidad"],
        rows=[
            {"tipo": "Solicitudes de terceros", "cantidad": terceros},
            {"tipo": "Solicitudes personales", "cantidad": personal},
        ],
        caveats=[],
        exportable=True,
        provider_called=False,
        actions_executed=[],
        canonical_action=route.canonical_action,
        raw_result=dict(data),
    )


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
            summary="No tengo una ruta determinística segura para esta solicitud. No ejecuté cambios.",
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
        rows = [
            {
                "concepto": row.get("label"),
                f"gasto_{years[1]}": row.get("amount_base_year") if len(years) == 2 else None,
                f"gasto_{years[0]}": row.get("amount_compare_year") if len(years) == 2 else None,
                "diferencia": row.get("difference"),
                "variacion_pct": row.get("variation_pct"),
            }
            for row in finance_result.rows
        ]
        return RequestReportResult(
            status=status,
            title=(
                f"Comparación de gasto por {intent.slots.get('group_by')}, "
                f"{years[0]} vs {years[1]}"
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
                "La solicitud fue reconocida, pero no hay un executor read-only "
                "disponible para consultar la fuente canónica. No ejecuté cambios."
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
        executed = await action_executor(route.canonical_action or "", _payload_for_intent(intent))
    except Exception as exc:
        return RequestReportResult(
            status="data_source_unavailable",
            title="Fuente read-only no disponible",
            summary=f"La ruta read-only no pudo responder: {exc}. No ejecuté cambios.",
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
    payment_result = _payment_pending_overview_result(data=data, route=route)
    if payment_result is not None:
        return payment_result

    rows = _rows_from_mapping(data)
    status = "success" if rows or data else "empty"
    return RequestReportResult(
        status=status,
        title=str(data.get("title") or route.canonical_action or "Reporte read-only"),
        summary=str(data.get("summary") or executed.get("summary") or "Reporte read-only generado."),
        columns=_columns(rows),
        rows=rows,
        caveats=[],
        exportable=status == "success" and bool(rows),
        provider_called=False,
        actions_executed=[],
        canonical_action=route.canonical_action,
        raw_result=data,
    )
