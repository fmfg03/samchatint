"""Presentation and workbook rendering of the shared cashflow result.

Amounts are supplied by the read model. Neither renderer calculates a second
financial result or interprets absent evidence as a zero balance.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

REPORT_FIELDS = (
    ("opening_balance", "Saldo inicial"),
    ("actual_cash_in", "Orígenes · entradas reales"),
    ("actual_cash_out", "Aplicaciones · salidas reales"),
    ("actual_cash_net", "Variación neta del período"),
    ("closing_balance", "Saldo final"),
)
FUTURE_FIELDS = (
    ("expected_uncollected_income", "Cobros pendientes"),
    ("approved_obligations", "Pagos aprobados / en proceso"),
    ("submitted_obligations", "Solicitudes enviadas"),
    ("forecast_net", "Variación proyectada"),
    ("detected_payables", "CxP detectada sin captura"),
)
CONTEXT_FIELDS = (
    ("planned_budget_income", "Ingresos presupuestados"),
    ("planned_budget_expense", "Egresos presupuestados"),
    ("recognized_income", "Ingreso reconocido contable"),
    ("collected_income", "Cobranza confirmada"),
)
SOURCE_LABELS = {
    "payable_risk": "Riesgo de CxP sin captura",
    "forecast": "Proyección",
    "bank": "Banco",
    "obligations": "Obligaciones",
    "ar": "Cuentas por cobrar",
    "budget": "Presupuesto",
    "bank_movements": "Movimiento bancario importado",
}
WINDOWS = {
    "overdue": "Vencido",
    "undated": "Sin fecha",
    "upcoming": "Próximo",
}


def _money(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "N/D"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "N/D"
    return f"${amount:,.2f}" if amount.is_finite() else "N/D"


def _text(value: Any, fallback: str = "—") -> str:
    return escape(str(value).strip() if value is not None else fallback) or fallback


def _source_label(value: Any) -> Any:
    return SOURCE_LABELS.get(str(value), value)


def _visible_note(value: Any) -> Any:
    if value is None:
        return None
    descriptions = {
        "ar_forecast_basis_and_schedule_unproven": (
            "Proyección no disponible: falta una base comparable y una fecha "
            "prevista de cobro para CxC. Consulte el saldo en Cuentas por Cobrar."
        ),
        "ar_currency_coverage_missing": (
            "Cuentas por cobrar: la moneda no está acreditada en todos los registros."
        ),
        "ar_collection_conflict": (
            "Cuentas por cobrar: hay cobros con evidencia en conflicto; "
            "la proyección requiere conciliación."
        ),
        "budget_distribution_requires_reconciliation": (
            "El calendario de egresos presupuestados requiere conciliación."
        ),
    }
    if str(value) in descriptions:
        return descriptions[str(value)]
    if str(value).startswith("budget_plan_unavailable:"):
        return "El calendario de egresos presupuestados no está disponible."
    text = str(value)
    for name, label in SOURCE_LABELS.items():
        if text.startswith(name + ":"):
            text = label + text[len(name) :]
            break
    for exception in (
        "ProgrammingError",
        "OperationalError",
        "IntegrityError",
        "SQLAlchemyError",
        "DBAPIError",
    ):
        text = text.replace(f" ({exception})", "")
    return text.replace("por el owner", "por la fuente")


def _href(value: Any, fallback: str = "#cashflow-movements") -> str:
    """Only local report navigation can be supplied by the read model."""
    value = str(value or "")
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return fallback
    if not value.startswith(("/", "?", "#")):
        return fallback
    return escape(value, quote=True)


def _currency_label(value: Any) -> str:
    return "Moneda no acreditada" if not value or value == "SIN_MONEDA" else str(value)


def _summary_groups(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] | None = payload.get("summary_by_currency")
    if groups:
        return groups
    return {
        str(payload.get("currency") or "Moneda no acreditada"): (
            payload.get("summary") or {}
        )
    }


def _period_label(row: dict[str, Any]) -> str:
    fallback = f"{row.get('year', '')}-{int(row.get('month') or 0):02d}"
    return str(row.get("key") or fallback)


def _metric(label: str, value: Any, note: str) -> str:
    return (
        '<div class="cashflow-metric">'
        f"<span>{escape(label)}</span><strong>{_money(value)}</strong>"
        f"<small>{escape(note)}</small></div>"
    )


def _report_table(payload: dict[str, Any], currency: str, summary: dict) -> str:
    rows = [
        row
        for row in payload.get("monthly_buckets") or []
        if str(row.get("currency") or currency) == currency
    ]
    headings = "".join(
        f'<th scope="col">{_text(_period_label(row))}</th>' for row in rows
    )
    detail_links = payload.get("detail_links") or {}
    body = []
    for field, label in REPORT_FIELDS:
        cells = []
        for row in rows + [summary]:
            key = _period_label(row) if row is not summary else "total"
            value = _money(row.get(field))
            direction = {"actual_cash_in": "in", "actual_cash_out": "out"}.get(field)
            if direction and value != "N/D":
                href = _href(detail_links.get(f"{currency}:{key}:{direction}"))
                value = (
                    f'<a href="{href}" aria-label="Ver detalle de '
                    f"{escape(label)} · {_text(key)} · "
                    f'{_text(_currency_label(currency))}">{value}</a>'
                )
            cells.append(f'<td class="cashflow-number">{value}</td>')
        body.append(f'<tr><th scope="row">{escape(label)}</th>{"".join(cells)}</tr>')
    return (
        '<div class="table-shell cashflow-table-shell" role="region" tabindex="0" '
        f'aria-label="Flujo real en {_text(_currency_label(currency))}">'
        '<table class="cashflow-table"><caption>'
        f"Flujo de efectivo · {_text(_currency_label(currency))} · pesos</caption>"
        '<thead><tr><th scope="col">Concepto</th>'
        f'{headings}<th scope="col">Acumulado</th></tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def _future_table(summary: dict[str, Any]) -> str:
    return (
        '<dl class="cashflow-future">'
        + "".join(
            f"<div><dt>{escape(label)}</dt><dd>{_money(summary.get(field))}</dd></div>"
            for field, label in FUTURE_FIELDS
        )
        + "</dl>"
    )


def _movement_table(rows: list[dict[str, Any]]) -> str:
    headers = (
        "Fecha",
        "Movimiento",
        "Contraparte",
        "Concepto",
        "Cuenta",
        "Moneda",
        "Importe",
        "Proyecto",
        "Referencia",
        "Conciliación",
        "Fuente",
    )
    body = []
    for row in rows:
        values = [
            _text(row.get("date")),
            {"in": "Entrada", "out": "Salida"}.get(
                str(row.get("direction") or ""), "N/D"
            ),
            _text(row.get("party"), "Sin identificar"),
            _text(row.get("concept"), "Sin clasificar"),
            _text(row.get("account"), "Cuenta sin identificar"),
            _text(_currency_label(row.get("currency"))),
            _money(row.get("amount")),
            _text(row.get("project"), "Sin atribución"),
            _text(row.get("reference")),
            _text(row.get("reconciliation")),
            _text(_source_label(row.get("source")), "Banco"),
        ]
        body.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    empty = '<tr><td colspan="11">Sin movimientos en esta selección.</td></tr>'
    return (
        '<div class="table-shell cashflow-table-shell" role="region" tabindex="0" '
        'aria-label="Detalle de movimientos"><table class="cashflow-table">'
        "<caption>Movimientos que explican las cifras</caption><thead><tr>"
        + "".join(f'<th scope="col">{label}</th>' for label in headers)
        + "</tr></thead><tbody>"
        + ("".join(body) or empty)
        + "</tbody></table></div>"
    )


def _pending_table(rows: list[dict[str, Any]], *, receivables: bool = False) -> str:
    headers = (
        "Referencia",
        "Fecha",
        "Contraparte",
        "Concepto",
        "Estado",
        "Ventana",
        "Moneda",
        "Importe",
    )
    body = []
    for row in rows:
        values = [
            _text(row.get("reference") or row.get("id")),
            _text(row.get("date")),
            _text(row.get("party"), "Sin identificar"),
            _text(row.get("concept"), "Sin clasificar"),
            _text(row.get("state")),
            _text(WINDOWS.get(str(row.get("window") or ""))),
            _text(_currency_label(row.get("currency"))),
            _money(row.get("amount")),
        ]
        body.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    caption = "Cobros pendientes" if receivables else "Obligaciones y solicitudes"
    return (
        '<div class="table-shell cashflow-table-shell" role="region" tabindex="0" '
        f'aria-label="{caption}"><table class="cashflow-table">'
        f"<caption>{caption}</caption><thead><tr>"
        + "".join(f'<th scope="col">{label}</th>' for label in headers)
        + "</tr></thead><tbody>"
        + ("".join(body) or '<tr><td colspan="8">Sin registros disponibles.</td></tr>')
        + "</tbody></table></div>"
    )


def _source_notes(notes: list[Any]) -> str:
    if not notes:
        return (
            "<li>Sin observaciones adicionales. "
            "Consulte la cobertura de cada fuente.</li>"
        )
    return "".join(f"<li>{_text(_visible_note(note))}</li>" for note in notes)


def _coverage(payload: dict[str, Any]) -> str:
    items = []
    for key, status in (payload.get("source_status") or {}).items():
        complete = status.get("available") is True and status.get("complete") is True
        label = "Completa" if complete else "Incompleta / no acreditada"
        items.append(
            f"<li><strong>{_text(_source_label(key))}: {label}</strong>"
            f' · {_text(_visible_note(status.get("reason")), "Sin observaciones")}'
            "</li>"
        )
    return "".join(items) or "<li>Cobertura no acreditada por las fuentes.</li>"


def render_cashflow_planning_html(payload: dict[str, Any]) -> str:
    """Render actual money first, then its evidence and separate future scenarios."""
    period = payload.get("period") or {}
    groups = _summary_groups(payload)
    actual = []
    future = []
    for currency, summary in groups.items():
        display_currency = _currency_label(currency)
        metrics = "".join(
            [
                _metric(
                    "Entradas reales", summary.get("actual_cash_in"), display_currency
                ),
                _metric(
                    "Salidas reales", summary.get("actual_cash_out"), display_currency
                ),
                _metric(
                    "Variación neta", summary.get("actual_cash_net"), display_currency
                ),
            ]
        )
        actual.append(
            '<section class="workspace-card cashflow-section">'
            f"<h2>{_text(_currency_label(currency))}</h2>"
            f'<div class="cashflow-metrics">{metrics}</div>'
            f"{_report_table(payload, currency, summary)}"
            '<p class="cashflow-muted">Saldo inicial y saldo final: N/D cuando '
            "no existe apertura y cobertura bancaria acreditadas. "
            "La variación del período no es saldo disponible.</p>"
            "</section>"
        )
        context = "".join(
            _metric(label, summary.get(field), display_currency)
            for field, label in CONTEXT_FIELDS
        )
        future.append(
            '<section class="workspace-card cashflow-section">'
            f"<h3>{_text(_currency_label(currency))}</h3>{_future_table(summary)}"
            "<details><summary>Presupuesto, facturación y cobranza</summary>"
            f'<div class="cashflow-metrics">{context}</div>'
            "<p>Estas medidas explican distintas etapas. "
            "No se suman como entradas nuevas.</p>"
            "</details></section>"
        )
    details = payload.get("details") or {}
    movement_rows = details.get("rows")
    if movement_rows is None:
        movement_rows = list(payload.get("movements") or [])[:50]
    page = details.get("page", 1)
    pages = details.get("pages", 1)
    total = details.get("total", len(payload.get("movements") or []))
    links = payload.get("page_links") or {}
    navigation = []
    for key, label in (("previous", "Anterior"), ("next", "Siguiente")):
        if links.get(key):
            navigation.append(
                f'<a class="button secondary" href="{_href(links[key])}">'
                f"{label}</a>"
            )
    party_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{value}</td>"
            for value in [
                "Entrada" if row.get("direction") == "in" else "Salida",
                _text(row.get("party"), "Sin identificar"),
                _text(_currency_label(row.get("currency"))),
                _money(row.get("amount")),
                _text(row.get("count")),
            ]
        )
        + "</tr>"
        for row in payload.get("party_groups") or []
    )
    parties = (
        '<div class="table-shell cashflow-table-shell"><table class="cashflow-table">'
        "<caption>Orígenes y destinos por contraparte</caption><thead><tr>"
        '<th scope="col">Movimiento</th><th scope="col">Contraparte</th>'
        '<th scope="col">Moneda</th><th scope="col">Importe</th>'
        '<th scope="col">Movimientos</th>'
        f"</tr></thead><tbody>{party_rows}</tbody></table></div>"
        if party_rows
        else "<p>Consulte la contraparte y el concepto de cada movimiento "
        "en el detalle.</p>"
    )
    company = _text(
        payload.get("company_name")
        or payload.get("company_label")
        or payload.get("company_id"),
        "Ámbito legado · empresa no acreditada",
    )
    selection = _text(payload.get("detail_selection"), "selección actual")
    obligations = _pending_table(list(payload.get("obligations") or []))
    receivables = _pending_table(
        list(payload.get("receivables") or []),
        receivables=True,
    )
    payable_status = (payload.get("source_status") or {}).get("payable_risk") or {}
    payable_reason = _text(
        payable_status.get("reason"),
        "Fuente de CxP sin captura no acreditada para este corte.",
    )
    payable_link = (
        f'<a href="{_href(payload["payables_url"])}">Abrir Cuentas por pagar</a>'
        if payload.get("payables_url")
        else ""
    )
    return f"""
    <section class="workspace-card cashflow-section" id="cashflow-report">
      <h2>Flujo de efectivo ejecutivo</h2>
      <p>Dinero que entra, de dónde viene, dinero que sale y a dónde va.</p>
      <dl class="cashflow-context">
        <div><dt>Empresa</dt><dd>{company}</dd></div>
        <div><dt>Período real</dt>
          <dd>{_text(period.get("start"))} → {_text(period.get("end"))}
          (fin exclusivo)</dd></div>
        <div><dt>Corte del reporte</dt>
          <dd>{_text(period.get("cutoff"), "No acreditado")}</dd></div>
        <div><dt>Estado documental vigente</dt>
          <dd>{_text(payload.get("state_as_of"), "No acreditado")}</dd></div>
        <div><dt>Unidad</dt><dd>Pesos · moneda indicada en cada bloque</dd></div>
      </dl>
      <p>Seleccione una entrada o salida para consultar
        los movimientos que la explican.</p>
    </section>
    {''.join(actual)}
    <section class="workspace-card cashflow-section">
      <h2>Orígenes y destinos</h2>{parties}</section>
    <section class="workspace-card cashflow-section" id="cashflow-movements">
      <details open><summary>Detalle de movimientos · {selection}</summary>
        <p>{_text(total)} movimientos · página {_text(page)} de {_text(pages)}.
          Los totales incluyen todo el universo autorizado.</p>
        {_movement_table(list(movement_rows))}
        <nav class="cashflow-pagination" aria-label="Páginas de movimientos">
          {''.join(navigation)}</nav>
        <a href="#cashflow-report">Volver al reporte</a>
      </details>
    </section>
    <section class="workspace-card cashflow-section"><h2>Lo que viene</h2>
      <p>Del {_text(period.get("forecast_start"))}
      al {_text(period.get("forecast_end"))} (fin exclusivo).
      Cobros y obligaciones se muestran separados del efectivo real.
      Las solicitudes enviadas aún no son pagos autorizados.</p>
      {''.join(future)}
      <details><summary>Obligaciones, vencidos y solicitudes sin fecha</summary>
        {obligations}</details>
      <details><summary>Detalle de cobros pendientes</summary>
        {receivables}</details>
      <h3>Pasivo detectado / CxP sin captura: N/D</h3>
      <p>{payable_reason} {payable_link}</p>
      <p>Los riesgos sin clasificación confirmada
        no se presentan como pagos aprobados.</p>
    </section>
    <section class="workspace-card cashflow-section"><h2>Notas de lectura</h2>
      <h3>Cobertura de las fuentes</h3><ul>{_coverage(payload)}</ul>
      <ul>{_source_notes(list(payload.get("source_notes") or []))}</ul>
    </section>
    """


def _workbook_value(value: Any) -> Any:
    if value is None:
        return "N/D"
    if isinstance(value, Decimal):
        return value if value.is_finite() else "N/D"
    if isinstance(value, str):
        # Text from a bank description must never become an Excel formula.
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    return value


def export_cashflow_xlsx(payload: dict[str, Any]) -> bytes:
    """Export the exact shared read model, including its complete movement set."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    report = workbook.active
    report.title = "Reporte"

    def append(sheet, values):
        sheet.append([_workbook_value(value) for value in values])

    append(report, ["Concepto", "Moneda", "Período", "Importe"])
    period = payload.get("period") or {}
    for label, value in (
        (
            "Empresa",
            payload.get("company_name")
            or payload.get("company_label")
            or payload.get("company_id"),
        ),
        ("Inicio real", period.get("start")),
        ("Fin real exclusivo", period.get("end")),
        ("Corte", period.get("cutoff")),
        ("Inicio proyección", period.get("forecast_start")),
        ("Fin proyección exclusivo", period.get("forecast_end")),
        ("Unidad", "Pesos"),
    ):
        append(report, [label, value])
    for currency, summary in _summary_groups(payload).items():
        rows = [
            row
            for row in payload.get("monthly_buckets") or []
            if str(row.get("currency") or currency) == currency
        ]
        for row in rows:
            for field, label in REPORT_FIELDS + FUTURE_FIELDS + CONTEXT_FIELDS:
                append(report, [label, currency, _period_label(row), row.get(field)])
        for field, label in REPORT_FIELDS + FUTURE_FIELDS + CONTEXT_FIELDS:
            append(report, [label, currency, "Acumulado", summary.get(field)])

    sheets = (
        (
            "Banco",
            payload.get("movements") or [],
            (
                ("date", "Fecha"),
                ("direction", "Movimiento"),
                ("party", "Contraparte"),
                ("concept", "Concepto"),
                ("account", "Cuenta"),
                ("currency", "Moneda"),
                ("amount", "Importe"),
                ("project", "Proyecto"),
                ("reference", "Referencia"),
                ("reconciliation", "Conciliación"),
                ("source", "Fuente"),
                ("id", "ID fuente"),
            ),
        ),
        (
            "Obligaciones",
            payload.get("obligations") or [],
            (
                ("reference", "Referencia"),
                ("date", "Fecha"),
                ("party", "Contraparte"),
                ("concept", "Concepto"),
                ("state", "Estado"),
                ("window", "Ventana"),
                ("currency", "Moneda"),
                ("amount", "Importe"),
                ("project", "Proyecto"),
                ("id", "ID fuente"),
            ),
        ),
        (
            "Cobros pendientes",
            payload.get("receivables") or [],
            (
                ("reference", "Referencia"),
                ("date", "Fecha"),
                ("party", "Contraparte"),
                ("state", "Estado"),
                ("window", "Ventana"),
                ("currency", "Moneda"),
                ("amount", "Importe"),
                ("id", "ID fuente"),
            ),
        ),
    )
    for name, rows, columns in sheets:
        sheet = workbook.create_sheet(name)
        append(sheet, [label for _, label in columns])
        for row in rows:
            append(
                sheet,
                [
                    _source_label(row.get(key)) if key == "source" else row.get(key)
                    for key, _ in columns
                ],
            )
    coverage = workbook.create_sheet("Cobertura")
    append(coverage, ["Fuente", "Disponible", "Completa", "Motivo"])
    for name, status in (payload.get("source_status") or {}).items():
        append(
            coverage,
            [
                _source_label(name),
                status.get("available"),
                status.get("complete"),
                _visible_note(status.get("reason")),
            ],
        )
    for note in payload.get("source_notes") or []:
        append(coverage, ["Observación", None, None, _visible_note(note)])
    for sheet in workbook:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="0F766E")
            cell.font = Font(color="FFFFFF", bold=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(
                max(len(str(cell.value or "")) for cell in column) + 2, 55
            )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def cashflow_admin_styles() -> str:
    """Responsive, keyboard-accessible styles scoped to the cashflow report."""
    return """
    .cashflow-shell {
      max-width:1380px; margin:0 auto; padding:20px;
      font:16px/1.5 Arial,sans-serif; color:#0f172a;
    }
    .cashflow-shell,.cashflow-shell * { box-sizing:border-box; }
    .cashflow-shell .workspace-card {
      min-width:0; background:#fff; border:1px solid #e2e8f0; border-radius:10px;
    }
    .cashflow-section,.cashflow-filters { margin-bottom:18px; padding:20px; }
    .cashflow-filters h1 { margin:0 0 16px; }
    .cashflow-filters form {
      display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
      align-items:end; gap:14px;
    }
    .cashflow-filters label { display:block; min-width:0; font-weight:700; }
    .cashflow-filters input,.cashflow-filters select {
      display:block; width:100%; min-width:0; max-width:100%; margin-top:6px;
      padding:10px; border:1px solid #94a3b8; border-radius:6px; font:inherit;
    }
    .cashflow-filters button {
      min-height:46px; padding:10px 16px; border:0; border-radius:6px;
      background:#0f766e; color:#fff; font:inherit; cursor:pointer;
    }
    .cashflow-filters :focus-visible {
      outline:3px solid #0284c7; outline-offset:3px;
    }
    .cashflow-section h2 { margin:0 0 12px; color:#0f172a; }
    .cashflow-context,.cashflow-future,.cashflow-metrics {
      display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
      gap:12px; margin:16px 0;
    }
    .cashflow-context div,.cashflow-future div,.cashflow-metric {
      padding:14px; border:1px solid #e2e8f0; border-radius:8px; background:#fff;
    }
    .cashflow-context dt,.cashflow-future dt,.cashflow-metric span {
      color:#475569; font-size:13px; font-weight:700;
    }
    .cashflow-context dd,.cashflow-future dd { margin:8px 0 0; }
    .cashflow-metric strong { display:block; font-size:1.4rem; margin:8px 0; }
    .cashflow-metric small { color:#475569; }
    .cashflow-table-shell { overflow-x:auto; max-width:100%; margin:16px 0; }
    .cashflow-table { width:100%; border-collapse:collapse; }
    .cashflow-table caption { text-align:left; padding:10px 0; font-weight:700; }
    .cashflow-table th,.cashflow-table td {
      padding:11px 12px; text-align:left; border-bottom:1px solid #e2e8f0;
      vertical-align:top;
    }
    .cashflow-table thead th { background:#f8fafc; color:#475569; }
    .cashflow-table .cashflow-number { text-align:right; white-space:nowrap; }
    .cashflow-section details { margin:14px 0; }
    .cashflow-section summary { cursor:pointer; font-weight:700; padding:10px 0; }
    .cashflow-muted { color:#475569; font-size:13px; line-height:1.6; }
    .cashflow-pagination { display:flex; flex-wrap:wrap; gap:12px; margin:12px 0; }
    .cashflow-section a { color:#0369a1; text-underline-offset:3px; }
    .cashflow-section :focus-visible { outline:3px solid #0284c7; outline-offset:3px; }
    @media(max-width:600px) {
      .cashflow-shell { padding:10px; }
      .cashflow-section,.cashflow-filters { padding:14px; }
      .cashflow-filters form { grid-template-columns:1fr; }
      .cashflow-context,.cashflow-future,.cashflow-metrics {
        grid-template-columns:1fr;
      }
      .cashflow-table th,.cashflow-table td { padding:9px; }
    }
    """


def render_cashflow_filters(payload: dict[str, Any], action: str) -> str:
    """Render report filters using opaque keys, not account numbers in URLs."""
    filters = payload.get("filters") or {}
    period = payload.get("period") or {}

    def selected(value: Any, current: Any) -> str:
        return " selected" if str(value) == str(current) else ""

    def option(value: Any, label: Any, current: Any) -> str:
        return (
            f'<option value="{escape(str(value), quote=True)}"'
            f"{selected(value, current)}>{escape(str(label))}</option>"
        )

    def field(label: str, name: str, value: Any, attributes: str = "") -> str:
        return (
            f'<label>{escape(label)}<input name="{name}" '
            f'value="{escape(str(value), quote=True)}" {attributes}></label>'
        )

    accounts = option("all", "Todas las cuentas", filters.get("cuenta_bancaria"))
    accounts += "".join(
        option(row["key"], row["label"], filters.get("cuenta_bancaria"))
        for row in payload.get("account_options") or []
    )
    projects = option("all", "Todos los proyectos", filters.get("proyecto_scope"))
    projects += "".join(
        option(row["key"], row["label"], filters.get("proyecto_scope"))
        for row in payload.get("project_options") or []
    )
    currencies = option("all", "Monedas separadas", filters.get("currency"))
    currencies += "".join(
        option(code, code, filters.get("currency"))
        for code in payload.get("summary_by_currency") or {}
        if code != "SIN_MONEDA"
    )
    groups = "".join(
        option(key, label, period.get("grouping"))
        for key, label in (
            ("semanal", "Semanal"),
            ("mensual", "Mensual"),
            ("trimestral", "Trimestral"),
            ("semestral", "Semestral"),
            ("anual", "Anual"),
        )
    )
    fields = [
        field(
            "Año", "year", period.get("year", ""), 'type="number" min="2000" max="2100"'
        ),
        field(
            "Mes inicial",
            "month",
            period.get("month", ""),
            'type="number" min="1" max="12"',
        ),
        field(
            "Meses",
            "horizon_months",
            filters.get("horizon_months") or 1,
            'type="number" min="1" max="24"',
        ),
        '<label>Agrupación<select name="periodo" '
        f'aria-label="Agrupación">{groups}</select></label>',
        '<label>Cuenta<select name="cuenta_bancaria" '
        f'aria-label="Cuenta">{accounts}</select></label>',
        '<label>Proyecto<select name="proyecto_scope" '
        f'aria-label="Proyecto">{projects}</select></label>',
        '<label>Moneda<select name="currency" '
        f'aria-label="Moneda">{currencies}</select></label>',
    ]
    if filters.get("horizon_days") is not None:
        fields.append(
            field(
                "Horizonte en días",
                "horizon_days",
                filters["horizon_days"],
                'type="number" min="15" max="180"',
            )
        )
    fields.append(
        field(
            "Días de crédito CxC",
            "dias_credito",
            filters.get("dias_credito", 30),
            'type="number" min="0" max="365"',
        )
    )
    version_id = filters.get("budget_version_id") or payload.get("budget_version_id")
    versions = list(payload.get("budget_version_options") or [])
    budget_options = "".join(
        option(
            row["id"],
            str(row.get("version_name") or "Nombre no disponible")
            + " · "
            + str(row.get("status") or "Estado no disponible"),
            version_id,
        )
        for row in versions
    )
    if version_id and not any(str(row["id"]) == str(version_id) for row in versions):
        budget_options += option(
            version_id,
            "Versión seleccionada · nombre no disponible",
            version_id,
        )
    if not budget_options:
        budget_options = option("", "Sin versiones disponibles", "")
    fields.append(
        '<label>Versión presupuestal<select name="budget_version_id" '
        f'aria-label="Versión presupuestal">{budget_options}</select></label>'
    )
    return (
        '<section class="workspace-card cashflow-filters" '
        'aria-label="Filtros del reporte"><h1>Flujo de efectivo</h1>'
        f'<form method="get" action="{_href(action)}">'
        f'<input type="hidden" name="limit" '
        f'value="{int(filters.get("limit") or 50)}">'
        + "".join(fields)
        + '<button type="submit">Actualizar reporte</button></form>'
        f'<p><a href="{_href(payload.get("export_url"), "#cashflow-report")}">'
        "Descargar Excel completo</a></p></section>"
    )
