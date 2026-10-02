"""Executive report contracts from the supplied, externally inspected layouts.

Workbook example amounts are never data sources. Production mappings must be
explicit, source-owned and approved; missing classifications remain missing.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

GROUPS = (
    (8, "Generales"),
    (11, "Inscripción"),
    (14, "Cena / Bienvenida"),
    (17, "Fase Colectiva"),
    (20, "Estatal"),
    (23, "Nacional"),
    (26, "Viaje campeones"),
)
COLUMNS = (
    "budget_month",
    "budget_ytd",
    "actual_month",
    "actual_ytd",
    "variance_month",
    "variance_ytd",
)
COLUMN_LABELS = (
    "Presupuesto · mes",
    "Presupuesto · acumulado",
    "Real · mes",
    "Real · acumulado",
    "Variación · mes",
    "Variación · acumulado",
)


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _sum(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values, Decimal("0"))


def budget_layout(*, period: dict, source_rows: dict | None = None) -> dict:
    """Preserve all 26 rows and six measures; parents never double-count children.

    source_rows is an internal, already-scoped mapping keyed by stable row IDs.
    No HTTP input accepts these amounts. Per-cell None propagates to subtotals.
    """
    source_rows = source_rows or {}
    spec = [
        (6, "income", "Ingresos", None, "income"),
        (7, "direct", "Costos Directos", None, "cost"),
    ]
    for row, label in GROUPS:
        key = f"group-{row}"
        spec.append((row, key, label, None, "cost"))
        for offset in (1, 2):
            spec.append(
                (row + offset, f"{key}/part-{offset}", f"Partida {offset}", key, "cost")
            )
    spec += [
        (29, "broadcast", "Transmisión y RRSS", None, "cost"),
        (30, "sponsorship", "Patrocinios", None, "cost"),
        (31, "remainder", "Remanente", None, "income"),
    ]
    data = {
        key: {col: number(source_rows.get(key, {}).get(col)) for col in COLUMNS[:4]}
        for _, key, _, _, _ in spec
    }
    for col in COLUMNS[:4]:
        for row, _ in GROUPS:
            key = f"group-{row}"
            data[key][col] = _sum(data[f"{key}/part-{i}"][col] for i in (1, 2))
        data["direct"][col] = _sum(data[f"group-{row}"][col] for row, _ in GROUPS)
        outgo = _sum(data[key][col] for key in ("direct", "broadcast", "sponsorship"))
        income = data["income"][col]
        data["remainder"][col] = (
            income - outgo if income is not None and outgo is not None else None
        )
    rows = []
    for origin, key, label, parent, kind in spec:
        item = data[key]
        favorable, percentages = {}, {}
        for horizon in ("month", "ytd"):
            b, a = item[f"budget_{horizon}"], item[f"actual_{horizon}"]
            delta = b - a if b is not None and a is not None else None
            item[f"variance_{horizon}"] = delta
            favorable[horizon] = (
                None
                if delta is None
                else (
                    "neutral"
                    if delta == 0
                    else (
                        "favorable"
                        if (delta > 0) == (kind == "cost")
                        else "unfavorable"
                    )
                )
            )
            percentages[horizon] = (
                str(delta / abs(b) * 100) if delta is not None and b else None
            )
        rows.append(
            {
                "id": key,
                "source_row": origin,
                "label": source_rows.get(key, {}).get("label") or label,
                "parent": parent,
                "kind": kind,
                "values": {
                    k: str(v) if v is not None else None for k, v in item.items()
                },
                "favorability": favorable,
                "percentages": percentages,
            }
        )
    return {
        "rows": rows,
        "columns": list(COLUMNS),
        "column_labels": list(COLUMN_LABELS),
        "period": period,
        "unit": "MXN",
        "source": "Correspondencia explícita con fuentes canónicas; nunca importes de la plantilla",
        "gap": (
            "Falta correspondencia validada entre partidas del reporte y conceptos canónicos, plan mensual aprobado e ingresos atribuibles."
            if not source_rows
            else None
        ),
    }


def cashflow_layout(*, period: dict, periods: list[dict] | None = None) -> dict:
    """Explicit periods only; opening/closing are stocks, not YTD sums.

    Inputs are pesos. Display scaling happens once, here. Six origin and six
    application inputs are required for a complete period; blanks stay unknown.
    Each supplied period has its own explicit start/end and opening balance.
    """
    periods = periods or []
    if len(periods) > 6:
        raise ValueError("Cashflow comparison has six explicit period slots")
    result = []
    for entry in periods:
        if (
            not entry.get("start")
            or not entry.get("end")
            or entry["start"] > entry["end"]
        ):
            raise ValueError("Cashflow requires explicit ordered periods")
        date.fromisoformat(entry["start"])
        date.fromisoformat(entry["end"])
        origins = [number(v) for v in entry.get("origins", [])]
        applications = [number(v) for v in entry.get("applications", [])]
        if len(origins) != 6 or len(applications) != 6:
            raise ValueError("Cashflow requires six origins and six applications")
        opening = number(entry.get("opening"))
        # A contiguous monthly series carries its closing stock forward once.
        # Overlapping YTD/comparative windows never sum or chain their stocks.
        prior = next(
            (
                p
                for p in result
                if date.fromisoformat(p["end"]) + timedelta(days=1)
                == date.fromisoformat(entry["start"])
            ),
            None,
        )
        if prior and prior["closing"] is not None:
            carried = number(prior["closing"]) * 1000
            if opening is not None and opening != carried:
                raise ValueError("Cashflow opening does not reconcile with prior close")
            opening = carried
        incoming, outgoing = _sum(origins), _sum(applications)
        closing = (
            opening + incoming - outgoing
            if all(v is not None for v in (opening, incoming, outgoing))
            else None
        )
        scale = lambda value: str(value / 1000) if value is not None else None
        result.append(
            {
                "start": entry["start"],
                "end": entry["end"],
                "label": entry.get("label") or f"{entry['start']} a {entry['end']}",
                "opening": scale(opening),
                "origins": [scale(v) for v in origins],
                "applications": [scale(v) for v in applications],
                "origins_total": scale(incoming),
                "applications_total": scale(outgoing),
                "closing": scale(closing),
            }
        )
    return {
        "period": period,
        "periods": result,
        "unit": "Miles de MXN",
        "input_unit": "MXN",
        "source": "Saldo y movimientos bancarios conciliados, clasificados y atribuibles al mismo alcance",
        "gap": (
            "Tesorería: acreditar saldo inicial, cuenta/empresa, clasificación y atribución. No se distribuye caja empresarial entre torneos. Los comparativos requieren periodos explícitos."
            if not periods
            else None
        ),
    }


def report_layouts(period: dict) -> dict:
    """Missing mappings are actionable gaps, never documentary totals as cash."""
    return {
        "budget": budget_layout(period=period),
        "cashflow": cashflow_layout(period=period),
    }


def report_cell_evidence(layouts: dict, selection: dict) -> dict:
    """Resolve identifiers against signed report values; accept no client money."""
    report = selection.get("report")
    layout = layouts[report]
    key = selection.get("column")
    if report == "budget":
        if key not in COLUMNS:
            raise ValueError("Unknown report column")
        row = next((r for r in layout["rows"] if r["id"] == selection.get("row")), None)
        if row is None:
            raise ValueError("Unknown report row")
        value = row["values"][key]
        label = row["label"] + " · " + COLUMN_LABELS[COLUMNS.index(key)]
        definition = "Variación = presupuesto − real. Favorabilidad según ingreso o costo; porcentaje N/A sin base."
        end = layout["period"]["end"]
        period = (
            (end[:7] + "-01" if key.endswith("month") else end[:4] + "-01-01")
            + " a "
            + end
        )
    elif report == "cashflow":
        if key not in {"opening", "origins_total", "applications_total", "closing"}:
            raise ValueError("Unknown cashflow field")
        slot = int(selection.get("period", "0"))
        if not 0 <= slot < len(layout["periods"]):
            raise ValueError("Unknown cashflow period")
        entry = layout["periods"][slot]
        value = entry[key]
        label = {
            "opening": "Saldo inicial",
            "origins_total": "Orígenes",
            "applications_total": "Aplicaciones",
            "closing": "Saldo final",
        }[key]
        period = entry["start"] + " a " + entry["end"]
        definition = "Apertura + orígenes − aplicaciones = cierre. Los saldos son existencias; no se suman como acumulados."
    else:
        raise ValueError("Unknown report")
    parsed = number(value)
    return {
        "label": label,
        "value": value,
        "formatted_value": (
            f'{parsed:,.2f} {layout["unit"]}' if parsed is not None else "Sin dato"
        ),
        "period": period,
        "definition": definition,
        "source": layout["source"],
        "gap": layout.get("gap"),
    }
