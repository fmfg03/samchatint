"""Admin HTML rendering for the read-only AR projection."""

from __future__ import annotations

from html import escape
from typing import Any

from .service import build_ar_operational_rows


def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"${amount:,.2f}"


def _text(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return escape(text or fallback)


def _row(cells: list[str]) -> str:
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _empty_row(colspan: int, message: str) -> str:
    return (
        f'<tr><td colspan="{colspan}" class="ar-muted">'
        f"{escape(message)}</td></tr>"
    )


def _status_pill(value: Any) -> str:
    text = str(value or "collection_unknown").strip() or "collection_unknown"
    return f'<span class="ar-pill">{escape(text)}</span>'


def _sort_link(
    *,
    label: str,
    field: str,
    base_url: str,
    current_sort: str,
    current_dir: str,
) -> str:
    next_dir = "asc"
    if current_sort == field and current_dir == "asc":
        next_dir = "desc"
    separator = "&" if "?" in base_url else "?"
    href = f"{base_url}{separator}sort_by={field}&sort_dir={next_dir}"
    marker = ""
    if current_sort == field:
        marker = " ↑" if current_dir == "asc" else " ↓"
    return f'<a class="ar-sort" href="{escape(href)}">{escape(label + marker)}</a>'


def _operational_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(14, "Sin cartera CxC con los filtros seleccionados.")
    return "".join(
        _row(
            [
                _text(item.get("tournament_name")),
                _text(item.get("phase")),
                _text(item.get("concept_name")),
                _text(item.get("payer_name")),
                _text(item.get("payer_rfc")),
                _text(item.get("cfdi_uuid")),
                _text(item.get("issued_date")),
                _text(item.get("due_date")),
                _money(item.get("expected_income_amount")),
                _money(item.get("issued_amount")),
                _money(item.get("linked_income_amount")),
                _money(item.get("collected_amount")),
                (
                    _money(item.get("balance_amount"))
                    if item.get("balance_amount") is not None
                    else '<span class="ar-muted">sin saldo confirmado</span>'
                ),
                _status_pill(item.get("operational_status")),
            ]
        )
        for item in rows
    )


def _expected_income_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(6, "Sin ingreso esperado para la version seleccionada.")
    return "".join(
        _row(
            [
                _text(item.get("tournament_name") or item.get("tournament_code")),
                _text(item.get("phase")),
                _text(item.get("concept_name")),
                _money(item.get("expected_income_amount")),
                _money(item.get("linked_income_amount")),
                _status_pill(item.get("collection_status")),
            ]
        )
        for item in rows
    )


def _linked_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(7, "Sin CFDI de ingreso ligado al presupuesto.")
    return "".join(
        _row(
            [
                _text(item.get("cfdi_uuid")),
                _text(item.get("concept_name")),
                _text(item.get("payer_name")),
                _text(item.get("payer_rfc")),
                _money(item.get("issued_amount")),
                _text(item.get("recognized_income_date")),
                _status_pill(item.get("collection_status")),
            ]
        )
        for item in rows
    )


def _unlinked_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(6, "Sin CFDI PSP candidatos sin liga presupuestal.")
    return "".join(
        _row(
            [
                _text(item.get("cfdi_uuid")),
                _text(item.get("issued_date")),
                _text(item.get("payer_name")),
                _text(item.get("payer_rfc")),
                _money(item.get("issued_amount")),
                _status_pill(item.get("collection_status")),
            ]
        )
        for item in rows
    )


def _collection_gap_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(6, "Sin gaps de cobranza para el alcance actual.")
    return "".join(
        _row(
            [
                _text(item.get("source")),
                _text(item.get("item_id")),
                _text(item.get("payer_name")),
                _text(item.get("payer_rfc")),
                _money(item.get("amount")),
                _status_pill(item.get("collection_status")),
            ]
        )
        for item in rows
    )


def _matching_gap_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(4, "Sin gaps de matching detectados.")
    return "".join(
        _row(
            [
                _text(item.get("severity")),
                _text(item.get("source")),
                _text(item.get("item_id")),
                _text(item.get("reason")),
            ]
        )
        for item in rows
    )


def _candidate_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return '<span class="ar-muted">sin evidencia bancaria candidata</span>'
    candidate = candidates[0]
    signals = ", ".join(candidate.get("signals") or [])
    return (
        f'{_text(candidate.get("bank_movement_id"))}<br>'
        f'{_money(candidate.get("bank_amount"))} · {_text(candidate.get("bank_date"))}'
        f'<br><span class="ar-muted">{escape(signals or "sin señales")}</span>'
    )


def _accept_match_form(
    item: dict[str, Any],
    candidate: dict[str, Any],
    *,
    budget_version_id: str,
    action_base: str,
    return_to: str,
    can_operate_matches: bool = True,
) -> str:
    if not can_operate_matches:
        return '<span class="ar-muted">sin permiso operativo</span>'
    if item.get("status") != "candidate_match":
        return '<span class="ar-muted">revision requerida</span>'
    hidden = {
        "budget_version_id": budget_version_id,
        "ar_item_id": item.get("ar_item_id"),
        "budget_line_id": item.get("budget_line_id"),
        "cfdi_report_id": item.get("cfdi_report_id"),
        "ar_amount": item.get("amount"),
        "payer_rfc": item.get("payer_rfc"),
        "payer_name": item.get("payer_name"),
        "bank_movement_id": candidate.get("bank_movement_id"),
        "bank_amount": candidate.get("bank_amount"),
        "return_to": return_to,
    }
    hidden_html = "".join(
        f'<input type="hidden" name="{escape(key)}" value="{_text(value, "")}">'
        for key, value in hidden.items()
    )
    return f"""
        <form method="POST" action="{escape(action_base)}/matches/accept"
              style="display:grid;gap:6px;min-width:180px;">
            {hidden_html}
            <input name="acceptance_reason" required
                   placeholder="Razon de aceptacion">
            <button class="button secondary" type="submit">Aceptar match</button>
        </form>
    """


def _prematch_rows(
    rows: list[dict[str, Any]],
    *,
    budget_version_id: str,
    action_base: str,
    return_to: str,
    can_operate_matches: bool,
) -> str:
    if not rows:
        return _empty_row(8, "Sin items AR para pre-matching.")
    return "".join(
        (
            lambda candidates: _row(
                [
                    _text(item.get("ar_item_id")),
                    _text(item.get("source")),
                    _text(item.get("payer_name")),
                    _text(item.get("payer_rfc")),
                    _money(item.get("amount")),
                    _candidate_summary(candidates),
                    _status_pill(item.get("status")),
                    _accept_match_form(
                        item,
                        candidates[0] if candidates else {},
                        budget_version_id=budget_version_id,
                        action_base=action_base,
                        return_to=return_to,
                        can_operate_matches=can_operate_matches,
                    ),
                ]
            )
        )(
            list(item.get("candidate_evidence") or [])
        )
        for item in rows
    )


def _unmatched_bank_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(6, "Sin entradas bancarias no vinculadas al alcance AR.")
    return "".join(
        _row(
            [
                _text(item.get("bank_movement_id")),
                _text(item.get("bank_date")),
                _text(item.get("bank_name")),
                _text(item.get("bank_rfc")),
                _money(item.get("bank_amount")),
                _status_pill(item.get("status")),
            ]
        )
        for item in rows
    )


def _accepted_match_rows(
    rows: list[dict[str, Any]],
    *,
    action_base: str,
    return_to: str,
    can_operate_matches: bool,
) -> str:
    if not rows:
        return _empty_row(7, "Sin matches AR aceptados.")
    rendered = []
    for item in rows:
        match_id = _text(item.get("id"), "")
        reverse_form = (
            '<span class="ar-muted">sin permiso operativo</span>'
            if not can_operate_matches
            else f"""
            <form method="POST"
                  action="{escape(action_base)}/matches/{match_id}/reverse"
                  style="display:grid;gap:6px;min-width:180px;">
                <input type="hidden" name="return_to" value="{_text(return_to, "")}">
                <input name="reversal_reason" required
                       placeholder="Razon de reversion">
                <button class="button secondary" type="submit">Revertir</button>
            </form>
        """
        )
        rendered.append(
            _row(
                [
                    _text(item.get("ar_item_id")),
                    _text(item.get("bank_movement_id")),
                    _money(item.get("accepted_amount")),
                    _text(item.get("collection_date") or item.get("accepted_at")),
                    _text(item.get("payer_name")),
                    _status_pill(item.get("status")),
                    reverse_form,
                ]
            )
        )
    return "".join(rendered)


def render_ar_matching_workbench_html(
    payload: dict[str, Any],
    *,
    action_base: str = "/admin/finanzas/cuentas-por-cobrar",
    return_to: str = "",
    can_operate_matches: bool = True,
) -> str:
    """Render AR S3 pre-matching as a read-only admin fragment."""

    summary = payload.get("summary") or {}
    items = list(payload.get("items") or [])
    accepted_matches = list(payload.get("accepted_matches") or [])
    unmatched_bank_inflows = list(payload.get("unmatched_bank_inflows") or [])
    budget_version_id = _text(payload.get("budget_version_id"), "")
    summary_cards = "".join(
        [
            "<div><span>Aceptados</span>"
            f"<strong>{int(summary.get('accepted_match_count') or 0)}</strong></div>",
            "<div><span>Candidatos</span>"
            f"<strong>{int(summary.get('candidate_match_count') or 0)}</strong></div>",
            "<div><span>Revision manual</span>"
            f"<strong>{int(summary.get('manual_match_required_count') or 0)}"
            "</strong></div>",
            "<div><span>Sin evidencia</span>"
            f"<strong>{int(summary.get('collection_unknown_count') or 0)}"
            "</strong></div>",
            "<div><span>Payer gap</span>"
            f"<strong>{int(summary.get('payer_gap_count') or 0)}</strong></div>",
            "<div><span>Banco sin AR</span>"
            f"<strong>{int(summary.get('unmatched_bank_inflow_count') or 0)}"
            "</strong></div>",
        ]
    )
    prematch_header = (
        "<thead><tr><th>AR item</th><th>Fuente</th><th>Receptor</th>"
        "<th>RFC</th><th>Monto</th><th>Evidencia bancaria candidata</th>"
        "<th>Estado</th><th>Accion</th></tr></thead>"
    )
    accepted_header = (
        "<thead><tr><th>AR item</th><th>Movimiento</th><th>Monto</th>"
        "<th>Fecha cobranza</th><th>Receptor</th><th>Estado</th>"
        "<th>Accion</th></tr></thead>"
    )
    bank_header = (
        "<thead><tr><th>Movimiento</th><th>Fecha</th><th>Ordenante</th>"
        "<th>RFC</th><th>Monto</th><th>Estado</th></tr></thead>"
    )
    return f"""
        <section class="workspace-card ar-warning" style="margin-bottom:18px;">
            <div class="workspace-section-title">Pre-matching AR</div>
            <div class="workspace-section-subtitle">
                Evidencia candidata; no prueba cobranza. Esta seccion no acepta
                matches, no escribe en banco y no cambia saldos.
            </div>
            <div class="ar-metrics">{summary_cards}</div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Matches AR aceptados</div>
            <div class="workspace-section-subtitle">
                Autoridad AR dedicada. No cambia conciliacion contable legacy.
            </div>
            <table class="ar-table">
                {accepted_header}
                <tbody>
                    {_accepted_match_rows(
                        accepted_matches,
                        action_base=action_base,
                        return_to=return_to,
                        can_operate_matches=can_operate_matches,
                    )}
                </tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Revision de candidatos</div>
            <table class="ar-table">
                {prematch_header}
                <tbody>
                    {_prematch_rows(
                        items,
                        budget_version_id=budget_version_id,
                        action_base=action_base,
                        return_to=return_to,
                        can_operate_matches=can_operate_matches,
                    )}
                </tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Entradas bancarias sin AR</div>
            <table class="ar-table">
                {bank_header}
                <tbody>{_unmatched_bank_rows(unmatched_bank_inflows)}</tbody>
            </table>
        </section>
    """


def render_ar_read_model_html(
    payload: dict[str, Any],
    *,
    status_filter: str = "todos",
    search: str = "",
    sort_by: str = "issued_date",
    sort_dir: str = "desc",
    base_url: str = "/admin/finanzas/cuentas-por-cobrar",
    export_url: str = "/admin/finanzas/cuentas-por-cobrar/export.xlsx",
) -> str:
    """Render AR S1 as an admin workspace body fragment."""

    summary = payload.get("summary") or {}
    expected_income = list(payload.get("expected_income") or [])
    issued_linked = list(payload.get("issued_linked") or [])
    issued_unlinked = list(payload.get("issued_unlinked") or [])
    collection_gaps = list(payload.get("collection_gaps") or [])
    matching_gaps = list(payload.get("matching_gaps") or [])
    operational_rows = build_ar_operational_rows(
        payload,
        status_filter=status_filter,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    clean_sort_dir = "asc" if str(sort_dir).lower() == "asc" else "desc"

    summary_cards = "".join(
        [
            "<div><span>Ingreso esperado</span>"
            f"<strong>{_money(summary.get('expected_income_total'))}</strong></div>",
            "<div><span>CFDI ligado</span>"
            f"<strong>{_money(summary.get('linked_income_total'))}</strong></div>",
            "<div><span>CFDI PSP no ligado</span>"
            f"<strong>{_money(summary.get('issued_unlinked_total'))}</strong></div>",
            "<div><span>Cobrado comprobado</span>"
            f"<strong>{_money(summary.get('collected_total'))}</strong></div>",
            "<div><span>Saldo</span>"
            f"<strong>{_money(summary.get('balance_total'))}</strong></div>",
            "<div><span>Vencido</span>"
            f"<strong>{_money(summary.get('overdue_total'))}</strong></div>",
            "<div><span>Gaps cobranza</span>"
            f"<strong>{int(summary.get('collection_gap_count') or 0)}</strong></div>",
            "<div><span>Gaps matching</span>"
            f"<strong>{int(summary.get('matching_gap_count') or 0)}</strong></div>",
        ]
    )
    expected_header = (
        "<thead><tr><th>Torneo</th><th>Fase</th><th>Concepto</th>"
        "<th>Esperado</th><th>CFDI ligado</th><th>Cobranza</th></tr></thead>"
    )
    linked_header = (
        "<thead><tr><th>UUID</th><th>Concepto</th><th>Receptor</th>"
        "<th>RFC</th><th>Monto</th><th>Fecha ingreso</th>"
        "<th>Cobranza</th></tr></thead>"
    )
    unlinked_header = (
        "<thead><tr><th>UUID</th><th>Fecha</th><th>Receptor</th>"
        "<th>RFC</th><th>Monto</th><th>Cobranza</th></tr></thead>"
    )
    collection_header = (
        "<thead><tr><th>Fuente</th><th>Item</th><th>Receptor</th>"
        "<th>RFC</th><th>Monto</th><th>Estado</th></tr></thead>"
    )
    matching_header = (
        "<thead><tr><th>Severidad</th><th>Fuente</th><th>Item</th>"
        "<th>Razon</th></tr></thead>"
    )
    operational_header = (
        "<thead><tr>"
        f"<th>{_sort_link(label='Torneo', field='tournament_name', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Fase', field='phase', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Concepto', field='concept_name', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Cliente', field='payer_name', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='RFC', field='payer_rfc', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='UUID', field='cfdi_uuid', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Fecha CFDI', field='issued_date', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Vence', field='due_date', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Presupuestado', field='expected_income_amount', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Facturado', field='issued_amount', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Reconocido', field='linked_income_amount', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Cobrado', field='collected_amount', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Saldo', field='balance_amount', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        f"<th>{_sort_link(label='Estado', field='operational_status', base_url=base_url, current_sort=sort_by, current_dir=clean_sort_dir)}</th>"
        "</tr></thead>"
    )

    return f"""
        <section class="workspace-card ar-warning" style="margin-bottom:18px;">
            <div class="workspace-section-title">Cuentas por Cobrar</div>
            <div class="workspace-section-subtitle">
                Esta vista separa ingreso presupuestado, CFDI emitido, ingreso
                reconocido y cobranza comprobada. Un CFDI vinculado no prueba
                pago; solo los matches aceptados cuentan como cobro.
            </div>
            <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;">
                <a class="button secondary" href="{escape(export_url)}">Descargar Excel CxC</a>
                <a class="button secondary" href="/admin/contabilidad/cuentas-por-cobrar">Vista contable</a>
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Resumen</div>
            <div class="ar-metrics">
                {summary_cards}
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Cartera operativa</div>
            <div class="workspace-section-subtitle">
                Click en cualquier encabezado para ordenar A-Z / Z-A o
                menor-mayor / mayor-menor.
            </div>
            <div class="ar-table-wrap">
                <table class="ar-table">
                    {operational_header}
                    <tbody>{_operational_rows(operational_rows)}</tbody>
                </table>
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Ingreso esperado</div>
            <table class="ar-table">
                {expected_header}
                <tbody>{_expected_income_rows(expected_income)}</tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">CFDI ligado</div>
            <table class="ar-table">
                {linked_header}
                <tbody>{_linked_rows(issued_linked)}</tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">CFDI PSP no ligado</div>
            <table class="ar-table">
                {unlinked_header}
                <tbody>{_unlinked_rows(issued_unlinked)}</tbody>
            </table>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Gaps de cobranza</div>
            <table class="ar-table">
                {collection_header}
                <tbody>{_collection_gap_rows(collection_gaps)}</tbody>
            </table>
        </section>
        <section class="workspace-card">
            <div class="workspace-section-title">Gaps de matching</div>
            <table class="ar-table">
                {matching_header}
                <tbody>{_matching_gap_rows(matching_gaps)}</tbody>
            </table>
        </section>
    """


def ar_admin_styles() -> str:
    """CSS for the AR admin fragment."""

    return """
        .ar-warning {
            border-color:#bfdbfe;
            background:#eff6ff;
        }
        .ar-metrics {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
            gap:12px;
            margin-top:14px;
        }
        .ar-metrics div {
            border:1px solid #e2e8f0;
            border-radius:8px;
            padding:14px;
            background:#ffffff;
        }
        .ar-metrics span {
            display:block;
            color:#64748b;
            font-size:12px;
            font-weight:800;
            text-transform:uppercase;
        }
        .ar-metrics strong {
            display:block;
            margin-top:6px;
            color:#0f172a;
            font-size:1.15rem;
        }
        .ar-table {
            width:100%;
            border-collapse:separate;
            border-spacing:0;
        }
        .ar-table-wrap {
            overflow:auto;
            max-height:68vh;
            margin-top:14px;
        }
        .ar-table th,
        .ar-table td {
            text-align:left;
            padding:11px 12px;
            border-bottom:1px solid #e2e8f0;
            vertical-align:top;
        }
        .ar-table th {
            color:#64748b;
            font-size:11px;
            text-transform:uppercase;
            background:#f8fafc;
            position:sticky;
            top:0;
            z-index:1;
        }
        .ar-sort {
            color:#334155;
            text-decoration:none;
            font-weight:900;
        }
        .ar-muted {
            color:#64748b;
        }
        .ar-pill {
            display:inline-flex;
            padding:4px 8px;
            border-radius:999px;
            background:#e0f2fe;
            color:#075985;
            font-size:11px;
            font-weight:900;
            text-transform:uppercase;
        }
    """
