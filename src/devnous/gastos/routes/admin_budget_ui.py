"""Presupuestos dashboard and tournament detail UI helpers."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import date, timedelta
from html import escape
from typing import Any, Optional
from urllib.parse import quote

from samchat.budgets.service import BUDGET_WEEK_COUNT

GENERAL_PHASE_FILTER = "__general__"


def budget_dashboard_url(
    *,
    edition_year: Optional[int] = None,
    version_id: Optional[str] = None,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> str:
    params: list[str] = []
    for key, value in [
        ("edition_year", edition_year),
        ("version_id", version_id),
        ("success_msg", success_msg),
        ("error_msg", error_msg),
    ]:
        if value not in (None, ""):
            params.append(f"{key}={quote(str(value))}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"/admin/presupuestos{query}"


def budget_tournament_detail_url(
    tournament_key: str,
    *,
    edition_year: Optional[int] = None,
    version_id: Optional[str] = None,
    budget_view: Optional[str] = None,
    phase_filter: Optional[str] = None,
    show_committed: bool = True,
    show_yoy: bool = False,
    budget_period: Optional[str] = None,
    success_msg: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> str:
    params: list[str] = []
    for key, value in [
        ("edition_year", edition_year),
        ("version_id", version_id),
        ("budget_view", budget_view),
        ("phase_filter", phase_filter),
        ("show_committed", "1" if show_committed else "0"),
        ("show_yoy", "1" if show_yoy else "0"),
        ("budget_period", budget_period),
        ("success_msg", success_msg),
        ("error_msg", error_msg),
    ]:
        if value not in (None, ""):
            params.append(f"{key}={quote(str(value))}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"/admin/presupuestos/torneo/{quote(str(tournament_key))}{query}"


def budget_line_phase_key(line: dict[str, Any]) -> str:
    raw = str(line.get("phase") or "").strip()
    return raw if raw else GENERAL_PHASE_FILTER


def budget_line_phase_label(line: dict[str, Any]) -> str:
    raw = str(line.get("phase") or "").strip()
    return raw if raw else "General"


def filter_budget_lines_by_phase(
    lines: list[dict[str, Any]],
    phase_filter: Optional[str],
) -> list[dict[str, Any]]:
    clean = str(phase_filter or "").strip()
    if not clean:
        return lines
    if clean == GENERAL_PHASE_FILTER:
        return [
            line for line in lines if not str(line.get("phase") or "").strip()
        ]
    return [
        line
        for line in lines
        if str(line.get("phase") or "").strip() == clean
    ]


def collect_matrix_phase_filter_options(
    lines: list[dict[str, Any]],
    extra_labels: Optional[list[str]] = None,
) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in lines:
        key = budget_line_phase_key(line)
        if key in seen:
            continue
        seen.add(key)
        options.append((key, budget_line_phase_label(line)))
    for label in extra_labels or []:
        clean = str(label or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        options.append((clean, clean))
    return sorted(options, key=lambda item: item[1].lower())


def render_budget_matrix_filters(
    *,
    tournament_key: str,
    edition_year: int,
    version_id: str,
    all_versions: list[dict[str, Any]],
    phase_options: list[tuple[str, str]],
    selected_phase_filter: str = "",
    show_committed: bool = True,
    budget_view: str = "expenses",
    budget_period: str = "weekly",
    visible_count: int,
    total_count: int,
) -> str:
    year_values = sorted(
        {int(item.get("edition_year") or 0) for item in all_versions}
        or {edition_year}
    )
    year_options = "".join(
        f'<option value="{year}" {"selected" if year == edition_year else ""}>{year}</option>'
        for year in year_values
    )
    phase_filter_options = ['<option value="">Todas las fases</option>']
    for key, label in phase_options:
        selected_attr = " selected" if key == selected_phase_filter else ""
        phase_filter_options.append(
            f'<option value="{escape(key)}"{selected_attr}>{escape(label)}</option>'
        )
    if (
        selected_phase_filter
        and selected_phase_filter not in {key for key, _ in phase_options}
    ):
        phase_filter_options.append(
            f'<option value="{escape(selected_phase_filter)}" selected>'
            f"{escape(selected_phase_filter)}</option>"
        )
    count_note = (
        f"Mostrando {visible_count} de {total_count} partidas."
        if total_count
        else "Sin partidas en este año."
    )
    return f"""
    <form
        method="GET"
        action="/admin/presupuestos/torneo/{quote(str(tournament_key))}"
        style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;align-items:end;margin-bottom:14px;padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc;"
    >
        <input type="hidden" name="show_committed" value="{"1" if show_committed else "0"}">
        <input type="hidden" name="version_id" value="{escape(version_id)}">
        <input type="hidden" name="budget_view" value="{escape(budget_view)}">
        <input type="hidden" name="budget_period" value="{escape(budget_period)}">
        <div>
            <label for="matrix-edition-year" style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;">
                Año edición
            </label>
            <select id="matrix-edition-year" name="edition_year" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                {year_options}
            </select>
        </div>
        <div>
            <label for="matrix-phase-filter" style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;">
                Fase / subproyecto
            </label>
            <select id="matrix-phase-filter" name="phase_filter" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                {"".join(phase_filter_options)}
            </select>
        </div>
        <div>
            <button type="submit" class="button" style="width:100%;">Filtrar matriz</button>
        </div>
        <div style="grid-column:1/-1;font-size:12px;color:#64748b;line-height:1.55;">
            {count_note}
            La matriz partida × semana es la <strong>fuente única operativa</strong> para el año
            <strong>{edition_year}</strong>.
            Las partidas y cuentas contables provienen del catálogo SSOT compartido con
            solicitudes de transferencia y captura rápida de gastos.
        </div>
    </form>
    """


_EXECUTIVE_MONTH_LABELS_ES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


_BUDGET_EXECUTIVE_PERIODS: tuple[tuple[str, str], ...] = (
    ("weekly", "Semanal"),
    ("monthly", "Mensual"),
    ("quarterly", "Trimestral"),
    ("semester", "Semestral"),
    ("annual", "Anual"),
)


def _clean_budget_period(value: Optional[str]) -> str:
    clean = str(value or "weekly").strip().lower()
    return clean if clean in {key for key, _ in _BUDGET_EXECUTIVE_PERIODS} else "weekly"


def _budget_week_bounds(edition_year: int, week_number: int) -> tuple[date, date]:
    week = max(1, min(BUDGET_WEEK_COUNT, int(week_number)))
    jan1 = date(int(edition_year), 1, 1)
    dec31 = date(int(edition_year), 12, 31)
    if week == 1:
        start = jan1
    else:
        days_to_next_monday = 7 if jan1.isoweekday() == 1 else 8 - jan1.isoweekday()
        start = jan1 + timedelta(days=days_to_next_monday + ((week - 2) * 7))
    end = min(start + timedelta(days=6), dec31)
    return start, end


def _budget_period_bucket(edition_year: int, week_number: int, period: str) -> tuple[str, str]:
    start, end = _budget_week_bounds(edition_year, week_number)
    if period == "monthly":
        return f"{start.year}-{start.month:02d}", f"{_EXECUTIVE_MONTH_LABELS_ES[start.month]} {start.year}"
    if period == "quarterly":
        quarter = ((start.month - 1) // 3) + 1
        return f"{start.year}-Q{quarter}", f"T{quarter} {start.year}"
    if period == "semester":
        half = 1 if start.month <= 6 else 2
        return f"{start.year}-S{half}", f"Semestre {half} {start.year}"
    if period == "annual":
        return str(start.year), str(start.year)
    return f"week-{week_number:02d}", f"Semana {week_number} ({start.strftime('%d/%m')}–{end.strftime('%d/%m')})"


def _budget_execution_style(percent: float) -> str:
    if percent <= 90:
        return "color:#166534;font-weight:800;"
    if percent <= 105:
        return "color:#92400e;font-weight:800;"
    return "color:#991b1b;font-weight:800;"


def _budget_available_style(value: float, *, income_view: bool = False) -> str:
    if income_view:
        return "color:#166534;font-weight:800;" if value >= 0 else "color:#991b1b;font-weight:800;"
    return "color:#166534;font-weight:800;" if value >= 0 else "color:#991b1b;font-weight:800;"


def _unbudgeted_expense_actuals(
    lines: list[dict[str, Any]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
) -> dict[int, dict[str, float]]:
    """Aggregate expense movements whose concept has no line in this view."""
    matched_keys = {
        str(line.get("budget_concept_id") or "").strip() or "__unassigned__"
        for line in lines
    }
    unmatched: dict[int, dict[str, float]] = {}
    for concept_key, weeks in actuals_map.items():
        if concept_key in matched_keys:
            continue
        for week, values in weeks.items():
            bucket = unmatched.setdefault(
                week,
                {"real_expense_cash": 0.0, "committed_unpaid": 0.0},
            )
            bucket["real_expense_cash"] += float(
                values.get("real_expense_cash") or 0
            )
            bucket["committed_unpaid"] += float(
                values.get("committed_unpaid") or 0
            )
    return unmatched


def _sum_budget_line_periods(
    lines: list[dict[str, Any]],
    *,
    plan_map: dict[str, dict[int, dict[str, float]]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
    edition_year: int,
    period: str,
    budget_view: str,
) -> list[dict[str, Any]]:
    buckets: OrderedDict[str, dict[str, Any]] = OrderedDict()
    unbudgeted_actuals = _unbudgeted_expense_actuals(lines, actuals_map)
    for week in range(1, BUDGET_WEEK_COUNT + 1):
        bucket_key, bucket_label = _budget_period_bucket(edition_year, week, period)
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "label": bucket_label,
                "budget": 0.0,
                "real": 0.0,
                "committed": 0.0,
            }
        bucket = buckets[bucket_key]
        for line in lines:
            line_id = str(line.get("id") or "")
            concept_id = str(line.get("budget_concept_id") or "")
            actual_key = concept_id or "__unassigned__"
            plan = plan_map.get(line_id, {})
            actuals = actuals_map.get(actual_key, {})
            week_plan = plan.get(week, {})
            week_actual = actuals.get(week, {})
            if budget_view == "income":
                bucket["budget"] += float(week_plan.get("expected_income_amount") or 0)
                bucket["real"] += float(week_actual.get("real_income") or 0)
            else:
                bucket["budget"] += float(week_plan.get("budget_expense_amount") or 0)
                bucket["real"] += float(week_actual.get("real_expense_cash") or 0)
                bucket["committed"] += float(week_actual.get("committed_unpaid") or 0)
        if budget_view == "expenses":
            week_actual = unbudgeted_actuals.get(week, {})
            bucket["real"] += float(week_actual.get("real_expense_cash") or 0)
            bucket["committed"] += float(week_actual.get("committed_unpaid") or 0)
    return list(buckets.values())


def summarize_budget_actuals_for_lines(
    lines: list[dict[str, Any]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
) -> dict[str, float]:
    """Summarize expense actuals using the same concept matching as the detail."""
    real_total = 0.0
    committed_total = 0.0
    seen_actual_keys: set[str] = set()
    for line in lines:
        concept_id = str(line.get("budget_concept_id") or "").strip()
        actual_key = concept_id or "__unassigned__"
        if actual_key in seen_actual_keys:
            continue
        seen_actual_keys.add(actual_key)
        for values in actuals_map.get(actual_key, {}).values():
            real_total += float(values.get("real_expense_cash") or 0)
            committed_total += float(values.get("committed_unpaid") or 0)
    for concept_key, months in actuals_map.items():
        if concept_key in seen_actual_keys:
            continue
        for values in months.values():
            real_total += float(values.get("real_expense_cash") or 0)
            committed_total += float(values.get("committed_unpaid") or 0)
    return {
        "real_expense_total": round(real_total, 2),
        "committed_pending_total": round(committed_total, 2),
    }


def _budget_executive_status(execution: float) -> tuple[str, str, str]:
    if execution > 100:
        return (
            "Excedido",
            "#fee2e2",
            "#991b1b",
        )
    if execution >= 85:
        return (
            "En observación",
            "#fef3c7",
            "#92400e",
        )
    return (
        "Controlado",
        "#dcfce7",
        "#166534",
    )


def render_budget_executive_dashboard(
    lines: list[dict[str, Any]],
    *,
    plan_map: dict[str, dict[int, dict[str, float]]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
    tournament_key: str,
    edition_year: int,
    version_id: str,
    budget_view: str = "expenses",
    budget_period: str = "weekly",
    phase_filter: Optional[str] = None,
    show_committed: bool = True,
) -> str:
    """Render an executive rollup over the weekly budget matrix."""
    clean_period = _clean_budget_period(budget_period)
    clean_view = "income" if str(budget_view or "").lower() == "income" else "expenses"
    buckets = _sum_budget_line_periods(
        lines,
        plan_map=plan_map,
        actuals_map=actuals_map,
        edition_year=edition_year,
        period=clean_period,
        budget_view=clean_view,
    )
    total_budget = sum(float(item["budget"] or 0) for item in buckets)
    total_real = sum(float(item["real"] or 0) for item in buckets)
    total_committed = sum(float(item["committed"] or 0) for item in buckets)
    available = total_budget - total_real - (total_committed if clean_view == "expenses" else 0)
    execution = ((total_real + (total_committed if clean_view == "expenses" else 0)) / total_budget * 100) if total_budget else 0.0
    executive_status, status_bg, status_color = _budget_executive_status(execution)

    period_options = "".join(
        f'<option value="{escape(key)}" {"selected" if key == clean_period else ""}>{escape(label)}</option>'
        for key, label in _BUDGET_EXECUTIVE_PERIODS
    )
    rows: list[str] = []
    for item in buckets:
        budget = float(item["budget"] or 0)
        real = float(item["real"] or 0)
        committed = float(item["committed"] or 0)
        period_available = budget - real - (committed if clean_view == "expenses" else 0)
        period_execution = ((real + (committed if clean_view == "expenses" else 0)) / budget * 100) if budget else 0.0
        rows.append(
            f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #e2e8f0;font-weight:800;color:#0f172a;">{escape(str(item['label']))}</td>
                <td style="padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;">${budget:,.2f}</td>
                <td style="padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;">${real:,.2f}</td>
                <td style="padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;">{('$' + format(committed, ',.2f')) if clean_view == 'expenses' else '—'}</td>
                <td style="padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;{_budget_available_style(period_available, income_view=clean_view == "income")}">${period_available:,.2f}</td>
                <td style="padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;{_budget_execution_style(period_execution)}">{period_execution:.1f}%</td>
            </tr>
            """
        )
    if not rows:
        rows.append(
            '<tr><td colspan="6" style="padding:14px;color:#64748b;">Sin partidas para los filtros actuales.</td></tr>'
        )

    form_action = f"/admin/presupuestos/torneo/{quote(str(tournament_key))}"
    committed_hidden = "1" if show_committed else "0"
    phase_hidden = (
        f'<input type="hidden" name="phase_filter" value="{escape(str(phase_filter), quote=True)}">'
        if phase_filter
        else ""
    )
    view_label = "Ingresos" if clean_view == "income" else "Gastos"
    available_label = "Variación" if clean_view == "income" else "Disponible"
    return f"""
    <section class="workspace-card" id="tablero-ejecutivo-presupuesto" style="margin-bottom:18px;">
        <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap;">
            <div>
                <div class="workspace-section-title">Tablero ejecutivo de presupuesto</div>
                <div class="workspace-section-subtitle">Lectura ejecutiva de {escape(view_label.lower())}: ejercido real, comprometido pendiente y disponible contra presupuesto autorizado.</div>
            </div>
            <div style="display:grid;gap:6px;min-width:170px;padding:12px 14px;border:1px solid {status_bg};border-radius:12px;background:{status_bg};color:{status_color};">
                <span style="font-size:11px;font-weight:900;text-transform:uppercase;letter-spacing:.08em;">Estado ejecutivo</span>
                <strong style="font-size:1.05rem;">{executive_status}</strong>
            </div>
            <form method="GET" action="{form_action}" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap;">
                <input type="hidden" name="edition_year" value="{int(edition_year)}">
                <input type="hidden" name="version_id" value="{escape(str(version_id), quote=True)}">
                <input type="hidden" name="budget_view" value="{escape(clean_view, quote=True)}">
                <input type="hidden" name="show_committed" value="{committed_hidden}">
                {phase_hidden}
                <label style="display:grid;gap:4px;font-size:12px;font-weight:800;color:#475569;">
                    Periodo
                    <select name="budget_period" style="min-width:180px;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                        {period_options}
                    </select>
                </label>
                <button type="submit" class="button">Actualizar tablero</button>
            </form>
        </div>
        <div class="meta-grid" style="margin-top:14px;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));">
            <div class="meta-card"><span>Presupuesto autorizado</span><strong>${total_budget:,.2f}</strong></div>
            <div class="meta-card"><span>Ejercido real</span><strong>${total_real:,.2f}</strong></div>
            <div class="meta-card"><span>Comprometido pendiente</span><strong>{('$' + format(total_committed, ',.2f')) if clean_view == 'expenses' else '—'}</strong></div>
            <div class="meta-card"><span>{available_label}</span><strong>${available:,.2f}</strong></div>
            <div class="meta-card"><span>% utilizado</span><strong>{execution:.1f}%</strong></div>
        </div>
        <div style="overflow-x:auto;margin-top:14px;">
            <div class="workspace-section-title" style="font-size:14px;margin-bottom:8px;">Lectura por periodo</div>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
                <thead>
                    <tr style="background:#0f766e;color:#fff;">
                        <th style="padding:10px;text-align:left;">Periodo</th>
                        <th style="padding:10px;text-align:right;">Presupuesto autorizado</th>
                        <th style="padding:10px;text-align:right;">Ejercido real</th>
                        <th style="padding:10px;text-align:right;">Comprometido pendiente</th>
                        <th style="padding:10px;text-align:right;">{available_label}</th>
                        <th style="padding:10px;text-align:right;">% utilizado</th>
                    </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
    </section>
    """


def _variance_style(plan: float, actual: float) -> str:
    if plan <= 0 and actual <= 0:
        return "color:#64748b;"
    if actual <= plan:
        return "color:#166534;font-weight:700;"
    if actual <= plan * 1.05:
        return "color:#92400e;font-weight:700;"
    return "color:#991b1b;font-weight:700;"


def render_tournament_dashboard_cards(
    tournaments: list[dict[str, Any]],
    *,
    edition_year: int,
    version_id: Optional[str],
    tournament_rollups: dict[str, dict[str, float]],
) -> str:
    if not tournaments:
        return (
            '<div style="color:#64748b;padding:16px;">'
            "Sin torneos presupuestales para esta versión. "
            "Crea líneas o importa un presupuesto anual desde el detalle de torneo."
            "</div>"
        )
    cards: list[str] = []
    for item in tournaments:
        key = str(
            item.get("tournament_id")
            or item.get("tournament_code")
            or item.get("tournament_name")
            or ""
        )
        rollup_key = str(item.get("tournament_id") or item.get("tournament_code") or key)
        rollup = tournament_rollups.get(rollup_key, {})
        comparison = item.get("comparison") or {}
        budget_total = float(
            rollup.get("budget_expense_total") or item.get("budget_total") or 0
        )
        paid_total = float(
            rollup.get("real_expense_total")
            if rollup.get("real_expense_total") is not None
            else comparison.get("actual_total")
            if comparison.get("actual_total") is not None
            else comparison.get("paid_total") or 0
        )
        committed_total = float(
            rollup.get("committed_pending_total")
            if rollup.get("committed_pending_total") is not None
            else comparison.get("committed_total") or 0
        )
        used_total = paid_total + committed_total
        execution = (used_total / budget_total * 100) if budget_total else 0.0
        executive_status, status_bg, status_color = _budget_executive_status(execution)
        detail_url = budget_tournament_detail_url(
            key,
            edition_year=edition_year,
            version_id=version_id,
        )
        cards.append(
            f"""
            <a href="{escape(detail_url)}" style="text-decoration:none;color:inherit;">
            <div style="border:1px solid #dbe2ea;border-radius:14px;background:#fff;padding:14px;">
                <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                    <div>
                        <div style="font-size:16px;font-weight:800;color:#0f172a;">
                            {escape(str(item.get("tournament_name") or "Torneo"))}
                        </div>
                        <div style="font-size:12px;color:#64748b;">
                            {escape(str(item.get("tournament_code") or "sin código"))}
                            · {int(item.get("line_count") or 0)} partidas
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">
                            Presupuesto autorizado
                        </div>
                        <div style="font-size:18px;font-weight:800;color:#0f766e;">
                            ${budget_total:,.2f}
                        </div>
                    </div>
                </div>
                <div style="margin-top:12px;display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;">
                    <div style="display:grid;gap:4px;padding:10px 12px;border:1px solid {status_bg};border-radius:12px;background:{status_bg};color:{status_color};">
                        <span style="font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.08em;">Estado ejecutivo</span>
                        <strong style="font-size:14px;">{executive_status}</strong>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:#64748b;">% utilizado</div>
                        <div style="font-size:18px;font-weight:900;color:{status_color};">{execution:.1f}%</div>
                    </div>
                </div>
                <div style="margin-top:10px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;font-size:12px;">
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Ingreso esperado</div>
                        <div style="font-weight:800;">${float(rollup.get("expected_income_total") or 0):,.2f}</div>
                    </div>
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Ejercido real</div>
                        <div style="font-weight:800;">${paid_total:,.2f}</div>
                    </div>
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Ingreso real</div>
                        <div style="font-weight:800;">${float(rollup.get("real_income_total") or 0):,.2f}</div>
                    </div>
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Comprometido pendiente</div>
                        <div style="font-weight:800;">${committed_total:,.2f}</div>
                    </div>
                </div>
                <div style="margin-top:12px;display:flex;justify-content:flex-end;">
                    <span class="button" style="padding:8px 12px;font-size:12px;border-radius:10px;">
                        Abrir detalle &rarr;
                    </span>
                </div>
            </div>
            </a>
            """
        )
    return "".join(cards)


def _matrix_cuenta_display(line: dict[str, Any]) -> tuple[str, str, str]:
    cuenta_id = str(line.get("cuenta_contable_id") or "").strip()
    codigo = str(
        line.get("cuenta_contable_codigo") or line.get("account_code_final") or ""
    ).strip()
    nombre = str(line.get("cuenta_contable_nombre") or "").strip()
    if codigo and nombre:
        display = f"{codigo} - {nombre}"
    elif codigo:
        display = codigo
    elif nombre:
        display = nombre
    else:
        display = ""
    return cuenta_id, codigo, display


def _render_matrix_cuenta_field(
    line_id: str,
    line: dict[str, Any],
    *,
    can_edit: bool,
) -> str:
    cuenta_id, codigo, display = _matrix_cuenta_display(line)
    if not can_edit:
        readonly = escape(display or "—")
        return (
            f'<div style="font-size:12px;color:#64748b;margin-top:6px;">'
            f"Cuenta contable: {readonly}"
            f"</div>"
        )
    return f"""
        <div class="matrix-cuenta-field" data-line-id="{escape(line_id)}" style="position:relative;max-width:460px;margin-top:8px;">
            <label for="matrix-cuenta-search-{escape(line_id)}" style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:4px;">
                Cuenta contable
            </label>
            <input
                type="text"
                class="matrix-cuenta-search"
                id="matrix-cuenta-search-{escape(line_id)}"
                value="{escape(display)}"
                placeholder="Buscar por código o nombre..."
                autocomplete="off"
                style="width:100%;padding:8px 10px;border:1px solid #cbd5e1;border-radius:8px;font-size:12px;box-sizing:border-box;"
            >
            <input type="hidden" name="cuenta_contable_id" class="matrix-cuenta-id" value="{escape(cuenta_id)}">
            <input type="hidden" name="account_code_final" class="matrix-cuenta-codigo" value="{escape(codigo)}">
            <div class="matrix-cuenta-results" style="display:none;border:1px solid #cbd5e1;border-radius:8px;max-height:180px;overflow-y:auto;background:#fff;position:absolute;left:0;right:0;z-index:20;margin-top:4px;box-shadow:0 8px 24px rgba(15,23,42,0.12);"></div>
            <small style="display:block;margin-top:4px;color:#64748b;">Escribe código o nombre y selecciona la cuenta correcta.</small>
        </div>
    """


def _render_matrix_cuenta_search_script(cuentas_contables: list[dict[str, Any]]) -> str:
    cuentas_json = json.dumps(cuentas_contables, ensure_ascii=False)
    return f"""
    <script>
        (function() {{
            const cuentasContables = {cuentas_json};
            function bindMatrixCuentaField(field) {{
                const searchInput = field.querySelector(".matrix-cuenta-search");
                const resultsDiv = field.querySelector(".matrix-cuenta-results");
                const hiddenId = field.querySelector(".matrix-cuenta-id");
                const hiddenCodigo = field.querySelector(".matrix-cuenta-codigo");
                if (!searchInput || !resultsDiv || !hiddenId || !hiddenCodigo) return;

                function hideResults() {{
                    resultsDiv.style.display = "none";
                }}

                function selectCuenta(cuenta) {{
                    hiddenId.value = cuenta.id || "";
                    hiddenCodigo.value = cuenta.codigo || "";
                    searchInput.value = cuenta.codigo && cuenta.nombre
                        ? cuenta.codigo + " - " + cuenta.nombre
                        : (cuenta.codigo || cuenta.nombre || "");
                    hideResults();
                }}

                function normalizeCuentaSearchText(value) {{
                    return (value || "")
                        .toString()
                        .toLowerCase()
                        .normalize("NFD")
                        .replace(/[\u0300-\u036f]/g, "")
                        .replace(/[^a-z0-9]+/g, " ")
                        .trim()
                        .replace(/\\s+/g, " ");
                }}

                searchInput.addEventListener("input", function() {{
                    const query = normalizeCuentaSearchText(searchInput.value);
                    if (query.length < 1) {{
                        hideResults();
                        return;
                    }}
                    const filtered = cuentasContables.filter(function(c) {{
                        const searchableText = normalizeCuentaSearchText(
                            (c.codigo || "") + " " + (c.nombre || "") + " " + (c.tipo || "")
                        );
                        return searchableText.includes(query);
                    }}).slice(0, 50);
                    if (filtered.length === 0) {{
                        resultsDiv.innerHTML = '<div style="padding:10px;color:#94a3b8;">No se encontraron cuentas</div>';
                        resultsDiv.style.display = "block";
                        return;
                    }}
                    resultsDiv.innerHTML = filtered.map(function(c) {{
                        const label = (c.codigo || "") + " - " + (c.nombre || "");
                        return '<div class="matrix-cuenta-option" data-id="' + (c.id || "") + '" data-codigo="' + (c.codigo || "") + '" data-nombre="' + (c.nombre || "") + '" style="padding:10px;cursor:pointer;border-bottom:1px solid #eef2f7;">'
                            + '<strong>' + (c.codigo || "") + '</strong> - ' + (c.nombre || "")
                            + '<br><small style="color:#64748b;">Tipo: ' + (c.tipo || "") + '</small>'
                            + '</div>';
                    }}).join("");
                    resultsDiv.style.display = "block";
                    resultsDiv.querySelectorAll(".matrix-cuenta-option").forEach(function(option) {{
                        option.addEventListener("click", function() {{
                            selectCuenta({{
                                id: option.getAttribute("data-id"),
                                codigo: option.getAttribute("data-codigo"),
                                nombre: option.getAttribute("data-nombre"),
                            }});
                        }});
                    }});
                }});

                searchInput.addEventListener("focus", function() {{
                    if ((searchInput.value || "").trim().length >= 1) {{
                        searchInput.dispatchEvent(new Event("input"));
                    }}
                }});

                document.addEventListener("click", function(event) {{
                    if (!field.contains(event.target)) {{
                        hideResults();
                    }}
                }});
            }}

            document.querySelectorAll(".matrix-cuenta-field").forEach(bindMatrixCuentaField);
        }})();
    </script>
    """


def _actuals_for_budget_line(
    actuals_map: dict[str, dict[int, dict[str, float]]],
    concept_id: str,
) -> dict[int, dict[str, float]]:
    actual_key = concept_id or "__unassigned__"
    return actuals_map.get(actual_key, {})


def _actuals_has_expense_values(months: dict[int, dict[str, float]]) -> bool:
    for values in months.values():
        if float(values.get("real_expense_cash") or 0):
            return True
        if float(values.get("committed_unpaid") or 0):
            return True
    return False


def _render_budget_aggregate_matrix(
    lines: list[dict[str, Any]],
    *,
    plan_map: dict[str, dict[int, dict[str, float]]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
    edition_year: int,
    budget_period: str,
    matrix_mode: str,
    show_committed: bool,
) -> str:
    clean_view = "income" if matrix_mode == "income" else "expenses"
    period_labels: OrderedDict[str, str] = OrderedDict()
    for week in range(1, BUDGET_WEEK_COUNT + 1):
        key, label = _budget_period_bucket(edition_year, week, budget_period)
        period_labels.setdefault(key, label)
    header_cells = "".join(
        f'<th style="padding:8px;border-bottom:1px solid #e2e8f0;min-width:120px;white-space:nowrap;text-align:right;">{escape(label)}</th>'
        for label in period_labels.values()
    )

    def _aggregate_line_periods(
        *,
        plan: dict[int, dict[str, float]],
        actuals: dict[int, dict[str, float]],
    ) -> OrderedDict[str, dict[str, float]]:
        buckets: OrderedDict[str, dict[str, float]] = OrderedDict(
            (
                key,
                {
                    "budget": 0.0,
                    "real": 0.0,
                    "committed": 0.0,
                },
            )
            for key in period_labels
        )
        for week in range(1, BUDGET_WEEK_COUNT + 1):
            key, _ = _budget_period_bucket(edition_year, week, budget_period)
            bucket = buckets[key]
            week_plan = plan.get(week, {})
            week_actual = actuals.get(week, {})
            if clean_view == "income":
                bucket["budget"] += float(
                    week_plan.get("expected_income_amount") or 0
                )
                bucket["real"] += float(week_actual.get("real_income") or 0)
            else:
                bucket["budget"] += float(
                    week_plan.get("budget_expense_amount") or 0
                )
                bucket["real"] += float(
                    week_actual.get("real_expense_cash") or 0
                )
                bucket["committed"] += float(
                    week_actual.get("committed_unpaid") or 0
                )
        return buckets

    def _metric_row(
        label: str,
        values: OrderedDict[str, dict[str, float]],
        key: str,
        *,
        color: str = "#475569",
    ) -> str:
        total = sum(float(item.get(key) or 0) for item in values.values())
        cells = [
            f'<td style="padding:8px 10px;font-weight:800;color:{color};position:sticky;left:0;background:#fff;z-index:1;min-width:170px;">{escape(label)}</td>',
            f'<td style="padding:8px 10px;text-align:right;font-weight:900;position:sticky;left:170px;background:#fff;z-index:1;min-width:110px;">${total:,.2f}</td>',
        ]
        for item in values.values():
            amount = float(item.get(key) or 0)
            cells.append(
                f'<td style="padding:8px;text-align:right;color:{color};">${amount:,.2f}</td>'
            )
        return f"<tr>{''.join(cells)}</tr>"

    grouped: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        phase = budget_line_phase_label(line)
        grouped.setdefault(phase, []).append(line)

    cards: list[str] = []
    for phase, phase_lines in sorted(grouped.items()):
        cards.append(
            f'<div style="margin-top:18px;"><div style="font-weight:800;color:#0f172a;margin-bottom:8px;">'
            f"{escape(phase)}</div>"
        )
        for line in phase_lines:
            line_id = str(line.get("id") or "")
            concept_id = str(line.get("budget_concept_id") or "")
            values = _aggregate_line_periods(
                plan=plan_map.get(line_id, {}),
                actuals=_actuals_for_budget_line(actuals_map, concept_id),
            )
            rows = []
            if clean_view == "income":
                rows.append(_metric_row("Ingreso esperado", values, "budget"))
                rows.append(_metric_row("Ingreso real", values, "real"))
            else:
                rows.append(_metric_row("Presupuesto gasto", values, "budget"))
                rows.append(_metric_row("Gasto Real", values, "real"))
                if show_committed:
                    rows.append(
                        _metric_row(
                            "Comprometido no pagado",
                            values,
                            "committed",
                            color="#92400e",
                        )
                    )
            cards.append(
                f"""
                <div class="budget-excel-line-card" style="border:1px solid #dbe2ea;border-radius:14px;background:#fff;padding:12px;margin-bottom:12px;">
                    <div style="font-weight:900;color:#0f172a;margin-bottom:10px;">
                        {escape(str(line.get("concept_name") or "Partida"))}
                    </div>
                    <div class="budget-excel-grid" style="overflow-x:auto;border:1px solid #e2e8f0;border-radius:12px;">
                        <table style="width:max-content;min-width:100%;border-collapse:collapse;font-size:12px;">
                            <thead>
                                <tr style="background:#f8fafc;">
                                    <th style="text-align:left;padding:8px 10px;border-bottom:1px solid #e2e8f0;position:sticky;left:0;background:#f8fafc;z-index:2;min-width:170px;">Renglón</th>
                                    <th style="padding:8px 10px;border-bottom:1px solid #e2e8f0;position:sticky;left:170px;background:#f8fafc;z-index:2;min-width:110px;text-align:right;">Total</th>
                                    {header_cells}
                                </tr>
                            </thead>
                            <tbody>{''.join(rows)}</tbody>
                        </table>
                    </div>
                </div>
                """
            )
        cards.append("</div>")

    unbudgeted_actuals = _unbudgeted_expense_actuals(lines, actuals_map)
    if (
        clean_view == "expenses"
        and _actuals_has_expense_values(unbudgeted_actuals)
    ):
        values = _aggregate_line_periods(plan={}, actuals=unbudgeted_actuals)
        rows = [
            _metric_row("Gasto Real", values, "real"),
        ]
        if show_committed:
            rows.append(
                _metric_row(
                    "Comprometido no pagado",
                    values,
                    "committed",
                    color="#92400e",
                )
            )
        cards.append(
            f"""
            <div class="budget-excel-line-card" style="border:1px dashed #f59e0b;border-radius:14px;background:#fffbeb;padding:12px;margin-top:12px;width:100%;min-width:0;max-width:100%;box-sizing:border-box;overflow:hidden;">
                <div style="font-weight:900;color:#92400e;margin-bottom:4px;">Sin línea presupuestal</div>
                <div style="font-size:12px;color:#92400e;margin-bottom:10px;">
                    Movimientos sin una línea equivalente en la versión seleccionada; se muestran una sola vez y no son editables.
                </div>
                <div class="budget-excel-grid" style="overflow-x:auto;border:1px solid #fde68a;border-radius:12px;background:#fff;">
                    <table style="width:max-content;min-width:100%;border-collapse:collapse;font-size:12px;">
                        <thead>
                            <tr style="background:#fef3c7;">
                                <th style="text-align:left;padding:8px 10px;border-bottom:1px solid #fde68a;position:sticky;left:0;background:#fef3c7;z-index:2;min-width:170px;">Renglón</th>
                                <th style="padding:8px 10px;border-bottom:1px solid #fde68a;position:sticky;left:170px;background:#fef3c7;z-index:2;min-width:110px;text-align:right;">Total</th>
                                {header_cells}
                            </tr>
                        </thead>
                        <tbody>{''.join(rows)}</tbody>
                    </table>
                </div>
            </div>
            """
        )

    return f"""
        <div style="padding:12px;border:1px solid #dbe2ea;border-radius:12px;background:#f8fafc;color:#475569;font-size:13px;margin-bottom:12px;">
            Vista agregada por periodo. La edición granular permanece en Semanal.
            Los importes sin partida asignada se incluyen una sola vez.
        </div>
        {''.join(cards)}
    """


def _render_unbudgeted_expense_actuals_card(
    actuals: dict[int, dict[str, float]],
    *,
    edition_year: int,
) -> str:
    headers = "".join(
        f'<th title="{escape(_budget_period_bucket(edition_year, idx, "weekly")[1])}" style="padding:6px;border-bottom:1px solid #e2e8f0;min-width:88px;white-space:nowrap;">Semana {idx}</th>'
        for idx in range(1, BUDGET_WEEK_COUNT + 1)
    )

    def _actual_row(label: str, key: str, color: str) -> str:
        total = 0.0
        cells: list[str] = [
            f'<td style="padding:6px 10px;font-weight:700;color:#475569;position:sticky;left:0;background:#fff;z-index:1;min-width:150px;">{escape(label)}</td>'
        ]
        for week in range(1, BUDGET_WEEK_COUNT + 1):
            value = float(actuals.get(week, {}).get(key) or 0)
            total += value
            cells.append(
                f'<td style="padding:6px;text-align:right;{color}">${value:,.2f}</td>'
            )
        cells.insert(
            1,
            f'<td style="padding:6px 10px;text-align:right;font-weight:800;position:sticky;left:150px;background:#fff;z-index:1;min-width:96px;">${total:,.2f}</td>',
        )
        return f"<tr>{''.join(cells)}</tr>"

    return f"""
        <div class="budget-excel-line-card" style="border:1px dashed #f59e0b;border-radius:14px;background:#fffbeb;padding:12px;margin-bottom:12px;width:100%;min-width:0;max-width:100%;box-sizing:border-box;overflow:hidden;">
            <div style="font-weight:900;color:#92400e;margin-bottom:4px;">Sin línea presupuestal</div>
            <div style="font-size:12px;color:#92400e;margin-bottom:10px;">
                Movimientos sin una línea equivalente en la versión seleccionada; se muestran una sola vez y no son editables.
            </div>
            <div class="budget-excel-grid" style="overflow-x:auto;border:1px solid #fde68a;border-radius:12px;background:#fff;">
                <table style="width:max-content;min-width:100%;border-collapse:collapse;font-size:11px;">
                    <thead>
                        <tr style="background:#fef3c7;">
                            <th style="text-align:left;padding:6px 10px;border-bottom:1px solid #fde68a;position:sticky;left:0;background:#fef3c7;z-index:2;min-width:150px;">Renglón</th>
                            <th style="padding:6px 10px;border-bottom:1px solid #fde68a;position:sticky;left:150px;background:#fef3c7;z-index:2;min-width:96px;">Monto total</th>
                            {headers}
                        </tr>
                    </thead>
                    <tbody>
                        {_actual_row("Gasto Real", "real_expense_cash", "color:#475569;")}
                        {_actual_row("Comprometido no pagado", "committed_unpaid", "color:#92400e;")}
                    </tbody>
                </table>
            </div>
        </div>
    """


def _render_budget_movement_details(
    lines: list[dict[str, Any]],
    movements: list[dict[str, Any]],
    *,
    version_id: str,
    tournament_key: str,
    edition_year: int,
    phase_filter: Optional[str],
    can_edit: bool,
) -> str:
    line_concepts = {
        str(line.get("budget_concept_id") or "") for line in lines
    }
    visible: list[dict[str, Any]] = []
    for movement in movements:
        if movement.get("kind") == "ledger_income":
            continue
        concept_key = str(movement.get("concept_key") or "__unassigned__")
        if concept_key not in line_concepts or movement.get("kind") == "pending_accounting":
            visible.append(movement)
    if not visible:
        return ""

    rows: list[str] = []
    for movement in visible:
        concept_key = str(movement.get("concept_key") or "__unassigned__")
        has_existing_concept = concept_key != "__unassigned__"
        kind = str(movement.get("kind") or "")
        status = (
            "Pendiente de contabilización"
            if kind == "pending_accounting"
            else "Sin línea en esta versión"
        )
        account = str(movement.get("cuenta_codigo") or "Sin cuenta")
        poliza = str(movement.get("numero_poliza") or "Sin póliza")
        poliza_date = movement.get("fecha_poliza") or ""
        action = ""
        if can_edit and has_existing_concept and concept_key not in line_concepts:
            action = f"""
                <form method="POST" action="/admin/presupuestos/versiones/{escape(version_id)}/lineas/assign-existing" style="display:grid;grid-template-columns:minmax(105px,1fr) minmax(110px,1fr) auto;gap:6px;align-items:end;min-width:330px;">
                    <input type="hidden" name="budget_concept_id" value="{escape(concept_key)}">
                    <input type="hidden" name="tournament_key" value="{escape(tournament_key)}">
                    <input type="hidden" name="edition_year" value="{int(edition_year)}">
                    <input type="hidden" name="budget_view" value="expenses">
                    <input type="hidden" name="phase_filter" value="{escape(str(phase_filter or ''))}">
                    <label style="font-size:11px;color:#475569;">Presupuesto
                        <input name="budget_amount" type="number" min="0" step="0.01" required value="0.00" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #cbd5e1;border-radius:6px;">
                    </label>
                    <label style="font-size:11px;color:#475569;">Fase
                        <input name="phase" value="{escape(str(movement.get('effective_phase') or ''))}" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #cbd5e1;border-radius:6px;">
                    </label>
                    <button type="submit" style="padding:7px 10px;border:0;border-radius:6px;background:#0f766e;color:#fff;font-weight:800;cursor:pointer;">Asignar</button>
                </form>
            """
        elif not has_existing_concept:
            action = '<span style="color:#92400e;">Asigna primero un concepto al documento.</span>'
        rows.append(
            f"""
            <tr>
                <td style="padding:8px;white-space:nowrap;">{escape(str(movement.get('operation_reference') or '—'))}</td>
                <td style="padding:8px;white-space:nowrap;font-weight:700;">{escape(str(movement.get('document_reference') or '—'))}</td>
                <td style="padding:8px;">{escape(str(movement.get('document_state') or '—'))}</td>
                <td style="padding:8px;min-width:180px;">{escape(str(movement.get('movement_concept') or movement.get('budget_concept_name') or 'Sin concepto'))}</td>
                <td style="padding:8px;white-space:nowrap;">{escape(account)}</td>
                <td style="padding:8px;text-align:right;white-space:nowrap;font-weight:800;">${float(movement.get('amount') or 0):,.2f}</td>
                <td style="padding:8px;white-space:nowrap;">{escape(poliza)}<br><span style="color:#64748b;font-size:11px;">{escape(str(poliza_date)[:10])}</span></td>
                <td style="padding:8px;min-width:190px;"><strong>{escape(status)}</strong><br><span style="color:#64748b;font-size:11px;">{escape(str(movement.get('reason') or ''))}</span></td>
                <td style="padding:8px;">{action}</td>
            </tr>
            """
        )
    return f"""
        <section style="margin:14px 0;border-top:3px solid #f59e0b;padding-top:12px;">
            <h3 style="margin:0 0 4px;font-size:16px;color:#78350f;">Movimientos por conciliar</h3>
            <p style="margin:0 0 10px;font-size:12px;color:#64748b;">Detalle trazable de movimientos sin línea en esta versión o sin póliza de resultados.</p>
            <div style="overflow-x:auto;border:1px solid #fde68a;border-radius:6px;background:#fff;">
                <table style="width:100%;min-width:1180px;border-collapse:collapse;font-size:12px;">
                    <thead><tr style="background:#fef3c7;text-align:left;">
                        <th style="padding:8px;">REF Op</th><th style="padding:8px;">Documento</th>
                        <th style="padding:8px;">Estado</th><th style="padding:8px;">Concepto</th>
                        <th style="padding:8px;">Cuenta</th><th style="padding:8px;text-align:right;">Monto</th>
                        <th style="padding:8px;">Póliza</th><th style="padding:8px;">Diagnóstico</th>
                        <th style="padding:8px;">Acción</th>
                    </tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            </div>
        </section>
    """


def render_budget_partida_matrix(
    lines: list[dict[str, Any]],
    *,
    plan_map: dict[str, dict[int, dict[str, float]]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
    actual_movements: Optional[list[dict[str, Any]]] = None,
    version_id: str,
    tournament_key: str,
    can_edit: bool,
    show_committed: bool = True,
    edition_year: Optional[int] = None,
    phase_filter: Optional[str] = None,
    filtered_empty: bool = False,
    cuentas_contables: Optional[list[dict[str, Any]]] = None,
    matrix_mode: str = "full",
    budget_view: str = "expenses",
    budget_period: str = "weekly",
) -> str:
    clean_mode = matrix_mode if matrix_mode in {"full", "expenses", "income"} else "full"
    clean_period = _clean_budget_period(budget_period)
    effective_year = int(edition_year or date.today().year)
    movement_details = (
        _render_budget_movement_details(
            lines,
            actual_movements or [],
            version_id=version_id,
            tournament_key=tournament_key,
            edition_year=effective_year,
            phase_filter=phase_filter,
            can_edit=can_edit,
        )
        if clean_mode in {"full", "expenses"}
        else ""
    )
    if not lines:
        unbudgeted_actuals = _unbudgeted_expense_actuals(lines, actuals_map)
        if (
            clean_mode in {"full", "expenses"}
            and _actuals_has_expense_values(unbudgeted_actuals)
        ):
            if clean_period != "weekly":
                return _render_budget_aggregate_matrix(
                    lines,
                    plan_map=plan_map,
                    actuals_map=actuals_map,
                    edition_year=effective_year,
                    budget_period=clean_period,
                    matrix_mode=clean_mode,
                    show_committed=show_committed,
                ) + movement_details
            return _render_unbudgeted_expense_actuals_card(
                unbudgeted_actuals,
                edition_year=effective_year,
            ) + movement_details
        if movement_details:
            return movement_details
        if filtered_empty:
            return (
                '<div style="padding:16px;color:#64748b;">'
                "No hay partidas para la fase/subproyecto seleccionada. "
                "Prueba otra fase o elige “Todas las fases”."
                "</div>"
            )
        return (
            '<div style="padding:16px;color:#64748b;">'
            + (
                "No hay partidas presupuestales de ingresos para este torneo en el año seleccionado."
                if matrix_mode == "income"
                else "No hay partidas presupuestales para este torneo en el año seleccionado."
            )
            + "</div>"
        )
    if clean_period != "weekly":
        return _render_budget_aggregate_matrix(
            lines,
            plan_map=plan_map,
            actuals_map=actuals_map,
            edition_year=effective_year,
            budget_period=clean_period,
            matrix_mode=clean_mode,
            show_committed=show_committed,
        ) + movement_details

    grouped: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        phase = budget_line_phase_label(line)
        grouped.setdefault(phase, []).append(line)

    html_parts: list[str] = []
    for phase, phase_lines in sorted(grouped.items()):
        html_parts.append(
            f'<div style="margin-top:18px;"><div style="font-weight:800;color:#0f172a;margin-bottom:8px;">'
            f"{escape(phase)}</div>"
        )
        for line in phase_lines:
            line_id = str(line.get("id") or "")
            concept_id = str(line.get("budget_concept_id") or "")
            plan = plan_map.get(line_id, {})
            actuals = _actuals_for_budget_line(actuals_map, concept_id)
            disabled = "" if can_edit else " disabled"
            cuenta_field_html = _render_matrix_cuenta_field(
                line_id,
                line,
                can_edit=can_edit,
            )
            budget_amount_value = float(line.get("budget_amount") or 0)
            html_parts.append(
                f"""
                <div class="budget-excel-line-card" style="border:1px solid #dbe2ea;border-radius:14px;background:#fff;padding:12px;margin-bottom:12px;">
                    <form method="POST" action="/admin/presupuestos/lineas/{escape(line_id)}/update" style="margin-top:0;">
                        <div style="display:grid;grid-template-columns:minmax(240px,1.2fr) minmax(180px,.6fr) minmax(280px,1fr) auto;gap:10px;align-items:end;margin-bottom:10px;">
                            <div>
                                <label style="display:block;font-size:11px;font-weight:800;color:#475569;margin-bottom:4px;letter-spacing:.04em;text-transform:uppercase;">Concepto</label>
                                <div style="font-weight:800;color:#0f172a;padding:9px 0;">{escape(str(line.get("concept_name") or ""))}</div>
                            </div>
                            <div>
                                <label for="budget-amount-{escape(line_id)}" style="display:block;font-size:11px;font-weight:800;color:#475569;margin-bottom:4px;letter-spacing:.04em;text-transform:uppercase;">Monto total</label>
                                <input id="budget-amount-{escape(line_id)}" class="budget-line-total" type="number" step="0.01" min="0" name="budget_amount" value="{budget_amount_value:.2f}" data-line-id="{escape(line_id)}" style="width:100%;padding:9px 10px;border:1px solid #cbd5e1;border-radius:8px;font-weight:800;box-sizing:border-box;"{disabled}>
                            </div>
                            <div>{cuenta_field_html if can_edit else _render_matrix_cuenta_field(line_id, line, can_edit=False)}</div>
                            <div>
                                {f'<button type="button" class="budget-spread-evenly" data-line-id="{escape(line_id)}" data-budget-target-kind="{"income" if clean_mode == "income" else "expense"}" style="background:#e0f2fe;color:#075985;border:1px solid #bae6fd;border-radius:999px;padding:8px 12px;font-size:12px;font-weight:800;cursor:pointer;white-space:nowrap;">Distribuir total</button>' if can_edit else ''}
                            </div>
                        </div>
                        <input type="hidden" name="version_id" value="{escape(version_id)}">
                        <input type="hidden" name="tournament_key" value="{escape(tournament_key)}">
                        {f'<input type="hidden" name="edition_year" value="{int(edition_year)}">' if edition_year is not None else ""}
                        {f'<input type="hidden" name="phase_filter" value="{escape(str(phase_filter))}">' if phase_filter else ""}
                        <input type="hidden" name="budget_view" value="{escape(budget_view)}">
        <input type="hidden" name="budget_period" value="{escape(budget_period)}">
                        <div class="budget-excel-grid" style="overflow-x:auto;border:1px solid #e2e8f0;border-radius:12px;">
                        <table style="width:max-content;min-width:100%;border-collapse:collapse;font-size:11px;">
                            <thead>
                                <tr style="background:#f8fafc;">
                                    <th style="text-align:left;padding:6px 10px;border-bottom:1px solid #e2e8f0;position:sticky;left:0;background:#f8fafc;z-index:2;min-width:150px;">Renglón</th>
                                    <th style="padding:6px 10px;border-bottom:1px solid #e2e8f0;position:sticky;left:150px;background:#f8fafc;z-index:2;min-width:96px;">Monto total</th>
                                    {''.join(f'<th title="{escape(_budget_period_bucket(effective_year, idx, "weekly")[1])}" style="padding:6px;border-bottom:1px solid #e2e8f0;min-width:88px;white-space:nowrap;">Semana {idx}</th>' for idx in range(1, BUDGET_WEEK_COUNT + 1))}
                                </tr>
                            </thead>
                            <tbody>
                """
            )

            def _row(label: str, row_kind: str, *, editable: bool = False) -> str:
                cells = [f'<td style="padding:6px 10px;font-weight:700;color:#475569;position:sticky;left:0;background:#fff;z-index:1;min-width:150px;">{escape(label)}</td>']
                total = 0.0
                for month in range(1, BUDGET_WEEK_COUNT + 1):
                    month_plan = plan.get(month, {})
                    month_actual = actuals.get(month, {})
                    if row_kind == "expense_plan":
                        value = float(month_plan.get("budget_expense_amount") or 0)
                        if editable:
                            cells.append(
                                f'<td style="padding:4px;"><input type="number" step="0.01" min="0" '
                                f'name="month_{month}_expense" value="{value:.2f}" data-budget-week-input="{escape(line_id)}" data-budget-week-kind="expense" '
                                f'style="width:76px;padding:5px;border:1px solid #cbd5e1;border-radius:6px;text-align:right;"{disabled}></td>'
                            )
                        else:
                            cells.append(
                                f'<td style="padding:6px;text-align:right;">${value:,.2f}</td>'
                            )
                    elif row_kind == "income_plan":
                        value = float(month_plan.get("expected_income_amount") or 0)
                        if editable:
                            cells.append(
                                f'<td style="padding:4px;"><input type="number" step="0.01" min="0" '
                                f'name="month_{month}_income" value="{value:.2f}" data-budget-week-input="{escape(line_id)}" data-budget-week-kind="income" '
                                f'style="width:76px;padding:5px;border:1px solid #cbd5e1;border-radius:6px;text-align:right;"{disabled}></td>'
                            )
                        else:
                            cells.append(
                                f'<td style="padding:6px;text-align:right;">${value:,.2f}</td>'
                            )
                    elif row_kind == "expense_real":
                        value = float(month_actual.get("real_expense_cash") or 0)
                        plan_value = float(month_plan.get("budget_expense_amount") or 0)
                        cells.append(
                            f'<td style="padding:6px;text-align:right;{_variance_style(plan_value, value)}">'
                            f"${value:,.2f}</td>"
                        )
                    elif row_kind == "income_real":
                        value = float(month_actual.get("real_income") or 0)
                        cells.append(
                            f'<td style="padding:6px;text-align:right;color:#475569;">${value:,.2f}</td>'
                        )
                    elif row_kind == "committed":
                        value = float(month_actual.get("committed_unpaid") or 0)
                        cells.append(
                            f'<td style="padding:6px;text-align:right;color:#92400e;">${value:,.2f}</td>'
                        )
                    else:
                        value = 0.0
                        cells.append('<td style="padding:6px;text-align:right;">—</td>')
                    total += value
                cells.insert(1, f'<td style="padding:6px 10px;text-align:right;font-weight:800;position:sticky;left:150px;background:#fff;z-index:1;min-width:96px;">${total:,.2f}</td>')
                return f"<tr>{''.join(cells)}</tr>"

            if clean_mode in {"full", "expenses"}:
                html_parts.append(_row("Presupuesto gasto", "expense_plan", editable=True))
                html_parts.append(_row("Gasto Real", "expense_real"))
            if clean_mode in {"full", "income"}:
                html_parts.append(_row("Ingreso esperado", "income_plan", editable=True))
                html_parts.append(_row("Ingreso real", "income_real"))
            if clean_mode in {"full", "expenses"} and show_committed:
                html_parts.append(_row("Comprometido no pagado", "committed"))

            plan_expense_total = sum(
                float(plan.get(m, {}).get("budget_expense_amount") or 0) for m in range(1, BUDGET_WEEK_COUNT + 1)
            )
            plan_income_total = sum(
                float(plan.get(m, {}).get("expected_income_amount") or 0) for m in range(1, BUDGET_WEEK_COUNT + 1)
            )
            real_expense_total = sum(
                float(actuals.get(m, {}).get("real_expense_cash") or 0) for m in range(1, BUDGET_WEEK_COUNT + 1)
            )
            real_income_total = sum(
                float(actuals.get(m, {}).get("real_income") or 0) for m in range(1, BUDGET_WEEK_COUNT + 1)
            )
            if clean_mode == "income":
                summary_html = (
                    f'<span>Ingreso esperado: <strong>${plan_income_total:,.2f}</strong></span>'
                    f'<span>Ingreso real: <strong>${real_income_total:,.2f}</strong></span>'
                )
                button_label = "Guardar ingreso por semanas"
            elif clean_mode == "expenses":
                summary_html = (
                    f'<span>Presupuesto gasto: <strong>${plan_expense_total:,.2f}</strong></span>'
                    f'<span>Gasto presupuestal: <strong>${real_expense_total:,.2f}</strong></span>'
                )
                button_label = "Guardar gasto por semanas"
            else:
                net_plan = plan_income_total - plan_expense_total
                net_real = real_income_total - real_expense_total
                summary_html = (
                    f'<span>Neto plan: <strong>${net_plan:,.2f}</strong></span>'
                    f'<span>Neto real: <strong>${net_real:,.2f}</strong></span>'
                )
                button_label = "Guardar plan por semanas"

            html_parts.append(
                f"""
                            </tbody>
                        </table>
                        </div>
                        <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:#475569;">
                            {summary_html}
                        </div>
                        {f'<button type="submit" style="margin-top:10px;background:#0f766e;color:#fff;border:none;border-radius:999px;padding:8px 14px;font-weight:700;cursor:pointer;">{button_label}</button>' if can_edit else '<div style="margin-top:8px;color:#64748b;">Sin permiso para editar.</div>'}
                    </form>
                </div>
                """
            )
        html_parts.append("</div>")

    unbudgeted_actuals = _unbudgeted_expense_actuals(lines, actuals_map)
    if (
        clean_mode in {"full", "expenses"}
        and _actuals_has_expense_values(unbudgeted_actuals)
    ):
        html_parts.append(
            _render_unbudgeted_expense_actuals_card(
                unbudgeted_actuals,
                edition_year=effective_year,
            )
        )

    if can_edit:
        html_parts.append(_render_budget_matrix_spreadsheet_script())
    if can_edit and cuentas_contables:
        html_parts.append(_render_matrix_cuenta_search_script(cuentas_contables))
    if movement_details:
        html_parts.append(movement_details)

    return "".join(html_parts)


def _render_budget_matrix_spreadsheet_script() -> str:
    return """
    <script>
      (function() {
        function parseAmount(value) {
          const n = Number((value || "").toString().replace(/,/g, ""));
          return Number.isFinite(n) ? n : 0;
        }
        document.querySelectorAll(".budget-spread-evenly").forEach(function(button) {
          button.addEventListener("click", function() {
            const lineId = button.getAttribute("data-line-id") || "";
            const kind = button.getAttribute("data-budget-target-kind") || "expense";
            const totalInput = document.querySelector('.budget-line-total[data-line-id="' + lineId + '"]');
            const weekInputs = Array.from(document.querySelectorAll('[data-budget-week-input="' + lineId + '"][data-budget-week-kind="' + kind + '"]'));
            if (!totalInput || weekInputs.length === 0) {
              return;
            }
            const total = parseAmount(totalInput.value);
            const base = Math.floor((total / weekInputs.length) * 100) / 100;
            let assigned = 0;
            weekInputs.forEach(function(input, index) {
              let value = base;
              if (index === weekInputs.length - 1) {
                value = Math.round((total - assigned) * 100) / 100;
              } else {
                assigned += base;
              }
              input.value = value.toFixed(2);
            });
          });
        });
      })();
    </script>
    """


def render_budget_detail_section_nav(
    *,
    tournament_key: Optional[str] = None,
    edition_year: Optional[int] = None,
    version_id: Optional[str] = None,
    selected_view: str = "expenses",
    phase_filter: Optional[str] = None,
    show_committed: bool = True,
    budget_period: Optional[str] = None,
) -> str:
    if not tournament_key:
        return """
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 18px;">
            <a class="button" href="#presupuesto-gastos">Gastos</a>
            <a class="button secondary" href="#presupuesto-ingresos">Ingresos</a>
        </div>
        """
    expenses_url = budget_tournament_detail_url(
        tournament_key,
        edition_year=edition_year,
        version_id=version_id,
        budget_view="expenses",
        phase_filter=phase_filter,
        show_committed=show_committed,
        budget_period=budget_period,
    )
    income_url = budget_tournament_detail_url(
        tournament_key,
        edition_year=edition_year,
        version_id=version_id,
        budget_view="income",
        phase_filter=phase_filter,
        show_committed=show_committed,
        budget_period=budget_period,
    )
    expenses_class = "button" if selected_view != "income" else "button secondary"
    income_class = "button" if selected_view == "income" else "button secondary"
    return """
    <div style="display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 18px;">
        <a class="{expenses_class}" href="{expenses_url}">Gastos</a>
        <a class="{income_class}" href="{income_url}">Ingresos</a>
    </div>
    """.format(
        expenses_class=expenses_class,
        expenses_url=escape(expenses_url),
        income_class=income_class,
        income_url=escape(income_url),
    )


def render_cfdi_income_bridge_panel(
    *,
    tournament_key: str,
    edition_year: int,
    lines: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    links: list[dict[str, Any]],
    can_edit: bool,
    return_to: str = "",
) -> str:
    line_items: list[dict[str, str]] = []
    for line in lines:
        line_id = str(line.get("id") or "")
        if not line_id:
            continue
        phase = budget_line_phase_label(line)
        phase_key = budget_line_phase_key(line)
        amount = float(line.get("budget_amount") or 0)
        account_code = str(
            line.get("account_code_final") or line.get("account_code_suggested") or ""
        ).strip()
        label_parts = [phase, str(line.get("concept_name") or "Partida")]
        if account_code:
            label_parts.append(account_code)
        label_parts.append(f"${amount:,.2f}")
        label = " / ".join(label_parts)
        line_items.append(
            {
                "id": line_id,
                "label": label,
                "phase": phase,
                "phase_key": phase_key,
            }
        )
    candidate_items: list[dict[str, str]] = []
    for cfdi in candidates:
        cfdi_id = str(cfdi.get("id") or "")
        if not cfdi_id:
            continue
        date_text = str(cfdi.get("fecha") or "")[:10] or "sin fecha"
        total = float(cfdi.get("total") or 0)
        label = (
            f"{cfdi.get('cfdi_uuid') or cfdi_id} / "
            f"{cfdi.get('emisor_rfc') or 'sin emisor'} / "
            f"${total:,.2f} / {date_text}"
        )
        candidate_items.append(
            {
                "id": cfdi_id,
                "label": label,
                "total": f"{total:.2f}",
                "fecha": date_text if date_text != "sin fecha" else "",
            }
        )
    return_to_hidden = (
        f'<input type="hidden" name="return_to" value="{escape(str(return_to), quote=True)}">'
        if str(return_to or "").strip()
        else ""
    )
    active_rows: list[str] = []
    inactive_rows: list[str] = []
    for link in links:
        is_active = not link.get("unlinked_at")
        amount = float(link.get("amount") or 0)
        income_date = str(link.get("income_date") or "")[:10]
        status = "Activo" if is_active else "Desvinculado"
        unlink_form = ""
        if can_edit and is_active:
            unlink_form = f"""
                <form method="POST" action="/admin/presupuestos/torneo/{quote(str(tournament_key))}/cfdi-ingresos/{escape(str(link.get('id') or ''))}/unlink">
                    <input type="hidden" name="edition_year" value="{int(edition_year)}">
                    {return_to_hidden}
                    <button type="submit" style="background:#fee2e2;color:#991b1b;border:1px solid #fecaca;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:700;cursor:pointer;">
                        Dejar de contar
                    </button>
                </form>
            """
        row_html = f"""
            <tr>
                <td><code>{escape(str(link.get("cfdi_uuid") or ""))}</code></td>
                <td>{escape(str(link.get("emisor_rfc") or ""))}</td>
                <td>{escape(str(link.get("concept_name") or ""))}</td>
                <td>{escape(str(link.get("phase") or "General"))}</td>
                <td style="text-align:right;">${amount:,.2f}</td>
                <td>{escape(income_date)}</td>
                <td>{status}</td>
                <td>{unlink_form}</td>
            </tr>
        """
        if is_active:
            active_rows.append(row_html)
        else:
            inactive_rows.append(row_html)

    no_lines_disabled = "" if line_items else " disabled"
    existing_disabled = "" if can_edit and line_items and candidate_items else " disabled"
    upload_disabled = "" if can_edit and line_items else " disabled"
    line_payload = json.dumps(line_items, ensure_ascii=True)
    candidate_payload = json.dumps(candidate_items, ensure_ascii=True)
    edit_note = (
        "Los RFC activos en /admin/rfc son el allowlist. El CFDI solo cuenta si su RFC emisor coincide estrictamente."
        if can_edit
        else "Sin permiso para vincular ingresos."
    )
    no_income_lines_notice = (
        """
        <div style="margin-top:12px;padding:12px;border:1px solid #fde68a;border-radius:10px;background:#fffbeb;color:#92400e;font-size:13px;font-weight:700;">
            Primero agrega o importa partidas de ingreso para poder vincular CFDI.
        </div>
        """
        if not line_items
        else ""
    )
    return f"""
    <section class="workspace-card" style="margin-bottom:18px;">
        <div class="workspace-section-title">CFDI PSP vinculados a ingreso real</div>
        <div class="workspace-section-subtitle">
            Usa CFDI emitidos disponibles desde la descarga SAT o sube un comprobante puntual.
            {escape(edit_note)}
            No borra el CFDI; solo deja de contar como ingreso real.
            <a href="/admin/gastos/sat" style="color:#0f766e;font-weight:800;">Operación SAT</a>
            ·
            <a href="/admin/gastos/cfdis/matching" style="color:#0f766e;font-weight:800;">Matching CFDI</a>
        </div>
        {no_income_lines_notice}
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin-top:14px;">
            <form method="POST" action="/admin/presupuestos/torneo/{quote(str(tournament_key))}/cfdi-ingresos/link" style="display:grid;gap:10px;padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc;">
                <div style="font-weight:800;color:#0f172a;">Vincular CFDI SAT existente</div>
                <input type="hidden" name="edition_year" value="{int(edition_year)}">
                {return_to_hidden}
                <label style="font-size:12px;font-weight:700;color:#475569;">CFDI emitido por PSP</label>
                <input type="hidden" id="cfdi-income-existing-cfdi-id" name="cfdi_report_id">
                <input id="cfdi-income-existing-cfdi-input" list="cfdi-income-existing-cfdi-options" data-cfdi-income-field="cfdi" data-hidden-input="cfdi-income-existing-cfdi-id" required{existing_disabled} placeholder="Buscar por UUID, RFC, monto o fecha" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                <datalist id="cfdi-income-existing-cfdi-options"></datalist>
                <label style="font-size:12px;font-weight:700;color:#475569;">Concepto</label>
                <input type="hidden" id="cfdi-income-existing-line-id" name="budget_line_id">
                <input id="cfdi-income-existing-line-input" list="cfdi-income-existing-line-options" data-cfdi-income-field="line" data-hidden-input="cfdi-income-existing-line-id" required{no_lines_disabled} placeholder="Buscar partida, cuenta o monto" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                <datalist id="cfdi-income-existing-line-options"></datalist>
                <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;">
                    <div><label style="font-size:12px;font-weight:700;color:#475569;">Monto</label><input type="number" step="0.01" min="0" name="amount" data-cfdi-income-autofill="amount" placeholder="Total CFDI" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;"></div>
                    <div><label style="font-size:12px;font-weight:700;color:#475569;">Fecha ingreso</label><input type="date" name="income_date" data-cfdi-income-autofill="income_date" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;"></div>
                </div>
                <button type="submit" class="button"{existing_disabled}>Vincular ingreso</button>
            </form>
            <form method="POST" action="/admin/presupuestos/torneo/{quote(str(tournament_key))}/cfdi-ingresos/upload-link" enctype="multipart/form-data" style="display:grid;gap:10px;padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc;">
                <div style="font-weight:800;color:#0f172a;">Subir CFDI y vincular</div>
                <input type="hidden" name="edition_year" value="{int(edition_year)}">
                {return_to_hidden}
                <label style="font-size:12px;font-weight:700;color:#475569;">XML CFDI</label>
                <input type="file" name="cfdi_xml" accept=".xml,text/xml,application/xml"{upload_disabled}>
                <label style="font-size:12px;font-weight:700;color:#475569;">PDF CFDI</label>
                <input type="file" name="cfdi_pdf" accept=".pdf,application/pdf"{upload_disabled}>
                <label style="font-size:12px;font-weight:700;color:#475569;">Concepto</label>
                <input type="hidden" id="cfdi-income-upload-line-id" name="budget_line_id">
                <input id="cfdi-income-upload-line-input" list="cfdi-income-upload-line-options" data-cfdi-income-field="line" data-hidden-input="cfdi-income-upload-line-id" required{no_lines_disabled} placeholder="Buscar partida, cuenta o monto" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                <datalist id="cfdi-income-upload-line-options"></datalist>
                <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;">
                    <div><label style="font-size:12px;font-weight:700;color:#475569;">Monto</label><input type="number" step="0.01" min="0" name="amount" placeholder="Total CFDI" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;"></div>
                    <div><label style="font-size:12px;font-weight:700;color:#475569;">Fecha ingreso</label><input type="date" name="income_date" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;"></div>
                </div>
                <button type="submit" class="button"{upload_disabled}>Subir y vincular</button>
            </form>
        </div>
        <div style="margin-top:16px;overflow-x:auto;">
            <table>
                <thead><tr><th>UUID CFDI</th><th>RFC emisor</th><th>Partida</th><th>Fase</th><th>Monto</th><th>Fecha</th><th>Estado</th><th>Acción</th></tr></thead>
                <tbody>
                    {''.join(active_rows + inactive_rows) if (active_rows or inactive_rows) else '<tr><td colspan="8">Sin CFDI PSP vinculados.</td></tr>'}
                </tbody>
            </table>
        </div>
        <script>
        (function() {{
            const lineOptions = {line_payload};
            const cfdiOptions = {candidate_payload};

            function fillDatalist(id, items) {{
                const datalist = document.getElementById(id);
                if (!datalist) return;
                datalist.innerHTML = "";
                items.forEach(function(item) {{
                    const option = document.createElement("option");
                    option.value = item.label;
                    datalist.appendChild(option);
                }});
            }}

            function selectedOptionFor(input, items) {{
                return items.find(function(item) {{
                    return item.label === input.value;
                }}) || null;
            }}

            function syncHidden(input, items) {{
                const hiddenId = input.getAttribute("data-hidden-input");
                const hidden = hiddenId ? document.getElementById(hiddenId) : null;
                const selected = selectedOptionFor(input, items);
                if (hidden) hidden.value = selected ? selected.id : "";
                return selected;
            }}

            function autofillCfdiFields(input, selected) {{
                const form = input.closest("form");
                if (!form || !selected) return;
                const amount = form.querySelector('[data-cfdi-income-autofill="amount"]');
                const incomeDate = form.querySelector('[data-cfdi-income-autofill="income_date"]');
                if (amount && selected.total) amount.value = selected.total;
                if (incomeDate && selected.fecha) incomeDate.value = selected.fecha;
            }}

            function wireLineInput(input) {{
                function refresh() {{
                    fillDatalist(input.getAttribute("list"), lineOptions);
                    syncHidden(input, lineOptions);
                    input.placeholder = lineOptions.length
                        ? "Buscar partida, cuenta o monto"
                        : "Sin partidas de ingreso disponibles";
                }}
                input.addEventListener("input", refresh);
                refresh();
            }}

            function wireCfdiInput(input) {{
                fillDatalist(input.getAttribute("list"), cfdiOptions);
                input.addEventListener("input", function() {{
                    const selected = syncHidden(input, cfdiOptions);
                    autofillCfdiFields(input, selected);
                }});
            }}

            document.querySelectorAll('[data-cfdi-income-field="line"]').forEach(wireLineInput);
            document.querySelectorAll('[data-cfdi-income-field="cfdi"]').forEach(wireCfdiInput);
            document.querySelectorAll('form[action*="/cfdi-ingresos/"]').forEach(function(form) {{
                form.addEventListener("submit", function(event) {{
                    const missing = Array.from(form.querySelectorAll('input[type="hidden"][name="cfdi_report_id"], input[type="hidden"][name="budget_line_id"]'))
                        .filter(function(input) {{ return !input.value; }});
                    if (!missing.length) return;
                    event.preventDefault();
                    const visible = form.querySelector('[data-cfdi-income-field="cfdi"], [data-cfdi-income-field="line"]');
                    if (visible) {{
                        visible.setCustomValidity("Selecciona una opción de la lista.");
                        visible.reportValidity();
                        visible.setCustomValidity("");
                    }}
                }});
            }});
        }})();
        </script>
    </section>
    """


def _render_phase_select_options(
    phase_labels: list[str],
    *,
    selected_value: str = "",
) -> str:
    options = ['<option value="">Todas</option>']
    seen: set[str] = set()
    for label in phase_labels:
        clean = str(label or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        selected_attr = " selected" if clean == selected_value else ""
        options.append(
            f'<option value="{escape(clean)}"{selected_attr}>{escape(clean)}</option>'
        )
    if selected_value and selected_value not in seen:
        options.append(
            f'<option value="{escape(selected_value)}" selected>'
            f"{escape(selected_value)}</option>"
        )
    return "".join(options)


def _render_cuenta_contable_select_options(
    cuentas_contables: list[dict[str, Any]],
    *,
    selected_value: str = "",
) -> str:
    options = ['<option value="">Sin cuenta contable</option>']
    selected_clean = str(selected_value or "").strip()
    for cuenta in cuentas_contables:
        cuenta_id = str(cuenta.get("id") or "").strip()
        if not cuenta_id:
            continue
        codigo = str(cuenta.get("codigo") or "").strip()
        nombre = str(cuenta.get("nombre") or "").strip()
        tipo = str(cuenta.get("tipo") or "").strip()
        label = " · ".join(part for part in [codigo, nombre, tipo] if part)
        selected_attr = " selected" if cuenta_id == selected_clean else ""
        options.append(
            f'<option value="{escape(cuenta_id)}"{selected_attr}>'
            f"{escape(label or cuenta_id)}</option>"
        )
    return "".join(options)


def render_add_tournament_line_form(
    *,
    version_id: str,
    tournament_key: str,
    tournament_id: Optional[str],
    tournament_code: str,
    tournament_name: str,
    phase_labels: Optional[list[str]] = None,
    selected_phase: str = "",
    line_direction: str = "expense",
    show_phase_field: bool = True,
    section_id: Optional[str] = None,
    cuentas_contables: Optional[list[dict[str, Any]]] = None,
    budget_view: Optional[str] = None,
) -> str:
    """Render the add-line form for tournament budget detail pages."""
    labels = [
        str(item).strip()
        for item in (phase_labels or [])
        if str(item or "").strip()
    ]
    phase_options = _render_phase_select_options(labels, selected_value=selected_phase)
    cuenta_options = _render_cuenta_contable_select_options(cuentas_contables or [])
    tournament_id_clean = str(tournament_id or "").strip()
    direction_clean = "income" if str(line_direction or "").strip().lower() == "income" else "expense"
    is_income = direction_clean == "income"
    view_clean = str(budget_view or ("income" if is_income else "expenses")).strip()
    section_title = "Agregar partida de ingreso" if is_income else "Agregar partida al torneo"
    concept_placeholder = (
        "Ej. Inscripción, patrocinio, recuperación"
        if is_income
        else "Ej. Hospedaje"
    )
    fetch_script = ""
    if show_phase_field and tournament_id_clean:
        fetch_script = f"""
        <script>
        (function() {{
            const select = document.getElementById('add-line-phase');
            if (!select) return;
            const tournamentId = {repr(tournament_id_clean)};

            function setPhaseOptions(labels) {{
                const current = select.value;
                select.innerHTML = '<option value="">Todas</option>';
                const seen = new Set();
                (labels || []).forEach(function(label) {{
                    const clean = String(label || '').trim();
                    if (!clean || seen.has(clean)) return;
                    seen.add(clean);
                    const opt = document.createElement('option');
                    opt.value = clean;
                    opt.textContent = clean;
                    if (clean === current) opt.selected = true;
                    select.appendChild(opt);
                }});
                if (current && !seen.has(current)) {{
                    const custom = document.createElement('option');
                    custom.value = current;
                    custom.textContent = current;
                    custom.selected = true;
                    select.appendChild(custom);
                }}
            }}

            fetch('/api/torneos/' + encodeURIComponent(tournamentId) + '/etapas', {{
                headers: {{ 'Accept': 'application/json' }},
            }})
                .then(function(response) {{
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    return response.json();
                }})
                .then(function(payload) {{
                    const scopeLabels = Array.isArray(payload.scope_labels)
                        ? payload.scope_labels
                        : [];
                    const etapas = Array.isArray(payload.etapas) ? payload.etapas : [];
                    const labels = scopeLabels.length ? scopeLabels : etapas;
                    if (labels.length) setPhaseOptions(labels);
                }})
                .catch(function(error) {{
                    console.error('No se pudieron cargar las fases del torneo.', error);
                }});
        }})();
        </script>
        """

    no_tournament_hint = ""
    if show_phase_field and not tournament_id_clean:
        no_tournament_hint = (
            '<div style="margin-top:10px;font-size:12px;color:#92400e;">'
            "Este torneo no está vinculado a un proyecto en "
            '<a href="/admin/torneos" style="color:#0f766e;">Torneos y proyectos</a>. '
            "Configura el proyecto para cargar fases/subproyectos."
            "</div>"
        )
    phase_field_html = ""
    if show_phase_field:
        phase_field_html = f"""
                <div>
                    <label for="add-line-phase" style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;">
                        Fase / subproyecto
                    </label>
                    <select
                        id="add-line-phase"
                        name="phase"
                        style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px;background:#fff;"
                        {'disabled title="Vincula el torneo en Torneos y proyectos"' if not tournament_id_clean else ''}
                    >
                        {phase_options}
                    </select>
                </div>
        """
    subtitle_tail = (
        'La fase/subproyecto proviene de las etapas configuradas en '
        '<a href="/admin/torneos" style="color:#0f766e;">Torneos y proyectos</a>.'
        if show_phase_field
        else "Esta partida se agregará a ingresos sin fase/subproyecto."
    )
    section_id_attr = f' id="{escape(section_id)}"' if section_id else ""

    return f"""
    <section class="workspace-card"{section_id_attr} style="margin-bottom:18px;">
        <div class="workspace-section-title">{section_title}</div>
        <div class="workspace-section-subtitle">
            Captura un nuevo concepto para
            <strong>{escape(tournament_name or tournament_key)}</strong>.
            {subtitle_tail}
        </div>
        <form
            method="POST"
            action="/admin/presupuestos/versiones/{escape(str(version_id))}/lineas/create"
            class="budget-add-line-form"
            style="margin-top:16px;"
        >
            <input type="hidden" name="tournament_key" value="{escape(tournament_key)}">
            <input type="hidden" name="tournament_id" value="{escape(tournament_id_clean)}">
            <input type="hidden" name="tournament_code" value="{escape(tournament_code or "")}">
            <input type="hidden" name="tournament_name" value="{escape(tournament_name or "")}">
            <input type="hidden" name="line_direction" value="{escape(direction_clean)}">
            <input type="hidden" name="budget_view" value="{escape(view_clean)}">
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;align-items:end;">
                <div>
                    <label for="add-line-concept" style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;">
                        Partida / concepto
                    </label>
                    <input
                        id="add-line-concept"
                        type="text"
                        name="concept_name"
                        required
                        placeholder="{concept_placeholder}"
                        style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px;"
                    >
                </div>
                {phase_field_html}
                <div>
                    <label for="add-line-cuenta-{escape(direction_clean)}" style="display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;">
                        Cuenta contable
                    </label>
                    <select
                        id="add-line-cuenta-{escape(direction_clean)}"
                        name="cuenta_contable_id"
                        style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px;"
                    >
                        {cuenta_options}
                    </select>
                </div>
                <div>
                    <button
                        type="submit"
                        class="button primary"
                        style="width:100%;justify-content:center;"
                    >
                        Agregar línea
                    </button>
                </div>
            </div>
            {no_tournament_hint}
        </form>
        {fetch_script}
    </section>
    """


def render_yoy_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div style="color:#64748b;">Sin datos comparables año contra año.</div>'
    body = ""
    for row in rows:
        status = str(row.get("status") or "")
        badge = {
            "new": ("Nueva", "#dcfce7", "#166534"),
            "retired": ("Retirada", "#fee2e2", "#991b1b"),
            "stable": ("Continua", "#e0f2fe", "#075985"),
        }.get(status, ("—", "#e2e8f0", "#334155"))
        body += f"""
        <tr>
            <td>{escape(str(row.get("concept_name") or ""))}</td>
            <td><span style="background:{badge[1]};color:{badge[2]};padding:4px 8px;border-radius:999px;font-size:11px;font-weight:700;">{badge[0]}</span></td>
            <td>${float(row.get("prior_budget") or 0):,.2f}</td>
            <td>${float(row.get("current_budget") or 0):,.2f}</td>
            <td>${float(row.get("delta") or 0):,.2f}</td>
            <td>{'' if row.get('delta_pct') is None else f"{float(row.get('delta_pct')):.1f}%"}</td>
        </tr>
        """
    return f"""
    <table style="width:100%;border-collapse:collapse;">
        <thead><tr>
            <th>Partida</th><th>Estado YoY</th><th>Año previo</th><th>Año actual</th><th>Δ</th><th>Δ%</th>
        </tr></thead>
        <tbody>{body}</tbody>
    </table>
    """
