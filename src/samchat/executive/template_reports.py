"""Read-only executive report templates.

These builders shape canonical finance payloads into spreadsheet-like
executive reports. They do not import workbook files as authority and they do
not execute business effects.
"""

from __future__ import annotations

from typing import Any


MONTH_LABELS_ES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]

REPORT_SAFETY_LABELS = [
    "read_only",
    "executive_template_report",
    "no_financial_effects",
    "source_model_is_authority",
]


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


def _period_label(year: int | None, month: int | None) -> str:
    clean_year = _safe_int(year)
    clean_month = _safe_int(month)
    month_label = (
        MONTH_LABELS_ES[clean_month - 1] if 1 <= clean_month <= 12 else "Periodo"
    )
    return f"Al {month_label} {clean_year}" if clean_year else month_label


def _month_values_from_buckets(
    buckets: list[dict[str, Any]],
    *,
    field: str,
    scale: float,
) -> list[float]:
    values = [0.0 for _ in range(12)]
    for bucket in buckets:
        month = _safe_int(bucket.get("month"))
        if 1 <= month <= 12:
            values[month - 1] = _safe_float(_safe_float(bucket.get(field)) / scale)
    return values


def _row(label: str, values: list[float], note: str = "") -> dict[str, Any]:
    return {
        "segment": label,
        "months": values,
        "total": _safe_float(sum(values)),
        "note": note,
    }


def build_cashflow_statement_report(
    cashflow_payload: dict[str, Any],
    *,
    as_of_year: int | None = None,
    as_of_month: int | None = None,
    opening_balance: float | None = None,
    scale: float = 1000.0,
) -> dict[str, Any]:
    """Build a CashFlow.xlsx-like report from the cashflow read model."""

    payload = cashflow_payload if isinstance(cashflow_payload, dict) else {}
    period = payload.get("period") if isinstance(payload.get("period"), dict) else {}
    year = _safe_int(as_of_year or period.get("year")) or None
    month = _safe_int(as_of_month or period.get("month")) or None
    buckets = [
        bucket
        for bucket in payload.get("monthly_buckets") or []
        if isinstance(bucket, dict)
    ]
    source_notes = list(payload.get("source_notes") or [])
    if opening_balance is None:
        opening_balance = 0.0
        source_notes.append(
            "missing_opening_bank_balance; saldo inicial mostrado como 0 hasta "
            "conectar saldo bancario de corte"
        )
    opening_scaled = _safe_float(opening_balance / scale)

    ingresos = _month_values_from_buckets(
        buckets, field="actual_cash_in", scale=scale
    )
    cobranza = _month_values_from_buckets(
        buckets, field="collected_income", scale=scale
    )
    ingreso_esperado = _month_values_from_buckets(
        buckets, field="expected_uncollected_income", scale=scale
    )
    salidas = _month_values_from_buckets(
        buckets, field="actual_cash_out", scale=scale
    )
    obligaciones = _month_values_from_buckets(
        buckets, field="approved_obligations", scale=scale
    )
    aplicaciones = [
        _safe_float(salidas[idx] + obligaciones[idx]) for idx in range(12)
    ]

    saldo_inicial: list[float] = []
    saldo_final: list[float] = []
    running = opening_scaled
    for idx in range(12):
        saldo_inicial.append(_safe_float(running))
        running = _safe_float(running + ingresos[idx] + cobranza[idx] - aplicaciones[idx])
        saldo_final.append(running)

    rows = [
        _row("SALDO INICIAL:", saldo_inicial, "Saldo inicial bancario de corte."),
        _row("Origen", [_safe_float(ingresos[i] + cobranza[i]) for i in range(12)]),
        _row("Ingresos reales", ingresos),
        _row("Cobranza comprobada", cobranza),
        _row(
            "Ingreso esperado no cobrado",
            ingreso_esperado,
            "Forecast derivado; no es cobranza probada.",
        ),
        _row("Aplicaciones", aplicaciones),
        _row("Salidas reales", salidas),
        _row("Obligaciones aprobadas", obligaciones),
        _row("SALDO FINAL:", saldo_final, "Saldo final derivado del reporte."),
    ]

    return {
        "ok": True,
        "read_only": True,
        "report_type": "cashflow_statement",
        "title": "Flujo de Efectivo",
        "subtitle": f"{_period_label(year, month)} (cifras en miles de pesos)",
        "period": {
            "year": year,
            "month": month,
            "horizon_months": period.get("horizon_months"),
        },
        "currency_scale": "thousands_mxn",
        "columns": ["Segmento", *MONTH_LABELS_ES, "Total"],
        "rows": rows,
        "summary": {
            "saldo_inicial": opening_scaled,
            "origen_total": rows[1]["total"],
            "aplicaciones_total": rows[5]["total"],
            "saldo_final": saldo_final[-1] if saldo_final else opening_scaled,
        },
        "source_notes": source_notes,
        "safety_labels": [*REPORT_SAFETY_LABELS, "forecast_is_derived"],
    }


def _monthly_amount(item: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return _safe_float(value)
    return 0.0


def _budget_rows_from_breakdowns(
    budget_snapshot: dict[str, Any],
    *,
    month: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    breakdowns = budget_snapshot.get("breakdowns") or {}
    source_items = breakdowns.get("by_phase") or breakdowns.get("by_concept") or []
    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in source_items:
        if not isinstance(item, dict):
            continue
        label = (
            item.get("label")
            or item.get("phase")
            or item.get("concept_name")
            or item.get("concepto")
            or "Sin segmento"
        )
        budget_accumulated = _safe_float(
            item.get("budget_total")
            or item.get("budget_amount")
            or item.get("presupuesto_2026")
        )
        real_accumulated = _safe_float(
            item.get("actual_total")
            or item.get("paid_total")
            or item.get("committed_total")
            or item.get("reference_total")
        )
        budget_month = _monthly_amount(
            item,
            "budget_month",
            "monthly_budget",
            f"budget_month_{month}" if month else "",
        )
        real_month = _monthly_amount(
            item,
            "real_month",
            "actual_month",
            f"actual_month_{month}" if month else "",
        )
        if month and not (budget_month or real_month):
            notes.append("missing_monthly_granularity")
        rows.append(
            {
                "segment": str(label),
                "budget_month": budget_month,
                "budget_accumulated": budget_accumulated,
                "real_month": real_month,
                "real_accumulated": real_accumulated,
                "variance_month": _safe_float(budget_month - real_month),
                "variance_accumulated": _safe_float(
                    budget_accumulated - real_accumulated
                ),
            }
        )
    return rows, notes


def build_budget_vs_actual_report(
    budget_snapshot: dict[str, Any],
    *,
    month: int | None = None,
    year: int | None = None,
    month_name: str | None = None,
    accumulated_label: str | None = None,
) -> dict[str, Any]:
    """Build a Dashboard Presupuesto vs Real-like report from budget snapshot."""

    snapshot = budget_snapshot if isinstance(budget_snapshot, dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    period_year = _safe_int(year or summary.get("edition_year")) or None
    period_month = _safe_int(month) or None
    month_label = (
        month_name
        or (MONTH_LABELS_ES[period_month - 1] if 1 <= period_month <= 12 else "Mes")
    )
    accumulated = accumulated_label or (
        f"Enero-{month_label}" if 1 <= (period_month or 0) <= 12 else "Acumulado"
    )

    rows, notes = _budget_rows_from_breakdowns(snapshot, month=period_month)
    if not rows:
        budget_total = _safe_float(summary.get("budget_total"))
        real_total = _safe_float(summary.get("actual_total") or summary.get("paid_total"))
        rows = [
            {
                "segment": "Presupuesto",
                "budget_month": 0.0,
                "budget_accumulated": budget_total,
                "real_month": 0.0,
                "real_accumulated": real_total,
                "variance_month": 0.0,
                "variance_accumulated": _safe_float(budget_total - real_total),
            }
        ]
        notes.append("missing_breakdown_granularity")

    budget_accumulated_total = _safe_float(
        sum(_safe_float(row.get("budget_accumulated")) for row in rows)
    )
    real_accumulated_total = _safe_float(
        sum(_safe_float(row.get("real_accumulated")) for row in rows)
    )
    budget_month_total = _safe_float(
        sum(_safe_float(row.get("budget_month")) for row in rows)
    )
    real_month_total = _safe_float(sum(_safe_float(row.get("real_month")) for row in rows))

    source_notes = list(snapshot.get("source_notes") or [])
    for note in notes:
        if note and note not in source_notes:
            source_notes.append(note)

    return {
        "ok": True,
        "read_only": True,
        "report_type": "budget_vs_actual",
        "title": "Presupuesto vs Real",
        "subtitle": f"{month_label} / {accumulated}",
        "period": {"year": period_year, "month": period_month},
        "currency_scale": "mxn",
        "columns": [
            "Segmento",
            f"Presupuesto {month_label}",
            f"Presupuesto {accumulated}",
            f"Real {month_label}",
            f"Real {accumulated}",
            f"Variación {month_label}",
            f"Variación {accumulated}",
        ],
        "rows": rows,
        "summary": {
            "budget_month_total": budget_month_total,
            "budget_accumulated_total": budget_accumulated_total,
            "real_month_total": real_month_total,
            "real_accumulated_total": real_accumulated_total,
            "variance_month_total": _safe_float(budget_month_total - real_month_total),
            "variance_accumulated_total": _safe_float(
                budget_accumulated_total - real_accumulated_total
            ),
        },
        "source_notes": source_notes,
        "safety_labels": [*REPORT_SAFETY_LABELS, "budget_authority_stays_in_source"],
    }
