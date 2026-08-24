"""Executive Finance/Accounting Q&A for the SamChat assistant.

This module is deliberately deterministic and read-only. It turns broad
director/accounting questions into a small set of canonical finance-platform
views, then renders a human executive answer with evidence, gaps and module
routes. It must never imply authority or execute a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .request_intent import normalize_request_text


@dataclass(frozen=True)
class FinanceAccountingQAIntent:
    question_type: str
    confidence: float
    reason: str


FINANCE_ACCOUNTING_QA_ROUTES: dict[str, str] = {
    "accounting_close": "/admin/contabilidad/cierres",
    "unbalanced_policies": "/admin/contabilidad/coi",
    "missing_cfdi": "/admin/gastos/cfdis/matching",
    "payment_run": "/admin/finanzas/payment-run",
    "amex_missing_cfdi": "/admin/gastos/amex/conciliacion",
    "coi_missing": "/admin/gastos/sin-cuenta-contable",
    "diot_blockers": "/admin/gastos/cfdis/matching",
    "accounting_loaded": "/admin/finanzas",
}


def detect_finance_accounting_qa_intent(
    raw_message: str,
) -> Optional[FinanceAccountingQAIntent]:
    """Detect Slice 9 finance/accounting questions."""

    normalized = normalize_request_text(raw_message)
    if not normalized:
        return None

    if "payment run" in normalized or "programacion de pagos" in normalized:
        return FinanceAccountingQAIntent(
            "payment_run", 0.95, "explicit_payment_run_question"
        )
    if "amex" in normalized and any(
        token in normalized
        for token in ("sin cfdi", "faltan cfdi", "falta cfdi", "cfdi")
    ):
        return FinanceAccountingQAIntent(
            "amex_missing_cfdi", 0.94, "explicit_amex_cfdi_question"
        )
    if "diot" in normalized:
        return FinanceAccountingQAIntent(
            "diot_blockers", 0.94, "explicit_diot_question"
        )
    if "coi" in normalized and any(
        token in normalized
        for token in ("falta", "faltan", "pendiente", "pendientes", "listo")
    ):
        return FinanceAccountingQAIntent(
            "coi_missing", 0.93, "explicit_coi_readiness_question"
        )
    if any(token in normalized for token in ("poliza", "polizas")) and any(
        token in normalized
        for token in ("no cuadran", "descuadrada", "descuadradas", "diferencia")
    ):
        return FinanceAccountingQAIntent(
            "unbalanced_policies", 0.95, "explicit_unbalanced_policy_question"
        )
    if "cfdi" in normalized and any(
        token in normalized
        for token in (
            "falta",
            "faltan",
            "sin vincular",
            "vincular",
            "pendiente",
            "pendientes",
        )
    ):
        return FinanceAccountingQAIntent(
            "missing_cfdi", 0.91, "explicit_cfdi_gap_question"
        )
    if any(
        token in normalized
        for token in (
            "cerrar contabilidad",
            "cerrar la contabilidad",
            "cierre contable",
            "cierre de contabilidad",
            "cierre mensual",
            "cerrar el mes",
        )
    ):
        return FinanceAccountingQAIntent(
            "accounting_close", 0.94, "explicit_accounting_close_question"
        )
    if any(
        token in normalized
        for token in (
            "contabilidad cargada",
            "contabilidad cargado",
            "tenemos contabilidad",
            "hay contabilidad",
            "estado de contabilidad",
            "status de contabilidad",
            "finanzas cargadas",
        )
    ):
        return FinanceAccountingQAIntent(
            "accounting_loaded", 0.89, "broad_accounting_status_question"
        )

    finance_terms = (
        "contabilidad",
        "contable",
        "coi",
        "poliza",
        "polizas",
        "finanzas",
        "cfdi",
        "cfdis",
        "pago",
        "pagos",
    )
    question_terms = (
        "tenemos",
        "hay",
        "esta",
        "cargada",
        "cargado",
        "estado",
        "status",
        "listo",
        "puedo cerrar",
        "por que no",
        "porque no",
        "que falta",
        "faltan",
        "pendiente",
        "pendientes",
    )
    if any(term in normalized for term in finance_terms) and any(
        term in normalized for term in question_terms
    ):
        return FinanceAccountingQAIntent(
            "accounting_loaded", 0.74, "broad_finance_accounting_question"
        )
    return None


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
    return f"${_safe_float(value):,.2f}"


def _text(value: Any, default: str = "—") -> str:
    text = str(value or "").strip()
    return text or default


def _item_label(row: dict[str, Any]) -> str:
    ref = _text(row.get("numero_referencia") or row.get("numero_poliza") or row.get("id"))
    name = _text(
        row.get("beneficiario_nombre")
        or row.get("proveedor_nombre")
        or row.get("empleado_nombre")
        or row.get("concepto"),
        "sin nombre",
    )
    amount = row.get("monto_total") or row.get("monto_solicitado") or row.get("gasto_cantidad")
    if amount is not None:
        return f"{ref} · {name} · {_money(amount)}"
    return f"{ref} · {name}"


def _policy_label(row: dict[str, Any]) -> str:
    number = _text(row.get("numero_poliza") or row.get("id"))
    kind = _text(row.get("tipo_poliza"), "póliza")
    delta = round(_safe_float(row.get("debe")) - _safe_float(row.get("haber")), 2)
    return f"{kind}-{number} · Debe {_money(row.get('debe'))} / Haber {_money(row.get('haber'))} · Diferencia {_money(delta)}"


def _lines_for_rows(rows: list[dict[str, Any]], *, policy: bool = False) -> list[str]:
    if not rows:
        return ["- Sin partidas visibles en el snapshot revisado."]
    labels = [_policy_label(row) if policy else _item_label(row) for row in rows[:5]]
    lines = [f"- {label}" for label in labels]
    if len(rows) > 5:
        lines.append(f"- … y {len(rows) - 5} más en el módulo correspondiente.")
    return lines


def _route_line(question_type: str) -> str:
    route = FINANCE_ACCOUNTING_QA_ROUTES.get(question_type, "/admin/finanzas")
    return f"Ruta sugerida para revisar: {route}"


def _source_notes(result: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for note in result.get("source_notes") or []:
        text = str(note or "").strip()
        if text and text not in notes:
            notes.append(text)
    return notes[:4]


def render_finance_accounting_qa_answer(
    *,
    result: dict[str, Any],
    intent: FinanceAccountingQAIntent,
) -> str:
    """Render a bounded, executive finance/accounting answer."""

    if not isinstance(result, dict) or not result.get("ok"):
        return "No pude consultar la fuente canónica de Finanzas en modo lectura. No ejecuté cambios."

    payload = result.get("payload") or {}
    summary = payload.get("summary") or {}
    accounting = payload.get("accounting_close_center") or {}
    tax = payload.get("tax_readiness") or {}
    payment_run = payload.get("payment_run") or {}
    period = payload.get("period") or {}

    documents = _safe_int(summary.get("documents"))
    expenses = _safe_int(summary.get("expenses"))
    policies = _safe_int(summary.get("polizas"))
    unbalanced = _safe_int(accounting.get("unbalanced_count"))
    coi_pending = _safe_int(accounting.get("pending_coi_expenses_count"))
    coi_ready = _safe_int(accounting.get("coi_ready_expenses_count"))
    diot_blockers = _safe_int(tax.get("diot_blockers_count"))
    missing_cfdi = _safe_int(tax.get("cfdi_missing_count"))
    amex_count = _safe_int(tax.get("amex_rows_count"))
    payable_count = _safe_int(payment_run.get("payable_count"))
    payable_total = _money(payment_run.get("payable_total"))

    question_type = intent.question_type
    lines: list[str]

    if question_type == "accounting_close":
        blockers = []
        if unbalanced:
            blockers.append(f"{unbalanced} pólizas descuadradas")
        if coi_pending:
            blockers.append(f"{coi_pending} gastos pendientes para COI")
        if diot_blockers:
            blockers.append(f"{diot_blockers} bloqueos DIOT/CFDI")
        if blockers:
            headline = "La contabilidad todavía no se debería cerrar."
            status = "Bloqueos detectados: " + "; ".join(blockers) + "."
        else:
            headline = "No detecté bloqueos principales para cierre en este snapshot."
            status = "Aun así, Finanzas debe validar el cierre mensual antes de cerrarlo."
        lines = [headline, status]
    elif question_type == "unbalanced_policies":
        rows = list(accounting.get("unbalanced_polizas") or [])
        lines = [
            f"Hay {unbalanced} pólizas descuadradas en el snapshot revisado.",
            "Evidencia visible:",
            *_lines_for_rows(rows, policy=True),
        ]
    elif question_type == "missing_cfdi":
        rows = list(tax.get("blockers") or [])
        lines = [
            f"Hay {missing_cfdi} documentos o gastos sin CFDI/UUID suficiente.",
            "Evidencia visible:",
            *_lines_for_rows(rows),
        ]
    elif question_type == "payment_run":
        rows = list(payment_run.get("items") or [])
        lines = [
            f"En Payment Run hay {payable_count} solicitudes autorizadas pendientes por {payable_total}.",
            f"Siguiente paso del módulo: {_text(payment_run.get('next_step'))}.",
            "Evidencia visible:",
            *_lines_for_rows(rows),
        ]
    elif question_type == "amex_missing_cfdi":
        blockers = [
            row
            for row in list(tax.get("blockers") or [])
            if "amex" in _text(row.get("metodo_pago") or row.get("origen"), "").lower()
        ]
        lines = [
            f"Hay {amex_count} gastos AMEX en el snapshot revisado.",
            f"De los bloqueos fiscales visibles, {len(blockers)} son AMEX sin CFDI/UUID suficiente.",
            "Evidencia visible:",
            *_lines_for_rows(blockers),
        ]
    elif question_type == "coi_missing":
        rows = list(accounting.get("pending_coi_expenses") or [])
        lines = [
            f"Para COI hay {coi_ready} gastos listos y {coi_pending} pendientes.",
            "Lo pendiente normalmente requiere cuenta/contracuenta y CFDI cuando aplique.",
            "Evidencia visible:",
            *_lines_for_rows(rows),
        ]
    elif question_type == "diot_blockers":
        rows = list(tax.get("blockers") or [])
        lines = [
            f"DIOT está en estado {_text(tax.get('status'))} con {diot_blockers} bloqueo(s) CFDI.",
            "Evidencia visible:",
            *_lines_for_rows(rows),
        ]
    else:
        if documents or expenses or policies:
            headline = "Sí hay información financiera/contable cargada en SamChat."
        else:
            headline = "No encontré información financiera/contable cargada en el snapshot canónico revisado."
        lines = [
            headline,
            f"Resumen: {documents} documentos, {expenses} gastos y {policies} pólizas.",
            f"COI: {coi_ready} listos, {coi_pending} pendientes.",
            f"Pólizas descuadradas: {unbalanced}.",
            f"DIOT/CFDI: {diot_blockers} bloqueo(s).",
            f"Payment Run: {payable_count} pendientes por {payable_total}.",
        ]

    period_line = f"Periodo revisado: {period.get('month') or '—'}/{period.get('year') or '—'}."
    source_lines = [
        "Fuente: finance.platform_snapshot read-only.",
        period_line,
    ]
    notes = _source_notes(result)
    if notes:
        source_lines.append("Notas de fuente:")
        source_lines.extend(f"- {note}" for note in notes)

    missing_lines = []
    if question_type in {"missing_cfdi", "diot_blockers", "amex_missing_cfdi"} and not missing_cfdi:
        missing_lines.append("No detecté CFDI faltante en el snapshot revisado.")
    if question_type == "unbalanced_policies" and not unbalanced:
        missing_lines.append("No detecté pólizas descuadradas en el snapshot revisado.")
    if question_type == "payment_run" and not payable_count:
        missing_lines.append("No detecté solicitudes pendientes en Payment Run.")
    if not missing_lines:
        missing_lines.append("Si necesitas detalle completo, abre la ruta del módulo; aquí sólo muestro el top visible del snapshot.")

    return "\n".join(
        [
            *lines,
            "",
            "Faltantes / límites:",
            *[f"- {line}" for line in missing_lines],
            "",
            "Fuentes y rutas:",
            *[f"- {line}" for line in source_lines],
            f"- {_route_line(question_type)}",
            "",
            "No ejecuté cambios; esta respuesta es sólo lectura.",
        ]
    )


__all__ = [
    "FINANCE_ACCOUNTING_QA_ROUTES",
    "FinanceAccountingQAIntent",
    "detect_finance_accounting_qa_intent",
    "render_finance_accounting_qa_answer",
]