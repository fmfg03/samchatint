"""Presupuestos dashboard and tournament detail UI helpers."""

from __future__ import annotations

import json
from html import escape
from typing import Any, Optional
from urllib.parse import quote

from samchat.budgets.service import month_labels_es

MONTH_SHORT = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
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
            La matriz partida × mes es la <strong>fuente única operativa</strong> para el año
            <strong>{edition_year}</strong>.
            Las partidas y cuentas contables provienen del catálogo SSOT compartido con
            solicitudes de transferencia y captura rápida de gastos.
        </div>
    </form>
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
                            Presupuesto gasto
                        </div>
                        <div style="font-size:18px;font-weight:800;color:#0f766e;">
                            ${float(rollup.get("budget_expense_total") or item.get("budget_total") or 0):,.2f}
                        </div>
                    </div>
                </div>
                <div style="margin-top:10px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;font-size:12px;">
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Ingreso esperado</div>
                        <div style="font-weight:800;">${float(rollup.get("expected_income_total") or 0):,.2f}</div>
                    </div>
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Gasto real (caja)</div>
                        <div style="font-weight:800;">${float(comparison.get("paid_total") or 0):,.2f}</div>
                    </div>
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Ingreso real</div>
                        <div style="font-weight:800;">${float(rollup.get("real_income_total") or 0):,.2f}</div>
                    </div>
                    <div style="padding:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;">
                        <div style="color:#64748b;">Comprometido</div>
                        <div style="font-weight:800;">${float(comparison.get("committed_total") or 0):,.2f}</div>
                    </div>
                </div>
                <div style="margin-top:12px;display:flex;justify-content:flex-end;">
                    <span class="button" style="padding:8px 12px;font-size:12px;border-radius:10px;">
                        Capturar detalle &rarr;
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


def render_budget_partida_matrix(
    lines: list[dict[str, Any]],
    *,
    plan_map: dict[str, dict[int, dict[str, float]]],
    actuals_map: dict[str, dict[int, dict[str, float]]],
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
) -> str:
    if not lines:
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
    clean_mode = matrix_mode if matrix_mode in {"full", "expenses", "income"} else "full"

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
            actual_key = concept_id or "__unassigned__"
            plan = plan_map.get(line_id, {})
            actuals = actuals_map.get(actual_key, actuals_map.get("__unassigned__", {}))
            disabled = "" if can_edit else " disabled"
            cuenta_field_html = _render_matrix_cuenta_field(
                line_id,
                line,
                can_edit=can_edit,
            )
            html_parts.append(
                f"""
                <div style="border:1px solid #dbe2ea;border-radius:14px;background:#fff;padding:12px;margin-bottom:12px;">
                    <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
                        <div style="flex:1;">
                            <div style="font-weight:800;color:#0f172a;">{escape(str(line.get("concept_name") or ""))}</div>
                            <div style="font-size:12px;color:#64748b;margin-top:4px;">
                                Anual plan: ${float(line.get("budget_amount") or 0):,.2f}
                            </div>
                            {cuenta_field_html if not can_edit else ""}
                        </div>
                    </div>
                    <form method="POST" action="/admin/presupuestos/lineas/{escape(line_id)}/update" style="margin-top:10px;">
                        <input type="hidden" name="version_id" value="{escape(version_id)}">
                        <input type="hidden" name="tournament_key" value="{escape(tournament_key)}">
                        {f'<input type="hidden" name="edition_year" value="{int(edition_year)}">' if edition_year is not None else ""}
                        {f'<input type="hidden" name="phase_filter" value="{escape(str(phase_filter))}">' if phase_filter else ""}
                        <input type="hidden" name="budget_view" value="{escape(budget_view)}">
                        {cuenta_field_html if can_edit else ""}
                        <div style="overflow-x:auto;">
                        <table style="width:100%;border-collapse:collapse;font-size:11px;">
                            <thead>
                                <tr style="background:#f8fafc;">
                                    <th style="text-align:left;padding:6px;border-bottom:1px solid #e2e8f0;">Concepto</th>
                                    {''.join(f'<th style="padding:6px;border-bottom:1px solid #e2e8f0;">{label}</th>' for label in MONTH_SHORT)}
                                    <th style="padding:6px;border-bottom:1px solid #e2e8f0;">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                """
            )

            def _row(label: str, row_kind: str, *, editable: bool = False) -> str:
                cells = [f'<td style="padding:6px;font-weight:700;color:#475569;">{escape(label)}</td>']
                total = 0.0
                for month in range(1, 13):
                    month_plan = plan.get(month, {})
                    month_actual = actuals.get(month, {})
                    if row_kind == "expense_plan":
                        value = float(month_plan.get("budget_expense_amount") or 0)
                        if editable:
                            cells.append(
                                f'<td style="padding:4px;"><input type="number" step="0.01" min="0" '
                                f'name="month_{month}_expense" value="{value:.2f}" '
                                f'style="width:72px;padding:4px;border:1px solid #cbd5e1;border-radius:6px;"{disabled}></td>'
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
                                f'name="month_{month}_income" value="{value:.2f}" '
                                f'style="width:72px;padding:4px;border:1px solid #cbd5e1;border-radius:6px;"{disabled}></td>'
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
                        cells.append(f'<td style="padding:6px;text-align:right;">—</td>')
                    total += value
                cells.append(f'<td style="padding:6px;text-align:right;font-weight:800;">${total:,.2f}</td>')
                return f"<tr>{''.join(cells)}</tr>"

            if clean_mode in {"full", "expenses"}:
                html_parts.append(_row("Presupuesto gasto", "expense_plan", editable=True))
                html_parts.append(_row("Gasto real (caja)", "expense_real"))
            if clean_mode in {"full", "income"}:
                html_parts.append(_row("Ingreso esperado", "income_plan", editable=True))
                html_parts.append(_row("Ingreso real", "income_real"))
            if clean_mode in {"full", "expenses"} and show_committed:
                html_parts.append(_row("Comprometido no pagado", "committed"))

            plan_expense_total = sum(
                float(plan.get(m, {}).get("budget_expense_amount") or 0) for m in range(1, 13)
            )
            plan_income_total = sum(
                float(plan.get(m, {}).get("expected_income_amount") or 0) for m in range(1, 13)
            )
            real_expense_total = sum(
                float(actuals.get(m, {}).get("real_expense_cash") or 0) for m in range(1, 13)
            )
            real_income_total = sum(
                float(actuals.get(m, {}).get("real_income") or 0) for m in range(1, 13)
            )
            if clean_mode == "income":
                summary_html = (
                    f'<span>Ingreso esperado: <strong>${plan_income_total:,.2f}</strong></span>'
                    f'<span>Ingreso real: <strong>${real_income_total:,.2f}</strong></span>'
                )
                button_label = "Guardar ingreso mensual"
            elif clean_mode == "expenses":
                summary_html = (
                    f'<span>Presupuesto gasto: <strong>${plan_expense_total:,.2f}</strong></span>'
                    f'<span>Gasto real: <strong>${real_expense_total:,.2f}</strong></span>'
                )
                button_label = "Guardar gasto mensual"
            else:
                net_plan = plan_income_total - plan_expense_total
                net_real = real_income_total - real_expense_total
                summary_html = (
                    f'<span>Neto plan: <strong>${net_plan:,.2f}</strong></span>'
                    f'<span>Neto real: <strong>${net_real:,.2f}</strong></span>'
                )
                button_label = "Guardar plan mensual"

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

    if can_edit and cuentas_contables:
        html_parts.append(_render_matrix_cuenta_search_script(cuentas_contables))

    return "".join(html_parts)


def render_budget_detail_section_nav(
    *,
    tournament_key: Optional[str] = None,
    edition_year: Optional[int] = None,
    version_id: Optional[str] = None,
    selected_view: str = "expenses",
    phase_filter: Optional[str] = None,
    show_committed: bool = True,
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
    )
    income_url = budget_tournament_detail_url(
        tournament_key,
        edition_year=edition_year,
        version_id=version_id,
        budget_view="income",
        phase_filter=phase_filter,
        show_committed=show_committed,
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
            }
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
            {escape(edit_note)}
            No borra el CFDI; solo deja de contar como ingreso real.
        </div>
        {no_income_lines_notice}
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin-top:14px;">
            <form method="POST" action="/admin/presupuestos/torneo/{quote(str(tournament_key))}/cfdi-ingresos/link" style="display:grid;gap:10px;padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc;">
                <div style="font-weight:800;color:#0f172a;">Vincular CFDI existente</div>
                <input type="hidden" name="edition_year" value="{int(edition_year)}">
                <label style="font-size:12px;font-weight:700;color:#475569;">CFDI con emisor PSP</label>
                <input type="hidden" id="cfdi-income-existing-cfdi-id" name="cfdi_report_id">
                <input id="cfdi-income-existing-cfdi-input" list="cfdi-income-existing-cfdi-options" data-cfdi-income-field="cfdi" data-hidden-input="cfdi-income-existing-cfdi-id" required{existing_disabled} placeholder="Buscar por UUID, RFC, monto o fecha" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                <datalist id="cfdi-income-existing-cfdi-options"></datalist>
                <label style="font-size:12px;font-weight:700;color:#475569;">Partida presupuestal</label>
                <input type="hidden" id="cfdi-income-existing-line-id" name="budget_line_id">
                <input id="cfdi-income-existing-line-input" list="cfdi-income-existing-line-options" data-cfdi-income-field="line" data-hidden-input="cfdi-income-existing-line-id" required{no_lines_disabled} placeholder="Buscar partida, cuenta o monto" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;">
                <datalist id="cfdi-income-existing-line-options"></datalist>
                <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;">
                    <div><label style="font-size:12px;font-weight:700;color:#475569;">Monto</label><input type="number" step="0.01" min="0" name="amount" placeholder="Total CFDI" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;"></div>
                    <div><label style="font-size:12px;font-weight:700;color:#475569;">Fecha ingreso</label><input type="date" name="income_date" style="width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;"></div>
                </div>
                <button type="submit" class="button"{existing_disabled}>Vincular ingreso</button>
            </form>
            <form method="POST" action="/admin/presupuestos/torneo/{quote(str(tournament_key))}/cfdi-ingresos/upload-link" enctype="multipart/form-data" style="display:grid;gap:10px;padding:14px;border:1px solid #e2e8f0;border-radius:14px;background:#f8fafc;">
                <div style="font-weight:800;color:#0f172a;">Subir CFDI y vincular</div>
                <input type="hidden" name="edition_year" value="{int(edition_year)}">
                <label style="font-size:12px;font-weight:700;color:#475569;">XML CFDI</label>
                <input type="file" name="cfdi_xml" accept=".xml,text/xml,application/xml"{upload_disabled}>
                <label style="font-size:12px;font-weight:700;color:#475569;">PDF CFDI</label>
                <input type="file" name="cfdi_pdf" accept=".pdf,application/pdf"{upload_disabled}>
                <label style="font-size:12px;font-weight:700;color:#475569;">Partida presupuestal</label>
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

            function syncHidden(input, items) {{
                const hiddenId = input.getAttribute("data-hidden-input");
                const hidden = hiddenId ? document.getElementById(hiddenId) : null;
                if (!hidden) return;
                const selected = items.find(function(item) {{
                    return item.label === input.value;
                }});
                hidden.value = selected ? selected.id : "";
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
                    syncHidden(input, cfdiOptions);
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
            Captura una nueva partida presupuestal para
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
