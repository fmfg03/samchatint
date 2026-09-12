"""Executive-grade HTML renderer for the internal Direction dossier."""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Optional
from urllib.parse import quote


def _text(value: object, fallback: str = "Sin información registrada") -> str:
    rendered = str(value or "").strip()
    return escape(rendered if rendered else fallback)


def _money(value: object) -> str:
    """Format a real monetary value without manufacturing zero from absence."""
    if value is None or value == "":
        return "No disponible"
    try:
        return "${:,.2f}".format(float(value))
    except (TypeError, ValueError):
        return "No disponible"


def _rows(items: Iterable[str]) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    return "".join(f"<li>{escape(value)}</li>" for value in values) or (
        "<li>Sin pendientes identificados.</li>"
    )


def _status(value: object) -> str:
    key = str(value or "pending_data").strip()
    label = {
        "available": "Fuente disponible",
        "with_data": "Con datos",
        "usable": "Utilizable",
        "partial": "Parcial",
        "needs_data": "Captura pendiente",
        "pending_data": "Captura pendiente",
        "unavailable": "Fuente no disponible",
        "edition_unavailable": "Edición no disponible",
    }.get(key, key.replace("_", " ").title())
    return f'<span class="status status-{escape(key)}">{escape(label)}</span>'


def _number(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _text(value, "—")


def _pct(numerator: Optional[float], denominator: Optional[float]) -> str:
    if numerator is None or denominator in {None, 0}:
        return "—"
    return f"{(float(numerator) / float(denominator)) * 100:.0f}%"


def _kpi(
    label: str,
    value: object,
    *,
    note: str = "",
    emphasis: bool = False,
) -> str:
    klass = "kpi kpi-primary" if emphasis else "kpi"
    return (
        f'<article class="{klass}"><span>{escape(label)}</span>'
        f"<strong>{_money(value)}</strong>"
        f"<small>{escape(note)}</small></article>"
    )


def _budget_kpis(card: dict[str, Any]) -> str:
    budget = card.get("budget")
    actual = card.get("actual")
    committed = card.get("committed")
    paid = card.get("paid")
    projected = card.get("projected")
    paid_note = (
        "Estado documental pagado/cerrado o fecha de pago registrada; "
        "no equivale por sí solo a salida de caja"
        if paid is not None
        else "Sin total pagado acreditado"
    )
    available = card.get("available")
    if (
        available is None
        and budget is not None
        and actual is not None
        and committed is not None
    ):
        available = float(budget) - max(float(actual), float(committed))

    if card.get("budget_source_status") == "unavailable":
        note = "La fuente presupuestal no está reconciliada con este torneo."
    elif card.get("budget_scope_bridge"):
        note = (
            "Partidas históricas reconciliadas por identidad canónica y alias validado."
        )
    else:
        note = "Presupuesto y actuals desde las fuentes canónicas de SamChat."

    return f"""
    <section class="kpi-section" aria-label="Resumen financiero">
      <div class="kpi-grid">
        {_kpi('Presupuesto', budget, note='Base autorizada', emphasis=True)}
        {_kpi('Ejercido', actual, note=f'{_pct(actual, budget)} del presupuesto')}
        {_kpi('Comprometido', committed, note=f'{_pct(committed, budget)} del presupuesto')}
        {_kpi('Cerrado / pagado', paid, note=paid_note)}
        {_kpi('Disponible', available, note='Presupuesto menos mayor uso reconocido')}
        {_kpi('Proyección', projected, note='Cierre estimado')}
      </div>
      <p class="source-note">{escape(note)}</p>
    </section>
    """


def _alerts(card: dict[str, Any]) -> str:
    alerts = [item for item in list(card.get("alerts") or []) if isinstance(item, dict)]
    if not alerts:
        return ""
    items = "".join(
        f'<li><span class="alert-dot alert-{escape(str(item.get("severity") or "info"))}"></span>'
        f'<strong>{_text(item.get("title"), "Alerta")}</strong></li>'
        for item in alerts
    )
    return f"""
    <section class="attention" aria-label="Asuntos que requieren atención">
      <div><span class="eyebrow">Atención ejecutiva</span><h3>Asuntos que requieren seguimiento</h3></div>
      <ul>{items}</ul>
    </section>
    """


def _breakdown_rows(items: list[dict[str, Any]], limit: int = 8) -> str:
    rows = []
    for item in items[:limit]:
        budget = item.get("budget_total")
        actual = item.get("actual_total")
        committed = item.get("committed_total")
        rows.append(
            "<tr>"
            f"<td>{_text(item.get('label'), 'Sin partida')}</td>"
            f"<td class='money'>{_money(budget)}</td>"
            f"<td class='money'>{_money(actual)}</td>"
            f"<td class='money'>{_money(committed)}</td>"
            f"<td>{_pct(actual, budget)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _account_breakdown_rows(items: list[dict[str, Any]], limit: int = 6) -> str:
    """Render account budgets without inventing account-level actuals."""
    rows = []
    for item in items[:limit]:
        rows.append(
            "<tr>"
            f"<td>{_text(item.get('label'), 'Sin cuenta')}</td>"
            f"<td class='money'>{_money(item.get('budget_total'))}</td>"
            "<td class='money'>No disponible</td>"
            "<td class='money'>No disponible</td>"
            "<td>—</td>"
            "</tr>"
        )
    return "".join(rows)


def _budget_detail(card: dict[str, Any]) -> str:
    if card.get("budget_source_status") == "unavailable":
        return """
        <section class="panel compact-panel">
          <div class="section-heading">
            <div><span class="eyebrow">Presupuesto y contabilidad</span><h2>Partidas presupuestales</h2></div>
            <span class="status status-unavailable">Fuente no disponible</span>
          </div>
          <p class="empty-copy">No se muestran ceros: la versión presupuestal existe, pero todavía no pudo acreditarse el alcance de este torneo.</p>
        </section>
        """

    breakdowns = card.get("budget_breakdowns")
    breakdowns = breakdowns if isinstance(breakdowns, dict) else {}
    concepts = [
        item
        for item in list(breakdowns.get("by_concept") or [])
        if isinstance(item, dict)
    ]
    accounts = [
        item
        for item in list(breakdowns.get("by_account") or [])
        if isinstance(item, dict)
    ]
    concept_rows = _breakdown_rows(concepts)
    account_rows = _account_breakdown_rows(accounts)
    if not concept_rows and not account_rows:
        return ""

    bridge = (
        card.get("budget_scope_bridge")
        if isinstance(card.get("budget_scope_bridge"), dict)
        else {}
    )
    bridge_badge = (
        '<span class="status status-partial">Identidad reconciliada</span>'
        if bridge
        else '<span class="status status-available">Fuente disponible</span>'
    )
    concepts_html = (
        f"""
        <div>
          <div class="subheading"><h3>Partidas presupuestales</h3><span>{len(concepts)} partidas</span></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Partida</th><th>Presupuesto</th><th>Ejercido</th><th>Comprometido</th><th>Uso</th></tr></thead>
            <tbody>{concept_rows}</tbody>
          </table></div>
        </div>
        """
        if concept_rows
        else ""
    )
    accounts_html = (
        f"""
        <div>
          <div class="subheading"><h3>Cuentas contables</h3><span>{len(accounts)} cuentas</span></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Cuenta</th><th>Presupuesto</th><th>Ejercido</th><th>Comprometido</th><th>Uso</th></tr></thead>
            <tbody>{account_rows}</tbody>
          </table></div>
          <p class="section-note">Ejercido y comprometido por cuenta contable aún no están acreditados por la fuente canónica; se muestran como no disponibles, no como cero.</p>
        </div>
        """
        if account_rows
        else ""
    )
    return f"""
    <section class="panel finance-panel">
      <div class="section-heading">
        <div><span class="eyebrow">Presupuesto y contabilidad</span><h2>Presupuesto vs. real</h2></div>
        {bridge_badge}
      </div>
      <div class="finance-split">{concepts_html}{accounts_html}</div>
    </section>
    """


def _entity_detail(entity: dict[str, Any], index: int) -> str:
    operations = dict(entity.get("operations") or {})
    finance = dict(entity.get("finance") or {})
    summary = dict(operations.get("summary") or {})
    contacts = list(operations.get("entity_contacts") or [])
    team_groups = list(operations.get("real_teams_by_category_gender") or [])
    player_groups = list(operations.get("players_by_category_age_gender") or [])

    contacts_html = (
        "".join(f"""
        <tr>
          <td>{_text(contact.get('name'), 'Sin nombre')}</td>
          <td>{_text(contact.get('phone'))}</td>
          <td>{_text(contact.get('email'))}</td>
        </tr>
        """ for contact in contacts)
        or '<tr><td colspan="3">Sin responsable de la entidad registrado.</td></tr>'
    )
    teams_html = (
        "".join(f"""
        <tr>
          <td>{_text(row.get('category'), 'Sin categoría')}</td>
          <td>{_text(row.get('gender_or_branch'), 'Sin género/rama')}</td>
          <td>{int(row.get('teams_count') or 0)}</td>
          <td>{int(row.get('players_count') or 0)}</td>
          <td>{_text(', '.join(row.get('team_names') or []))}</td>
        </tr>
        """ for row in team_groups)
        or '<tr><td colspan="5">Sin equipos participantes registrados.</td></tr>'
    )
    players_html = (
        "".join(f"""
        <tr>
          <td>{_text(row.get('category'), 'Sin categoría')}</td>
          <td>{_text(row.get('gender_or_branch'), 'Sin género/rama')}</td>
          <td>{_text(row.get('age'), 'Edad no disponible')}</td>
          <td>{int(row.get('players_count') or 0)}</td>
        </tr>
        """ for row in player_groups)
        or '<tr><td colspan="4">Sin jugadores registrados.</td></tr>'
    )

    return f"""
    <details class="entity" {'open' if index == 0 else ''}>
      <summary>
        <div><strong>{_text(entity.get('entity_name'), 'Entidad sin nombre')}</strong><span>Detalle operativo</span></div>
        <span class="summary-meta">{int(summary.get('teams_count') or 0)} equipos · {int(summary.get('players_count') or 0)} jugadores · {_status((entity.get('readiness') or {}).get('status'))}</span>
      </summary>
      <div class="entity-body">
        <div class="entity-grid">
          <section aria-labelledby="entity-contact-{index}">
            <h3 id="entity-contact-{index}">Responsables</h3>
            <p><strong>Plataforma Sports:</strong> {_text(operations.get('ps_owner'))}</p>
            <div class="table-wrap"><table><thead><tr><th>Responsable entidad</th><th>Teléfono</th><th>Correo</th></tr></thead><tbody>{contacts_html}</tbody></table></div>
          </section>
          <section aria-labelledby="entity-state-{index}">
            <h3 id="entity-state-{index}">Estado operativo</h3>
            <div class="mini-kpis">
              <div><span>Equipos reales</span><strong>{_number(summary.get('teams_count'))}</strong></div>
              <div><span>Jugadores</span><strong>{_number(summary.get('players_count'))}</strong></div>
              <div><span>Equipos esperados</span><strong>—</strong></div>
            </div>
            <ul class="pending">{_rows(operations.get('pending_fields') or [])}</ul>
          </section>
        </div>
        <section aria-labelledby="entity-teams-{index}">
          <h3 id="entity-teams-{index}">Equipos y jugadores</h3>
          <div class="table-wrap"><table><thead><tr><th>Categoría</th><th>Género/rama</th><th>Equipos</th><th>Jugadores</th><th>Nombres</th></tr></thead><tbody>{teams_html}</tbody></table></div>
          <div class="table-wrap secondary-table"><table><thead><tr><th>Categoría</th><th>Género/rama</th><th>Edad</th><th>Jugadores</th></tr></thead><tbody>{players_html}</tbody></table></div>
        </section>
        <section aria-labelledby="entity-finance-{index}">
          <h3 id="entity-finance-{index}">Finanzas por entidad</h3>
          <p class="section-note">La vista sólo presenta hechos respaldados; presupuesto agregado no equivale a transferencia o pago realizado.</p>
          <ul class="pending">{_rows(finance.get('pending_fields') or [])}</ul>
        </section>
      </div>
    </details>
    """


def _operations(dossier: dict[str, Any], index: int) -> str:
    entities = list(dossier.get("entities") or [])
    status = dossier.get("source_status") or "unavailable"
    if status != "available":
        return f"""
        <section id="entidades-{index}" class="panel compact-panel">
          <div class="section-heading"><div><span class="eyebrow">Operaciones por entidad</span><h2>Responsables, equipos, jugadores y avance</h2></div>{_status(status)}</div>
          <p class="empty-copy">La identidad operativa del torneo aún no pudo reconciliarse con la fuente SOUL. No se muestran entidades ni conteos inventados.</p>
        </section>
        """
    entity_html = (
        "".join(
            _entity_detail(entity, item_index)
            for item_index, entity in enumerate(entities)
        )
        or '<p class="empty-copy">La fuente está disponible, pero no contiene entidades para este alcance.</p>'
    )
    bridge = dossier.get("source_bridge")
    bridge_note = (
        " · identidad reconciliada por nombre exacto y edición"
        if bridge == "exact_name_edition_bridge"
        else ""
    )
    return f"""
    <section id="entidades-{index}" class="panel">
      <div class="section-heading"><div><span class="eyebrow">Operaciones por entidad</span><h2>Responsables, equipos, jugadores y avance</h2><p>{len(entities)} entidades{bridge_note}</p></div>{_status('available')}</div>
      <div class="entity-stack">{entity_html}</div>
    </section>
    """


def _national_phase(dossier: dict[str, Any], index: int) -> str:
    source_status = dossier.get("source_status") or "unavailable"
    national = dict(dossier.get("national_phase") or {})
    if source_status != "available" or national.get("status") == "unavailable":
        return f"""
        <section id="fase-nacional-{index}" class="panel compact-panel">
          <div class="section-heading"><div><span class="eyebrow">Fase nacional</span><h2>Operación y finanzas de finales</h2></div>{_status(source_status)}</div>
          <p class="empty-copy">Sin fuente operativa acreditada para esta edición.</p>
        </section>
        """
    if national.get("matches_source_status") == "unavailable":
        return f"""
        <section id="fase-nacional-{index}" class="panel compact-panel">
          <div class="section-heading"><div><span class="eyebrow">Fase nacional</span><h2>Operación y finanzas de finales</h2></div>{_status('unavailable')}</div>
          <p class="empty-copy">La fuente de partidos no está disponible; no se presenta una lista vacía como si acreditara ausencia de partidos.</p>
        </section>
        """
    matches = list(national.get("matches") or [])
    match_rows = (
        "".join(
            f"<tr><td>{_text(row.get('phase'))}</td><td>{_text(row.get('match_date'))}</td><td>{_text(row.get('field_number'), 'Sede/cancha sin registrar')}</td><td>{_text(row.get('status'))}</td></tr>"
            for row in matches
        )
        or '<tr><td colspan="4">Sin partidos de fase nacional identificados.</td></tr>'
    )
    missing = [
        "Ciudad, inauguración, clausura y duración confirmadas.",
        "Hoteles contratados y camas-noche.",
        "Desayunos, comidas, box lunch y cenas contratados.",
        "Unidad deportiva, número y tipo de canchas.",
        "Servicios médicos y accidentes con traslado.",
        "Viajes del personal de Plataforma Sports.",
        "Anticipos y liquidaciones de hoteles por servicio.",
        "Pagos a proveedores, servicios médicos y seguros.",
    ]
    return f"""
    <section id="fase-nacional-{index}" class="panel">
      <div class="section-heading"><div><span class="eyebrow">Fase nacional</span><h2>Operación y finanzas de finales</h2></div>{_status(national.get('status'))}</div>
      <div class="table-wrap"><table><thead><tr><th>Fase</th><th>Fecha</th><th>Sede/cancha</th><th>Estado</th></tr></thead><tbody>{match_rows}</tbody></table></div>
      <details class="pending-details"><summary>Información pendiente de integración o captura</summary><ul class="pending">{_rows(missing)}</ul></details>
    </section>
    """


def _marketing(dossier: dict[str, Any], index: int) -> str:
    marketing = dict(dossier.get("marketing") or {})
    media = dict(marketing.get("media") or {})
    source_unavailable = (
        marketing.get("status") == "unavailable"
        or (dossier.get("source_status") or "unavailable") != "available"
    )
    photos_unavailable = (
        source_unavailable or media.get("photos_source_status") == "unavailable"
    )
    videos_unavailable = (
        source_unavailable or media.get("videos_source_status") == "unavailable"
    )
    streams_unavailable = (
        source_unavailable or media.get("streams_source_status") == "unavailable"
    )
    component_unavailable = (
        photos_unavailable or videos_unavailable or streams_unavailable
    )
    evidence_count = sum(
        int(media.get(key) or 0)
        for key, unavailable in (
            ("photos_count", photos_unavailable),
            ("videos_count", videos_unavailable),
            ("streams_count", streams_unavailable),
        )
        if not unavailable
    )
    if source_unavailable:
        evidence_status = "unavailable"
    elif component_unavailable:
        evidence_status = "partial"
    else:
        evidence_status = "with_data" if evidence_count else "pending_data"
    photos = (
        "Fuente no disponible"
        if photos_unavailable
        else str(int(media.get("photos_count") or 0))
    )
    videos = (
        "Fuente no disponible"
        if videos_unavailable
        else str(int(media.get("videos_count") or 0))
    )
    return f"""
    <section id="mercadotecnia-{index}" class="panel {'compact-panel' if source_unavailable else ''}">
      <div class="section-heading"><div><span class="eyebrow">Mercadotecnia</span><h2>Activaciones y evidencia</h2></div>{_status(evidence_status)}</div>
      <div class="mini-kpis marketing-kpis">
        <div><span>Proveedores presentes</span><strong>—</strong></div>
        <div><span>Visitantes patrocinadores</span><strong>—</strong></div>
        <div><h4>Fotografías</h4><p>{photos}</p></div>
        <div><h4>Videos</h4><p>{videos}</p></div>
      </div>
      {'<p class="section-note">La existencia de fotografías no prueba por sí sola una activación ni su resultado.</p>' if not source_unavailable else ''}
    </section>
    """


def _tournament(card: dict[str, Any], edition_year: int, index: int) -> str:
    dossier = dict(card.get("dossier") or {})
    source_status = dossier.get("source_status") or "unavailable"
    budget_bridge = (
        card.get("budget_scope_bridge")
        if isinstance(card.get("budget_scope_bridge"), dict)
        else {}
    )
    operational_bridge = dossier.get("source_bridge")
    provenance = []
    if budget_bridge:
        provenance.append("presupuesto reconciliado")
    if operational_bridge == "exact_name_edition_bridge":
        provenance.append("operación reconciliada")
    provenance_text = " · ".join(provenance)
    return f"""
    <article class="tournament" id="torneo-{index}">
      <header class="tournament-header">
        <div>
          <span class="eyebrow">Torneo · edición {edition_year}</span>
          <h2>{_text(card.get('tournament_name'), 'Torneo sin nombre')}</h2>
          <p>Corte: {_text(card.get('as_of'))} · <a href="/direccion/tableros/torneos/{quote(str(card.get('tournament_id') or ''))}?edition_year={edition_year}">Abrir sólo este torneo</a>{' · ' + escape(provenance_text) if provenance_text else ''}</p>
        </div>
        {_status(source_status)}
      </header>
      {_budget_kpis(card)}
      {_alerts(card)}
      {_budget_detail(card)}
      {_operations(dossier, index)}
      <div class="two-column">
        {_national_phase(dossier, index)}
        {_marketing(dossier, index)}
      </div>
    </article>
    """


def render_direction_dashboard(payload: dict[str, Any]) -> str:
    """Render the assigned Direction scope as an executive decision surface."""
    edition_year = int(payload.get("edition_year") or 0)
    cards = list(payload.get("cards") or [])
    tournaments = (
        "".join(
            _tournament(card, edition_year, index) for index, card in enumerate(cards)
        )
        or '<section class="panel empty-copy">No hay torneos activos en el alcance asignado.</section>'
    )
    section_links = "".join(
        f'<a href="#torneo-{index}">Torneo {index + 1}</a>'
        f'<a href="#entidades-{index}">Entidades {index + 1}</a>'
        f'<a href="#fase-nacional-{index}">Fase nacional {index + 1}</a>'
        f'<a href="#mercadotecnia-{index}">Mercadotecnia {index + 1}</a>'
        for index, _card in enumerate(cards)
    )
    year_options = "".join(
        f'<option value="{year}" {"selected" if year == edition_year else ""}>{year}</option>'
        for year in range(max(2024, edition_year - 2), edition_year + 3)
    )
    return f"""
    <!doctype html>
    <html lang="es">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Tableros de Dirección · SamChat</title>
      <style>
        :root {{
          --ink:#132238; --muted:#64748b; --paper:#ffffff; --canvas:#f5f7fa;
          --line:#e2e8f0; --soft:#f8fafc; --accent:#0f766e; --accent-soft:#ecfdf5; --link:#0369a1;
          --navy:#0f172a; --blue:#2563eb; --warn:#92400e; --danger:#991b1b;
          --shadow:0 10px 30px rgba(15,23,42,.07);
        }}
        * {{ box-sizing:border-box; }}
        body {{ margin:0; background:var(--canvas); color:var(--ink); font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
        a {{ color:var(--link); text-underline-offset:3px; }}
        .shell {{ width:min(1480px,calc(100% - 40px)); margin:0 auto; padding:32px 0 64px; }}
        .hero {{ background:linear-gradient(135deg,#0f172a,#183153); color:#fff; border-radius:24px; padding:28px 30px; display:flex; justify-content:space-between; gap:28px; align-items:end; box-shadow:var(--shadow); }}
        .hero h1 {{ margin:4px 0 6px; font-size:clamp(26px,3vw,40px); letter-spacing:-.03em; line-height:1.05; color:#fff; }}
        .hero p {{ margin:0; color:#cbd5e1; max-width:800px; }}
        .hero .eyebrow {{ color:#67e8f9; }}
        .filters {{ display:flex; gap:10px; align-items:end; }}
        label {{ display:grid; gap:5px; color:#cbd5e1; font-size:12px; font-weight:700; }}
        select,button {{ min-height:42px; border:1px solid rgba(255,255,255,.35); border-radius:10px; padding:8px 12px; background:#fff; color:#0f172a; }}
        button {{ cursor:pointer; font-weight:800; }}
        a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible {{ outline:3px solid #38bdf8; outline-offset:2px; }}
        nav {{ display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 26px; }}
        nav a {{ text-decoration:none; color:#334155; background:#fff; border:1px solid var(--line); border-radius:999px; padding:7px 12px; font-size:13px; box-shadow:0 2px 8px rgba(15,23,42,.03); }}
        .tournament {{ display:grid; gap:18px; margin:0 0 36px; }}
        .tournament-header {{ display:flex; justify-content:space-between; gap:22px; align-items:start; padding:4px 2px; }}
        .tournament-header h2 {{ margin:3px 0; font-size:clamp(24px,2.6vw,34px); letter-spacing:-.025em; color:var(--navy); }}
        .tournament-header p,.section-note,.source-note,.section-heading p {{ color:var(--muted); margin:.35rem 0 0; }}
        .eyebrow {{ color:var(--accent); text-transform:uppercase; letter-spacing:.12em; font-size:11px; font-weight:900; }}
        .status {{ display:inline-flex; align-items:center; white-space:nowrap; border-radius:999px; padding:6px 10px; font-size:12px; font-weight:800; }}
        .status-available,.status-with_data,.status-usable {{ background:#dcfce7; color:#166534; }}
        .status-partial,.status-pending_data,.status-needs_data {{ background:#fef3c7; color:#854d0e; }}
        .status-unavailable,.status-edition_unavailable {{ background:#fee2e2; color:#991b1b; }}
        .kpi-section,.panel,.attention {{ background:var(--paper); border:1px solid var(--line); border-radius:20px; box-shadow:var(--shadow); }}
        .kpi-section {{ padding:18px; }}
        .kpi-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; }}
        .kpi {{ min-height:118px; padding:16px; border-radius:16px; background:var(--soft); border:1px solid var(--line); display:flex; flex-direction:column; justify-content:space-between; }}
        .kpi-primary {{ background:linear-gradient(145deg,#ecfeff,#f0fdfa); border-color:#99f6e4; }}
        .kpi span {{ color:#475569; font-size:11px; letter-spacing:.08em; text-transform:uppercase; font-weight:900; }}
        .kpi strong {{ display:block; margin:6px 0; color:var(--navy); font-size:clamp(20px,2vw,28px); letter-spacing:-.025em; }}
        .kpi small {{ color:var(--muted); min-height:20px; }}
        .source-note {{ font-size:12px; margin:10px 2px 0; }}
        .attention {{ display:grid; grid-template-columns:240px 1fr; gap:22px; padding:18px 20px; border-left:4px solid #f59e0b; }}
        .attention h3 {{ margin:2px 0; font-size:18px; }}
        .attention ul {{ margin:0; padding:0; list-style:none; display:grid; gap:7px; }}
        .attention li {{ display:flex; align-items:center; gap:9px; color:#334155; }}
        .alert-dot {{ width:8px; height:8px; border-radius:50%; background:#64748b; }}
        .alert-critical,.alert-high {{ background:#dc2626; }} .alert-warning {{ background:#f59e0b; }} .alert-info {{ background:#2563eb; }}
        .panel {{ padding:22px; }}
        .compact-panel {{ min-height:0; padding:18px 22px; }}
        .section-heading {{ display:flex; justify-content:space-between; gap:20px; align-items:start; margin-bottom:16px; }}
        .section-heading h2 {{ margin:3px 0; color:var(--navy); font-size:22px; letter-spacing:-.015em; }}
        .section-heading p {{ font-size:13px; }}
        .empty-copy {{ color:var(--muted); margin:8px 0; }}
        .finance-split {{ display:grid; grid-template-columns:1.25fr .75fr; gap:22px; }}
        .subheading {{ display:flex; justify-content:space-between; gap:10px; align-items:center; margin:2px 0 10px; }}
        .subheading h3 {{ margin:0; font-size:16px; }} .subheading span {{ color:var(--muted); font-size:12px; }}
        .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:14px; background:#fff; }}
        table {{ width:100%; border-collapse:collapse; min-width:620px; }}
        th {{ text-align:left; background:#f1f5f9; color:#475569; font-size:11px; text-transform:uppercase; letter-spacing:.06em; padding:11px 12px; border-bottom:1px solid var(--line); }}
        td {{ padding:11px 12px; border-bottom:1px solid #eef2f7; color:#334155; }}
        tbody tr:last-child td {{ border-bottom:0; }}
        .money {{ text-align:right; font-variant-numeric:tabular-nums; }}
        .entity-stack {{ display:grid; gap:10px; }}
        details.entity {{ border:1px solid var(--line); border-radius:15px; background:#fff; overflow:hidden; }}
        details.entity>summary {{ cursor:pointer; list-style:none; padding:15px 17px; display:flex; justify-content:space-between; gap:16px; align-items:center; background:#f8fafc; }}
        details.entity>summary::-webkit-details-marker {{ display:none; }}
        details.entity>summary div {{ display:grid; gap:2px; }}
        details.entity>summary div span {{ color:var(--muted); font-size:12px; }}
        .summary-meta {{ display:flex; align-items:center; gap:8px; color:#475569; font-size:13px; }}
        .entity-body {{ padding:18px; display:grid; gap:20px; }}
        .entity-body h3 {{ margin:0 0 10px; font-size:16px; }}
        .entity-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
        .mini-kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:9px; }}
        .mini-kpis>div {{ border:1px solid var(--line); border-radius:12px; padding:12px; background:var(--soft); }}
        .mini-kpis span,.mini-kpis h4 {{ display:block; margin:0 0 6px; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }}
        .mini-kpis strong,.mini-kpis p {{ margin:0; color:var(--navy); font-size:18px; font-weight:800; }}
        .secondary-table {{ margin-top:12px; }}
        .pending {{ margin:10px 0 0; padding:12px 12px 12px 30px; border-radius:12px; background:#fff7ed; color:#9a3412; border:1px solid #fed7aa; }}
        .pending-details summary {{ cursor:pointer; color:#475569; font-weight:700; margin-top:14px; }}
        .two-column {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
        .marketing-kpis {{ grid-template-columns:repeat(4,1fr); }}
        @media (max-width:1180px) {{ .kpi-grid {{ grid-template-columns:repeat(3,1fr); }} .finance-split,.two-column {{ grid-template-columns:1fr; }} }}
        @media (max-width:760px) {{
          .shell {{ width:min(100% - 18px,1480px); padding-top:12px; }}
          .hero,.tournament-header,.section-heading,details.entity>summary {{ display:block; }}
          .hero {{ padding:22px; }} .filters {{ margin-top:18px; }} .kpi-grid {{ grid-template-columns:1fr 1fr; }}
          .attention,.entity-grid {{ grid-template-columns:1fr; }} .summary-meta {{ margin-top:8px; }} .marketing-kpis {{ grid-template-columns:1fr 1fr; }}
        }}
        @media (prefers-color-scheme:dark) {{
          :root {{ --ink:#f1f5f9; --muted:#b8c7d9; --paper:#101c2a; --canvas:#07111c; --line:#41566d; --soft:#162638; --navy:#ffffff; --link:#7dd3fc; --shadow:0 12px 30px rgba(0,0,0,.22); }}
          body {{ background:var(--canvas); color:var(--ink); }}
          .hero {{ background:linear-gradient(135deg,#0a1522,#0f2940); }}
          nav a,.panel,.kpi-section,.attention,details.entity,.table-wrap {{ background:var(--paper); }}
          nav a,td,.attention li,.summary-meta {{ color:var(--ink); }}
          .tournament-header h2,.section-heading h2,.kpi strong,.mini-kpis strong,.mini-kpis p,.entity-body h3,.subheading h3,details.entity>summary strong {{ color:var(--navy); }}
          .tournament-header p,.section-note,.source-note,.section-heading p,.empty-copy,.kpi small,.kpi span,.subheading span,details.entity>summary div span,.mini-kpis span,.mini-kpis h4,.pending-details summary {{ color:var(--muted); }}
          .eyebrow {{ color:#67e8f9; }}
          .kpi,.mini-kpis>div,details.entity>summary {{ background:var(--soft); }}
          .kpi-primary {{ background:#11333a; border-color:#226f72; }}
          th {{ background:#1a3044; color:#cbd5e1; }} td {{ border-color:#24384c; }}
          .table-wrap {{ border-color:var(--line); }}
          select,button {{ background:#fff; color:#0f172a; }}
          .pending {{ background:#3a2810; color:#fed7aa; border-color:#7c4a12; }}
          .status-available,.status-with_data,.status-usable {{ background:#123a2a; color:#86efac; }}
          .status-partial,.status-pending_data,.status-needs_data {{ background:#422f10; color:#fde68a; }}
          .status-unavailable,.status-edition_unavailable {{ background:#431919; color:#fecaca; }}
        }}
      </style>
    </head>
    <body>
      <main class="shell">
        <header class="hero">
          <div><span class="eyebrow">Plataforma Sports</span><h1>Tablero ejecutivo de Dirección</h1><p>Una vista de decisión: presupuesto, operación y evidencia dentro de la cartera y torneos asignados. Sólo lectura.</p></div>
          <form class="filters" method="get" action="/direccion/tableros"><label>Edición<select name="edition_year">{year_options}</select></label><button type="submit">Actualizar</button></form>
        </header>
        <nav aria-label="Secciones">{section_links}<a href="/direccion/reportes">Reportes publicados</a></nav>
        {tournaments}
      </main>
    </body>
    </html>
    """
