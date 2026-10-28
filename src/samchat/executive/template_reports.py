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
    preserve_unknown: bool = False,
) -> list[float | None]:
    values = [0.0 for _ in range(12)]
    for bucket in buckets:
        month = _safe_int(bucket.get("month"))
        if 1 <= month <= 12:
            value = bucket.get(field)
            values[month - 1] = (
                None
                if preserve_unknown and value is None
                else _safe_float(_safe_float(value) / scale)
            )
    return values


def _row(label: str, values: list[float | None], note: str = "") -> dict[str, Any]:
    return {
        "segment": label,
        "months": values,
        "total": (
            None if any(value is None for value in values) else _safe_float(sum(values))
        ),
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
    """Preserve annual report shape without combining cash, forecasts or currencies.

    Only balances already projected by the source model are accepted. The legacy
    opening argument supplies no account, currency or coverage evidence by itself.
    """
    from decimal import Decimal, InvalidOperation

    def number(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value))
            return parsed if parsed.is_finite() else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    factor = number(scale)
    if factor is None or factor <= 0:
        raise ValueError("Cashflow scale must be finite and positive")
    payload = cashflow_payload if isinstance(cashflow_payload, dict) else {}
    raw_period = payload.get("period")
    period: dict[str, Any] = raw_period if isinstance(raw_period, dict) else {}
    year = _safe_int(as_of_year or period.get("year")) or None
    month = _safe_int(as_of_month or period.get("month")) or None
    notes = list(payload.get("source_notes") or [])
    notes.append(
        "bank_balance_evidence_required: apertura y cierre sólo se muestran con "
        "evidencia del modelo bancario; nunca se sustituye un faltante por cero."
    )
    if opening_balance is not None:
        notes.append("opening_balance_argument_not_evidence: importe aislado omitido.")
    candidates = []
    for bucket in payload.get("monthly_buckets") or []:
        if not isinstance(bucket, dict):
            continue
        key = str(bucket.get("key") or "")
        bucket_year = _safe_int(bucket.get("year"))
        bucket_month = _safe_int(bucket.get("month"))
        if not bucket_year and len(key) >= 7 and key[4:5] == "-":
            bucket_year = _safe_int(key[:4])
            bucket_month = _safe_int(key[5:7])
        if bucket_year != year or not 1 <= bucket_month <= 12:
            notes.append(
                "annual_statement_period_excluded: período fuera del año "
                "solicitado o sin año-mes acreditado."
            )
            continue
        code = str(bucket.get("currency") or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            code = "SIN_MONEDA"
        candidates.append((bucket_month, code, bucket))
    if not any(
        number(bucket.get("opening_balance")) is not None for _, _, bucket in candidates
    ):
        notes.append("missing_opening_bank_balance: apertura no acreditada.")
    selected = str(payload.get("currency") or "all").strip().upper()
    currencies = sorted({code for _, code, _ in candidates})
    if selected != "ALL" and len(selected) == 3 and selected.isalpha():
        currencies = [selected]
    if not currencies:
        currencies = ["SIN_MONEDA"]
    fields = (
        "opening_balance",
        "actual_cash_in",
        "collected_income",
        "expected_uncollected_income",
        "actual_cash_out",
        "approved_obligations",
        "closing_balance",
    )

    def report_for(currency):
        values: dict[str, list[Decimal | None]] = {
            field: [None] * 12 for field in fields
        }
        seen = set()
        duplicate = set()
        source_notes = list(notes)
        for bucket_month, code, bucket in candidates:
            if code != currency or currency == "SIN_MONEDA":
                continue
            index = bucket_month - 1
            if index in seen:
                duplicate.add(index)
                continue
            seen.add(index)
            for field in fields:
                amount = number(bucket.get(field))
                values[field][index] = amount / factor if amount is not None else None
        if duplicate:
            source_notes.append(
                "annual_statement_duplicate_period: períodos repetidos no se "
                "sobrescriben ni se agregan sin cobertura acreditada."
            )
            for field in fields:
                for index in duplicate:
                    values[field][index] = None
        if len(seen) != 12:
            source_notes.append(
                "annual_statement_partial_coverage: meses no acreditados y "
                "totales anuales incompletos permanecen N/D."
            )
        if currency in {None, "SIN_MONEDA"}:
            source_notes.append(
                "annual_statement_currency_unavailable: seleccione una moneda "
                "acreditada; no se combinan importes de monedas diferentes."
            )

        def row(label, field, note="", stock=None):
            months = values[field]
            total = (
                None
                if any(value is None for value in months)
                else sum((value for value in months if value is not None), Decimal("0"))
            )
            if stock:
                positions = sorted(seen)
                index = (
                    (positions[0] if stock == "opening" else positions[-1])
                    if positions
                    else None
                )
                total = months[index] if index is not None else None
            return {
                "segment": label,
                "months": list(months),
                "total": total,
                "note": note,
            }

        rows = [
            row(
                "SALDO INICIAL:",
                "opening_balance",
                "Saldo de apertura acreditado; total = primer corte, no suma.",
                stock="opening",
            ),
            row("Origen", "actual_cash_in", "Únicamente entradas bancarias reales."),
            row("Ingresos reales", "actual_cash_in"),
            row(
                "Cobranza comprobada",
                "collected_income",
                "Evidencia explicativa; no se suma al origen bancario.",
            ),
            row(
                "Ingreso esperado no cobrado",
                "expected_uncollected_income",
                "Proyección separada del efectivo real.",
            ),
            row(
                "Aplicaciones",
                "actual_cash_out",
                "Únicamente salidas bancarias reales.",
            ),
            row("Salidas reales", "actual_cash_out"),
            row(
                "Obligaciones aprobadas",
                "approved_obligations",
                "Compromisos separados; no son salidas bancarias realizadas.",
            ),
            row(
                "SALDO FINAL:",
                "closing_balance",
                "Saldo de cierre acreditado; total = último corte, no suma.",
                stock="closing",
            ),
        ]
        currency_label = currency if currency not in {None, "SIN_MONEDA"} else "N/D"
        unit = (
            f"miles de {currency_label}"
            if factor == 1000
            else (f"{currency_label}; factor de escala {factor}")
        )
        return {
            "ok": True,
            "read_only": True,
            "report_type": "cashflow_statement",
            "title": "Flujo de Efectivo",
            "subtitle": f"{_period_label(year, month)} (cifras en {unit})",
            "period": {
                "year": year,
                "month": month,
                "horizon_months": period.get("horizon_months"),
            },
            "currency": currency,
            "currency_scale": f"thousands_{currency.lower()}" if currency else None,
            "columns": ["Segmento", *MONTH_LABELS_ES, "Total"],
            "rows": rows,
            "summary": {
                "saldo_inicial": rows[0]["total"],
                "origen_total": rows[1]["total"],
                "aplicaciones_total": rows[5]["total"],
                "saldo_final": rows[-1]["total"],
            },
            "source_notes": list(dict.fromkeys(source_notes)),
            "safety_labels": [*REPORT_SAFETY_LABELS, "forecast_is_derived"],
        }

    reports = {currency: report_for(currency) for currency in currencies}
    result = (
        dict(next(iter(reports.values()))) if len(reports) == 1 else report_for(None)
    )
    result["currency_reports"] = reports
    return result


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
