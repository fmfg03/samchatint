from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
)

from sqlalchemy import extract, func, select

from devnous.gastos.models import ExpenseReport

from .finance_query_intent import FinanceComparisonIntent


FinanceRowsProvider = Callable[
    [FinanceComparisonIntent], Awaitable[Iterable[Mapping[str, Any]]]
]


@dataclass(frozen=True)
class FinanceComparisonRow:
    label: str
    amount_base_year: float
    amount_compare_year: float
    difference: float
    variation_pct: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinanceComparisonResult:
    status: str
    message: str
    intent: Dict[str, Any]
    rows: List[Dict[str, Any]]
    exportable: bool
    source: str
    caveat: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _variation_pct(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)


def _label_from_row(row: Mapping[str, Any], group_by: str) -> str:
    value = (
        row.get(group_by)
        or row.get("label")
        or row.get("concepto")
        or row.get("category")
        or row.get("account")
    )
    return str(value or f"(sin {group_by})")


def _amount_from_row(row: Mapping[str, Any]) -> float:
    return _money(
        row.get("amount")
        or row.get("monto")
        or row.get("gasto_cantidad")
        or row.get("total")
    )


def build_comparison_rows(
    *,
    intent: FinanceComparisonIntent,
    source_rows: Iterable[Mapping[str, Any]],
) -> List[FinanceComparisonRow]:
    if len(intent.years) != 2:
        return []
    current_year, base_year = intent.years[0], intent.years[1]
    grouped: Dict[str, Dict[int, float]] = {}
    for row in source_rows:
        try:
            year = int(row.get("year") or row.get("anio") or row.get("año"))
        except (TypeError, ValueError):
            continue
        if year not in {base_year, current_year}:
            continue
        label = _label_from_row(row, intent.group_by)
        grouped.setdefault(label, {base_year: 0.0, current_year: 0.0})
        grouped[label][year] = round(
            grouped[label][year] + _amount_from_row(row),
            2,
        )

    rows: List[FinanceComparisonRow] = []
    for label, values in grouped.items():
        base_amount = _money(values.get(base_year))
        current_amount = _money(values.get(current_year))
        if base_amount == 0 and current_amount == 0:
            continue
        diff = round(current_amount - base_amount, 2)
        rows.append(
            FinanceComparisonRow(
                label=label,
                amount_base_year=base_amount,
                amount_compare_year=current_amount,
                difference=diff,
                variation_pct=_variation_pct(current_amount, base_amount),
            )
        )
    return sorted(rows, key=lambda item: abs(item.difference), reverse=True)


async def _read_expense_rows(
    *,
    session: Any,
    intent: FinanceComparisonIntent,
) -> List[Dict[str, Any]]:
    current_year, base_year = intent.years[0], intent.years[1]
    if intent.group_by == "account":
        label_col = ExpenseReport.cuenta_contable_id
    else:
        label_col = ExpenseReport.concepto
    year_expr = extract("year", ExpenseReport.fecha)
    result = await session.execute(
        select(
            year_expr.label("year"),
            label_col.label(intent.group_by),
            func.coalesce(
                func.sum(ExpenseReport.gasto_cantidad),
                0,
            ).label("amount"),
        )
        .where(
            ExpenseReport.estado_gasto != "cancelado",
            year_expr.in_([base_year, current_year]),
        )
        .group_by(year_expr, label_col)
    )
    return [
        {
            "year": int(row.year),
            intent.group_by: getattr(row, intent.group_by),
            "amount": _money(row.amount),
        }
        for row in result.all()
    ]


def render_finance_comparison_result(result: FinanceComparisonResult) -> str:
    if result.status != "success":
        return result.message
    intent = result.intent
    years = intent.get("years") or []
    current_year, base_year = years[0], years[1]
    lines = [
        (
            f"Comparación de gasto por {intent.get('group_by')}, "
            f"{current_year} vs {base_year}"
        ),
        "",
        f"| Concepto | {base_year} | {current_year} | Dif. | Var. % |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in result.rows:
        variation = (
            "N/A"
            if row.get("variation_pct") is None
            else f"{row.get('variation_pct'):.2f}%"
        )
        lines.append(
            (
                "| {label} | ${base:,.2f} | ${current:,.2f} | "
                "${diff:,.2f} | {variation} |"
            ).format(
                label=row.get("label") or "(sin concepto)",
                base=_money(row.get("amount_base_year")),
                current=_money(row.get("amount_compare_year")),
                diff=_money(row.get("difference")),
                variation=variation,
            )
        )
    if result.caveat:
        lines.extend(["", result.caveat])
    return "\n".join(lines)


async def run_read_only_comparison(
    *,
    intent: FinanceComparisonIntent,
    session: Any = None,
    rows_provider: Optional[FinanceRowsProvider] = None,
) -> FinanceComparisonResult:
    try:
        if rows_provider is not None:
            source_rows = list(await rows_provider(intent))
            source = "mocked_read_only_provider"
        elif session is not None:
            source_rows = await _read_expense_rows(
                session=session,
                intent=intent,
            )
            source = "gastos_expense_report_read_model"
        else:
            return FinanceComparisonResult(
                status="unavailable",
                message=(
                    "No encontré una fuente de datos financiera "
                    "disponible para "
                    "resolver esta comparación. No ejecuté cambios."
                ),
                intent=intent.to_dict(),
                rows=[],
                exportable=False,
                source="unavailable",
                caveat="",
            )
    except Exception:
        return FinanceComparisonResult(
            status="unavailable",
            message=(
                "No encontré una fuente de datos financiera disponible para "
                "resolver esta comparación. No ejecuté cambios."
            ),
            intent=intent.to_dict(),
            rows=[],
            exportable=False,
            source="unavailable",
            caveat="",
        )

    rows = [
        row.to_dict()
        for row in build_comparison_rows(
            intent=intent,
            source_rows=source_rows,
        )
    ]
    if not rows:
        years = intent.years
        return FinanceComparisonResult(
            status="empty",
            message=(
                "No encontré datos suficientes para comparar "
                f"gasto {years[0]} vs "
                f"{years[1]} por {intent.group_by}. No ejecuté cambios."
            ),
            intent=intent.to_dict(),
            rows=[],
            exportable=False,
            source=source,
            caveat="",
        )
    return FinanceComparisonResult(
        status="success",
        message="Comparación financiera generada.",
        intent=intent.to_dict(),
        rows=rows,
        exportable=True,
        source=source,
        caveat="Lectura read-only sobre datos de gastos disponibles.",
    )
