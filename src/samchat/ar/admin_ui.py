"""Admin HTML rendering for the read-only AR projection."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

from .service import (
    build_ar_accounting_preview,
    build_ar_actionable_gaps,
    build_ar_operational_rows,
)


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


def _operational_rows(rows: list[dict[str, Any]], *, return_to: str = "") -> str:
    if not rows:
        return _empty_row(15, "Sin cartera CxC con los filtros seleccionados.")
    context_query = ""
    if "?" in return_to:
        context_query = return_to.split("?", 1)[1]
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
                (
                    '<a class="button secondary compact" href="'
                    f'/admin/finanzas/cuentas-por-cobrar/item/'
                    f'{quote(str(item.get("ar_item_id") or ""), safe="")}'
                    f'?{context_query + "&" if context_query else ""}'
                    f'return_to={quote(return_to or "", safe="")}'
                    '">Ver detalle</a>'
                ),
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


def _actionable_gap_rows(rows: list[dict[str, Any]], *, return_to: str = "") -> str:
    if not rows:
        return _empty_row(9, "Sin pendientes CxC accionables para el alcance actual.")
    context_query = ""
    if "?" in return_to:
        context_query = return_to.split("?", 1)[1]
    return "".join(
        _row(
            [
                _status_pill(item.get("priority")),
                _text(item.get("gap_type")),
                _status_pill(item.get("operational_status")),
                _text(item.get("payer_name")),
                _text(item.get("payer_rfc")),
                _text(item.get("tournament_name")),
                _text(item.get("concept_name")),
                _money(item.get("amount")),
                (
                    f'{_text(item.get("suggested_action"))}<br>'
                    '<a class="button secondary compact" href="'
                    f'/admin/finanzas/cuentas-por-cobrar/item/'
                    f'{quote(str(item.get("ar_item_id") or ""), safe="")}'
                    f'?{context_query + "&" if context_query else ""}'
                    f'return_to={quote(return_to or "", safe="")}'
                    '">Ver detalle</a>'
                ),
            ]
        )
        for item in rows
    )


def _detail_metric(label: str, value: Any) -> str:
    return (
        '<div class="ar-detail-metric">'
        f"<span>{escape(label)}</span>"
        f"<strong>{_text(value)}</strong>"
        "</div>"
    )


def _detail_money(label: str, value: Any) -> str:
    return (
        '<div class="ar-detail-metric">'
        f"<span>{escape(label)}</span>"
        f"<strong>{_money(value)}</strong>"
        "</div>"
    )


def _detail_gap_items(item: dict[str, Any], gaps: list[dict[str, Any]]) -> str:
    item_id = str(item.get("ar_item_id") or "")
    messages: list[str] = []
    if item.get("source") == "expected_income":
        messages.append("Falta vincular CFDI a la partida de ingreso.")
    if not item.get("payer_name") and not item.get("payer_rfc"):
        messages.append("Falta cliente/RFC.")
    if not item.get("concept_name") and item.get("source") != "issued_unlinked":
        messages.append("Falta partida de ingreso.")
    if item.get("collection_status") in {None, "", "collection_unknown"}:
        messages.append("Falta comprobar cobranza con match aceptado.")
    if item.get("operational_status") == "Revisión sobrepago":
        messages.append("Sobrepago requiere revisión manual contable.")
    if item.get("issued_amount") and not item.get("due_date"):
        messages.append("Sin fecha de vencimiento confiable.")
    for gap in gaps:
        if str(gap.get("item_id") or "") == item_id:
            messages.append(str(gap.get("reason") or "Gap sin clasificar."))
    if not messages:
        return '<li class="ar-muted">Sin gaps accionables detectados.</li>'
    return "".join(f"<li>{escape(message)}</li>" for message in dict.fromkeys(messages))


def _policy_side_summary(lines: list[dict[str, Any]], side: str) -> str:
    selected = [line for line in lines if str(line.get("side") or "") == side]
    if not selected:
        return '<span class="ar-muted">-</span>'
    return "<br>".join(
        (
            f'{_text(line.get("account_code"))} '
            f'<span class="ar-muted">{_text(line.get("label"))}</span> '
            f'{_money(line.get("amount"))}'
        )
        for line in selected
    )


def _policy_issue_summary(preview: dict[str, Any]) -> str:
    issues = list(preview.get("gaps") or []) + list(preview.get("warnings") or [])
    if not issues:
        return '<span class="ar-muted">Sin gaps.</span>'
    return "<br>".join(_text(issue) for issue in issues)


def _accounting_preview_rows(preview: dict[str, Any]) -> str:
    policies = [
        ("Factura", preview.get("invoice_policy_preview") or {}),
        ("Cobro", preview.get("collection_policy_preview") or {}),
    ]
    return "".join(
        _row(
            [
                _text(label),
                _status_pill(policy.get("status")),
                _policy_side_summary(list(policy.get("lines") or []), "debe"),
                _policy_side_summary(list(policy.get("lines") or []), "haber"),
                _policy_issue_summary(policy),
            ]
        )
        for label, policy in policies
    )


def _detail_action_links(item: dict[str, Any], *, return_to: str) -> str:
    links = [
        f'<a class="button secondary" href="{escape(return_to)}">Volver a CxC</a>',
        (
            '<a class="button secondary" '
            f'href="{escape(return_to)}#prematching">Ir a pre-matching</a>'
        ),
    ]
    budget_version_id = str(item.get("budget_version_id") or "").strip()
    tournament_key = str(
        item.get("tournament_id")
        or item.get("tournament_code")
        or item.get("tournament_name")
        or ""
    ).strip()
    if tournament_key:
        query_parts = []
        if budget_version_id:
            query_parts.append(f"version_id={quote(budget_version_id, safe='')}")
        query_parts.append("budget_view=income")
        budget_href = (
            f"/admin/presupuestos/torneo/{quote(tournament_key, safe='')}"
            f"?{'&'.join(query_parts)}#presupuesto-ingresos"
        )
        links.append(
            '<a class="button secondary" '
            f'href="{escape(budget_href)}">Presupuesto ingresos</a>'
        )
        accounting_query = []
        if item.get("tournament_id"):
            accounting_query.append(
                f"torneo_id={quote(str(item.get('tournament_id')), safe='')}"
            )
        if budget_version_id:
            accounting_query.append(
                f"budget_version_id={quote(budget_version_id, safe='')}"
            )
        accounting_href = "/admin/contabilidad/cuentas-por-cobrar"
        if accounting_query:
            accounting_href = f"{accounting_href}?{'&'.join(accounting_query)}"
        links.append(
            '<a class="button secondary" '
            f'href="{escape(accounting_href)}">Vista contable CxC</a>'
        )
    return "".join(links)


def render_ar_item_detail_html(
    item: dict[str, Any],
    payload: dict[str, Any],
    *,
    return_to: str,
    can_operate_matches: bool,
) -> str:
    """Render one CxC operational item detail from the AR read model."""

    gaps = list(payload.get("matching_gaps") or []) + list(
        payload.get("collection_gaps") or []
    )
    status = item.get("operational_status") or item.get("status")
    collection_status = item.get("collection_status") or "collection_unknown"
    balance = (
        _money(item.get("balance_amount"))
        if item.get("balance_amount") is not None
        else "sin saldo confirmado"
    )
    navigation_actions = _detail_action_links(item, return_to=return_to)
    accounting_preview = build_ar_accounting_preview(item, payload)
    mutable_actions = ""
    if can_operate_matches:
        mutable_actions = """
            <span class="ar-muted">Acciones operativas habilitadas para CxC.</span>
        """
        if item.get("collection_match_id"):
            mutable_actions += (
                '<span class="ar-muted">Match aceptado: '
                f'{_text(item.get("collection_match_id"))}</span>'
            )
    else:
        mutable_actions = '<span class="ar-muted">Sin permiso operativo para acciones mutables.</span>'

    return f"""
        <section class="workspace-card ar-warning" style="margin-bottom:18px;">
            <div class="workspace-section-title">Detalle de Cuentas por Cobrar</div>
            <div class="workspace-section-subtitle">
                Detalle construido desde el mismo read model de la tabla CxC.
            </div>
            <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;">
                {navigation_actions}
                {mutable_actions}
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">{_text(status)}</div>
            <div class="ar-detail-grid" style="margin-top:14px;">
                {_detail_metric("Cliente/pagador", item.get("payer_name"))}
                {_detail_metric("RFC", item.get("payer_rfc"))}
                {_detail_metric("UUID CFDI", item.get("cfdi_uuid"))}
                {_detail_metric("Saldo", balance)}
            </div>
        </section>
        <section class="ar-detail-sections">
            <div class="workspace-card">
                <div class="workspace-section-title">CFDI</div>
                <div class="ar-detail-grid">
                    {_detail_metric("UUID", item.get("cfdi_uuid"))}
                    {_detail_metric("Fecha CFDI", item.get("issued_date"))}
                    {_detail_money("Monto facturado", item.get("issued_amount"))}
                    {_detail_metric("RFC emisor", item.get("emisor_rfc"))}
                    {_detail_metric("Nombre emisor", item.get("emisor_nombre"))}
                    {_detail_metric("RFC receptor", item.get("payer_rfc"))}
                    {_detail_metric("Nombre receptor", item.get("payer_name"))}
                </div>
            </div>
            <div class="workspace-card">
                <div class="workspace-section-title">Presupuesto</div>
                <div class="ar-detail-grid">
                    {_detail_metric("Torneo/proyecto", item.get("tournament_name") or item.get("tournament_code"))}
                    {_detail_metric("Fase", item.get("phase"))}
                    {_detail_metric("Concepto/partida", item.get("concept_name"))}
                    {_detail_money("Monto presupuestado", item.get("expected_income_amount"))}
                    {_detail_money("Monto reconocido", item.get("linked_income_amount"))}
                    {_detail_metric("Versión", item.get("budget_version_id"))}
                </div>
            </div>
            <div class="workspace-card">
                <div class="workspace-section-title">Cobranza</div>
                <div class="ar-detail-grid">
                    {_detail_money("Cobrado comprobado", item.get("collected_amount"))}
                    {_detail_metric("Fecha cobranza", item.get("collection_date"))}
                    {_detail_metric("Match aceptado", item.get("collection_match_id"))}
                    {_detail_metric("Estado cobranza", collection_status)}
                    {_detail_metric("Vencimiento", item.get("due_date"))}
                    {_detail_metric("Saldo", balance)}
                </div>
                <div class="workspace-section-subtitle">
                    {"" if item.get("collection_match_id") else "La cobranza no está comprobada: falta match aceptado."}
                </div>
            </div>
            <div class="workspace-card">
                <div class="workspace-section-title">Gaps / Siguientes Acciones</div>
                <ul class="ar-gap-list">{_detail_gap_items(item, gaps)}</ul>
            </div>
            <div class="workspace-card">
                <div class="workspace-section-title">Prepólizas CxC</div>
                <table class="ar-table">
                    <thead><tr><th>Tipo</th><th>Estado</th><th>Debe</th><th>Haber</th><th>Gaps</th></tr></thead>
                    <tbody>{_accounting_preview_rows(accounting_preview)}</tbody>
                </table>
            </div>
        </section>
    """


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
        <section class="workspace-card ar-warning" id="prematching" style="margin-bottom:18px;">
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
    return_to: str = "",
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
    actionable_gaps = build_ar_actionable_gaps(payload)
    actionable_header = (
        "<thead><tr><th>Prioridad</th><th>Tipo</th><th>Estado</th>"
        "<th>Cliente</th><th>RFC</th><th>Torneo/proyecto</th>"
        "<th>Concepto</th><th>Monto</th><th>Accion sugerida</th></tr></thead>"
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
        "<th>Detalle</th>"
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
            <div class="workspace-section-title">Gaps accionables</div>
            <div class="workspace-section-subtitle">
                Pendientes CxC priorizados desde la misma cartera operativa.
            </div>
            <div class="ar-table-wrap">
                <table class="ar-table">
                    {actionable_header}
                    <tbody>{_actionable_gap_rows(actionable_gaps, return_to=return_to or base_url)}</tbody>
                </table>
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
                    <tbody>{_operational_rows(operational_rows, return_to=return_to or base_url)}</tbody>
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
        .button.compact {
            padding:7px 10px;
            border-radius:10px;
            font-size:12px;
            white-space:nowrap;
        }
        .ar-detail-grid {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
            gap:12px;
            margin-top:12px;
        }
        .ar-detail-sections {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
            gap:16px;
            margin-bottom:18px;
        }
        .ar-detail-metric {
            border:1px solid #e2e8f0;
            border-radius:8px;
            background:#fff;
            padding:12px;
        }
        .ar-detail-metric span {
            display:block;
            color:#64748b;
            font-size:11px;
            font-weight:900;
            text-transform:uppercase;
        }
        .ar-detail-metric strong {
            display:block;
            margin-top:6px;
            color:#0f172a;
            font-size:14px;
            overflow-wrap:anywhere;
        }
        .ar-gap-list {
            margin:12px 0 0 18px;
            color:#334155;
            line-height:1.55;
        }
    """
