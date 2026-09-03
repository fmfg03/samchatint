"""Deterministic answer rendering for canonical assistant finance reads."""

from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _money(value: Any) -> str:
    amount = _safe_float(value)
    return f"${amount:,.2f}"


def _notes(result: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for source in (result.get("source_notes"), payload.get("source_notes")):
        for note in source or []:
            text = str(note or "").strip()
            if text and text not in notes:
                notes.append(text)
    return notes[:5]


def _matched_collection_totals(payload: dict[str, Any]) -> tuple[int, float]:
    count = 0
    total = 0.0
    for key in ("issued_linked", "issued_unlinked"):
        for item in payload.get(key) or []:
            if item.get("collection_status") != "matched_collected":
                continue
            count += 1
            total += _safe_float(
                item.get("collected_amount")
                or item.get("linked_income_amount")
                or item.get("issued_amount")
            )
    return count, _safe_float(total)


def _render_source_notes(result: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    notes = _notes(result, payload)
    if not notes:
        return []
    return ["Notas de fuente:"] + [f"- {note}" for note in notes]


def _render_error(result: dict[str, Any]) -> str:
    error = result.get("error") or {}
    code = str(error.get("code") or "unknown_error").strip()
    allowed = result.get("allowed_intents") or []
    lines = [
        "No pude responder esta consulta financiera con la fuente canónica read-only.",
        f"Error: {code}",
    ]
    if allowed:
        lines.append("Intents permitidos: " + ", ".join(str(item) for item in allowed))
    return "\n".join(lines)


def _render_ar_summary(result: dict[str, Any], payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    matched_count, matched_total = _matched_collection_totals(payload)
    lines = [
        "CxC AR read-only",
        f"- Ingreso esperado: {_money(summary.get('expected_income_total'))} "
        f"en {_safe_int(summary.get('expected_income_count'))} líneas.",
        f"- Ingreso reconocido/linkeado: {_money(summary.get('linked_income_total'))} "
        f"en {_safe_int(summary.get('issued_linked_count'))} CFDI vinculados.",
        f"- CFDI emitidos no vinculados: "
        f"{_money(summary.get('issued_unlinked_total'))} "
        f"en {_safe_int(summary.get('issued_unlinked_count'))} registros.",
        f"- Cobranza AR probada: {_money(matched_total)} "
        f"en {matched_count} matches aceptados.",
        f"- Cobranza desconocida: "
        f"{_safe_int(summary.get('collection_gap_count'))} gaps de cobranza.",
        f"- Gaps de matching: {_safe_int(summary.get('matching_gap_count'))}.",
    ]
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def _render_ar_prematching(result: dict[str, Any], payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "Pre-matching AR read-only",
        f"- Items AR revisados: {_safe_int(summary.get('ar_item_count'))}.",
        f"- Evidencia candidata candidate_match: "
        f"{_safe_int(summary.get('candidate_match_count'))}.",
        f"- Revisión manual requerida: "
        f"{_safe_int(summary.get('manual_match_required_count'))}.",
        f"- Cobranza desconocida: "
        f"{_safe_int(summary.get('collection_unknown_count'))}.",
        f"- Gaps de pagador: {_safe_int(summary.get('payer_gap_count'))}.",
        f"- Ingresos bancarios sin item AR candidato: "
        f"{_safe_int(summary.get('unmatched_bank_inflow_count'))}.",
        "candidate_match es evidencia candidata; no es cobranza AR probada.",
        "Este resultado no tiene autoridad de cobranza.",
    ]
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def _render_cashflow_summary(result: dict[str, Any], payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "Cashflow Planning read-only",
        f"- Caja real neta: {_money(summary.get('actual_cash_net'))}.",
        f"- Obligaciones aprobadas: {_money(summary.get('approved_obligations'))}.",
        f"- Ingreso reconocido: {_money(summary.get('recognized_income'))}.",
        f"- Cobranza AR probada: {_money(summary.get('collected_income'))}.",
        f"- Ingreso esperado no cobrado: "
        f"{_money(summary.get('expected_uncollected_income'))}.",
        f"- Forecast derivado: {_money(summary.get('forecast_net'))}.",
        "El forecast es derivado.",
        "Los candidatos AR no cuentan como cobrado.",
    ]
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def _render_template_report(result: dict[str, Any], payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("rows") or []
    title = str(payload.get("title") or "Reporte ejecutivo").strip()
    subtitle = str(payload.get("subtitle") or "").strip()
    lines = [title]
    if subtitle:
        lines.append(f"- Periodo: {subtitle}.")
    report_type = str(payload.get("report_type") or "")
    if report_type == "cashflow_statement":
        lines.extend(
            [
                f"- Saldo inicial: {_money(summary.get('saldo_inicial'))}.",
                f"- Origen total: {_money(summary.get('origen_total'))}.",
                f"- Aplicaciones total: {_money(summary.get('aplicaciones_total'))}.",
                f"- Saldo final derivado: {_money(summary.get('saldo_final'))}.",
            ]
        )
    elif report_type == "budget_vs_actual":
        lines.extend(
            [
                f"- Presupuesto acumulado: "
                f"{_money(summary.get('budget_accumulated_total'))}.",
                f"- Real acumulado: {_money(summary.get('real_accumulated_total'))}.",
                f"- Variación acumulada: "
                f"{_money(summary.get('variance_accumulated_total'))}.",
                f"- Variación del mes: {_money(summary.get('variance_month_total'))}.",
            ]
        )
    lines.append(f"- Filas exportables: {_safe_int(len(rows))}.")
    if rows:
        lines.append("Principales renglones:")
        for row in rows[:5]:
            if isinstance(row, dict):
                lines.append(f"- {row.get('segment') or 'Sin segmento'}")
    lines.append("Este reporte es una previsualización ejecutiva; no cambia datos.")
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def _render_budget_snapshot(result: dict[str, Any], payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    forecast = payload.get("forecast") or {}
    version = payload.get("version") or {}
    source = str(payload.get("source") or "").strip()
    lines = [
        "Presupuesto read-only",
        f"- Version: {version.get('name') or version.get('id') or 'sin version DB'}."
        if version
        else "- Version: sin version DB.",
        f"- Fuente: {source or 'no especificada'}.",
        f"- Presupuesto total: {_money(summary.get('budget_total'))}.",
        f"- Solicitado: {_money(summary.get('requested_total'))}.",
        f"- Comprometido: {_money(summary.get('committed_total'))}.",
        f"- Pagado: {_money(summary.get('paid_total'))}.",
        f"- Real/actual: {_money(summary.get('actual_total'))}.",
        f"- Pendiente por pagar: {_money(summary.get('pending_to_pay_total'))}.",
        f"- Varianza contra actual: {_money(summary.get('variance_vs_actual'))}.",
    ]
    health = forecast.get("health")
    if health:
        lines.append(f"- Forecast health: {health}.")
    if source and source != "budget_db":
        lines.append(
            "La fuente no es budget_db; tratala como fallback o referencia hasta "
            "verificar la version runtime."
        )
    lines.append("Este snapshot no autoriza cambios de presupuesto.")
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def _render_finance_platform(result: dict[str, Any], payload: dict[str, Any]) -> str:
    period = payload.get("period") or {}
    summary = payload.get("summary") or {}
    action_queue = payload.get("action_queue") or {}
    cash_control = payload.get("cash_control_center") or {}
    accounting_close = payload.get("accounting_close_center") or {}
    tax_readiness = payload.get("tax_readiness") or {}
    payment_run = payload.get("payment_run") or {}
    finance_brief = payload.get("finance_brief") or {}
    lines = [
        "Finance Platform read-only",
        f"- Periodo: {period.get('month') or '-'} / {period.get('year') or '-'}.",
        f"- Documentos: {_safe_int(summary.get('documents'))}.",
        f"- Gastos: {_safe_int(summary.get('expenses'))}.",
        f"- Polizas: {_safe_int(summary.get('polizas'))}.",
        f"- Acciones abiertas: {_safe_int(action_queue.get('open_count'))} "
        f"({_safe_int(action_queue.get('high_count'))} alta prioridad).",
        f"- Presion de pago: {cash_control.get('payment_pressure') or '-'}."
        f" Pagos AP pendientes: {_safe_int(payment_run.get('payable_count'))} "
        f"por {_money(payment_run.get('payable_total'))}.",
        f"- COI listo: "
        f"{_safe_int(accounting_close.get('coi_ready_expenses_count'))}; "
        f"COI pendiente: "
        f"{_safe_int(accounting_close.get('pending_coi_expenses_count'))}.",
        f"- Polizas descuadradas: "
        f"{_safe_int(accounting_close.get('unbalanced_count'))}.",
        f"- DIOT/CFDI blockers: "
        f"{_safe_int(tax_readiness.get('diot_blockers_count'))}; "
        f"tax status: {tax_readiness.get('status') or '-'}.",
        "Payment run es AP; no es cobranza AR.",
    ]
    brief = str(finance_brief.get("plain_text") or "").strip()
    if brief:
        lines.append("Brief financiero:")
        lines.append(brief)
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def _render_finance_exports(result: dict[str, Any], payload: dict[str, Any]) -> str:
    lines = [
        "Finance exports guidance read-only",
        "El asistente no genero archivo; cada export pertenece a su modulo dueno.",
    ]
    for item in payload.get("exports") or []:
        route = (
            item.get("route")
            or item.get("route_template")
            or item.get("route_family")
            or "-"
        )
        lines.append(
            f"- {item.get('id') or 'export'}: owner={item.get('owner') or '-'}; "
            f"status={item.get('status') or '-'}; route={route}; "
            f"artifact_class={item.get('artifact_class') or '-'}."
        )
        caveat = str(item.get("caveat") or "").strip()
        if caveat:
            lines.append(f"  Caveat: {caveat}")
    lines.extend(_render_source_notes(result, payload))
    return "\n".join(lines)


def render_finance_read_answer(result: dict[str, Any]) -> str:
    """Render a safe deterministic answer for assistant_finance_read results."""

    if not isinstance(result, dict) or not result.get("ok"):
        return _render_error(result if isinstance(result, dict) else {})

    intent = str(result.get("intent") or "").strip()
    payload = result.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if intent == "ar.summary":
        return _render_ar_summary(result, payload)
    if intent == "ar.prematching":
        return _render_ar_prematching(result, payload)
    if intent == "cashflow.summary":
        return _render_cashflow_summary(result, payload)
    if intent == "cashflow.statement":
        return _render_template_report(result, payload)
    if intent == "budget.snapshot":
        return _render_budget_snapshot(result, payload)
    if intent == "budget.vs_actual":
        return _render_template_report(result, payload)
    if intent == "finance.platform":
        return _render_finance_platform(result, payload)
    if intent == "finance.exports":
        return _render_finance_exports(result, payload)
    return _render_error(
        {
            "error": {"code": "unsupported_finance_intent"},
            "allowed_intents": [
                "ar.summary",
                "ar.prematching",
                "cashflow.summary",
                "cashflow.statement",
                "budget.snapshot",
                "budget.vs_actual",
                "finance.platform",
                "finance.exports",
            ],
        }
    )
