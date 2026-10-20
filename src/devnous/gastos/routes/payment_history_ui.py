"""HTML for the read-only payment history; ordering always happens server-side."""

from decimal import Decimal
from html import escape
from typing import Any, Mapping
from urllib.parse import urlencode

HISTORY_PATH = "/admin/finanzas/payment-history"
FILTER_DEFAULTS = {
    "status": "todas",
    "date_kind": "programacion",
    "sort": "referencia_operaciones",
    "direction": "desc",
    "page": "1",
    "month": "",
    "date_from": "",
    "date_to": "",
    "employee": "",
    "requester": "",
    "beneficiary": "",
    "supplier": "",
    "amount_min": "",
    "amount_max": "",
    "q": "",
}


def history_link(values: Mapping[str, str], **changes: Any) -> str:
    return escape(HISTORY_PATH + "?" + urlencode({**values, **changes}), quote=True)


def _money(value: Any, currency: str | None) -> str:
    if value is None:
        return "No disponible"
    return escape(f"{currency or 'Moneda no disponible'} ${Decimal(str(value)):,.2f}")


def render_history(
    values: Mapping[str, str], payload: dict[str, Any], error: str = ""
) -> str:
    """Render escaped filters, full-result summaries and global sort links."""

    def input_field(name: str, label: str, kind: str = "text") -> str:
        extra = ' min="0" step="0.01"' if kind == "number" else ""
        return f'<label>{label}<input name="{name}" type="{kind}" value="{escape(values[name], quote=True)}"{extra}></label>'

    def select_field(name: str, label: str, options: list[tuple[str, str]]) -> str:
        rendered = "".join(
            f'<option value="{key}" {"selected" if values[name] == key else ""}>{text}</option>'
            for key, text in options
        )
        return f'<label>{label}<select name="{name}" aria-label="{label}">{rendered}</select></label>'

    fields = select_field(
        "status",
        "Estado",
        [
            ("todas", "Todos"),
            ("pendientes", "Pendientes de pagar"),
            ("programadas", "Programados / vencidos"),
            ("cerradas", "En proceso de pago"),
            ("pagadas", "Pagados"),
        ],
    )
    fields += select_field(
        "date_kind",
        "Filtrar por fecha de",
        [
            ("solicitud", "Solicitud"),
            ("programacion", "Programación"),
            ("pago", "Pago"),
        ],
    )
    for name, label, kind in [
        ("month", "Mes y año", "month"),
        ("date_from", "Desde", "date"),
        ("date_to", "Hasta", "date"),
        ("employee", "Empleado (solicitante o beneficiario)", "text"),
        ("requester", "Solicitante", "text"),
        ("beneficiary", "Empleado beneficiario", "text"),
        ("supplier", "Proveedor", "text"),
        ("amount_min", "Monto mínimo", "number"),
        ("amount_max", "Monto máximo", "number"),
        ("q", "Referencia o concepto", "search"),
    ]:
        fields += input_field(name, label, kind)
    headers = []
    for key, label in [
        ("solicitud", "Solicitud / concepto"),
        ("referencia_operaciones", "Referencia Operaciones"),
        ("monto", "Monto"),
        ("estado", "Estado"),
        ("solicitante", "Solicitante"),
        ("beneficiario", "Empleado beneficiario"),
        ("proveedor", "Proveedor"),
        ("fecha_solicitud", "Fecha solicitud"),
        ("fecha_aprobacion", "Fecha aprobación"),
        ("fecha_programacion", "Fecha programación"),
        ("fecha_pagada", "Fecha pagada"),
    ]:
        active = values["sort"] == key
        direction = "asc" if active and values["direction"] == "desc" else "desc"
        arrow = (" ↑" if values["direction"] == "asc" else " ↓") if active else " ↕"
        aria = (
            ("ascending" if values["direction"] == "asc" else "descending")
            if active
            else "none"
        )
        headers.append(
            f'<th aria-sort="{aria}"><a href="{history_link(values, sort=key, direction=direction, page=1)}">{label}{arrow}</a></th>'
        )
    rows = []
    for row in payload["rows"]:

        def value(key: str) -> str:
            raw = row.get(key)
            return escape(str(raw)) if raw is not None and str(raw) else "No disponible"

        cells = [
            f'<td><a href="/documentos/{escape(str(row["id"]), quote=True)}">{value("numero_referencia")}</a><div>{value("concepto_pago")}</div></td>',
            f'<td>{value("referencia_operaciones")}</td>',
            f'<td>{_money(row.get("monto"), row.get("currency"))}</td>',
            f'<td>{value("status")}</td>',
            f'<td>{value("solicitante_nombre")}</td>',
            f'<td>{value("beneficiario_nombre")}</td>',
            f'<td>{value("proveedor_nombre")}</td>',
        ]
        for key in ("creado_en", "aprobado_en", "fecha_pago", "pagado_en"):
            cells.append(
                f'<td>{escape(str(row[key])[:10]) if row.get(key) else "No disponible"}</td>'
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    summaries = []
    for total in payload["totals"]:
        paid = "0" if not total["paid_count"] else total["paid"]
        pending = "0" if not total["pending_count"] else total["pending"]
        summaries.append(
            f"""<div class="workspace-card"><strong>{escape(total['currency'] or 'Moneda no disponible')}</strong>
            <p>Pagado: {_money(paid, total['currency'])} · {total['paid_count']} solicitudes</p>
            <p>Pendiente: {_money(pending, total['currency'])} · {total['pending_count']} solicitudes</p>
            <p>{total['missing_amounts']} solicitudes sin monto disponible. Los importes conocidos se suman; faltantes excluidos.</p></div>"""
        )
    count, page = int(payload["total_count"]), int(payload["current_page"])
    pages = max(1, (count + 49) // 50)
    pagination = f"<span>Página {page} de {pages} · {count} solicitudes</span>"
    if page > 1:
        pagination += f' <a class="button secondary" href="{history_link(values, page=page - 1)}">Anterior</a>'
    if page < pages:
        pagination += f' <a class="button secondary" href="{history_link(values, page=page + 1)}">Siguiente</a>'
    alert = (
        f'<p role="alert" class="alert alert-error">{escape(error)}</p>'
        if error
        else ""
    )
    return f"""<h1>Pagado y pendiente</h1>
        <p>Consulta de solicitudes aprobadas y pagos. Un corte en proceso continúa pendiente hasta confirmar el pago.</p>
        {alert}<form method="GET" action="{HISTORY_PATH}" class="history-filters">
        {fields}<input type="hidden" name="sort" value="{escape(values['sort'], quote=True)}">
        <input type="hidden" name="direction" value="{escape(values['direction'], quote=True)}">
        <button class="button" type="submit">Filtrar</button>
        <a class="button secondary" href="{HISTORY_PATH}">Limpiar filtros</a></form>
        <p>El mes y el rango de fechas se combinan sobre la fecha elegida. Los registros sin esa fecha se excluyen al filtrar.</p>
        <section aria-label="Totales de todos los resultados filtrados" class="history-summary">{''.join(summaries)}</section>
        <p>Totales de todos los resultados filtrados, separados por moneda. Haz clic en un encabezado para cambiar el orden de todos los resultados.</p>
        <nav aria-label="Paginación">{pagination}</nav>
        <div style="overflow-x:auto"><table class="payment-table"><thead><tr>{''.join(headers)}</tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan="11">Sin solicitudes para estos filtros.</td></tr>'}</tbody></table></div>"""
