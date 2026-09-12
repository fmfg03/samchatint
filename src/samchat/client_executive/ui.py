"""Accessible HTML renderer for the internal Direction executive dossier."""

from __future__ import annotations

from html import escape
from typing import Any, Iterable
from urllib.parse import quote


def _text(value: object, fallback: str = "Sin información registrada") -> str:
    rendered = str(value or "").strip()
    return escape(rendered if rendered else fallback)


def _money(value: object) -> str:
    try:
        return "${:,.2f}".format(float(value or 0))
    except (TypeError, ValueError):
        return "Sin información registrada"


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
    }.get(key, key.replace("_", " ").title())
    return f'<span class="status status-{escape(key)}">{escape(label)}</span>'


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
          <td>Sin información registrada</td>
          <td>Sin información registrada</td>
          <td>Sin información registrada</td>
        </tr>
        """ for contact in contacts)
        or '<tr><td colspan="6">Sin responsable de la entidad registrado.</td></tr>'
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

    finance_cards = [
        ("Ayudas y transferencias", finance.get("first_and_successive_aid_transfers")),
        ("Uniformes, balones y utilería", finance.get("equipment_costs")),
        ("Informes de visitas", finance.get("visit_reports")),
        ("Gastos de visitas", finance.get("visit_expenses")),
    ]
    finance_html = "".join(f"""
        <article class="fact">
          <h4>{escape(label)}</h4>
          <p>{len(list(values or [])) if values else 'Sin información registrada'}</p>
        </article>
        """ for label, values in finance_cards)

    return f"""
    <details class="entity" {'open' if index == 0 else ''}>
      <summary>
        <span>{_text(entity.get('entity_name'), 'Entidad sin nombre')}</span>
        <span class="summary-meta">{int(summary.get('teams_count') or 0)} equipos · {int(summary.get('players_count') or 0)} jugadores · {_status((entity.get('readiness') or {}).get('status'))}</span>
      </summary>
      <div class="entity-body">
        <section aria-labelledby="entity-contact-{index}">
          <h3 id="entity-contact-{index}">Responsables</h3>
          <div class="facts">
            <article class="fact"><h4>Entidad</h4><p>{_text(entity.get('entity_name'))}</p></article>
            <article class="fact"><h4>Responsable Plataforma Sports</h4><p>{_text(operations.get('ps_owner'))}</p></article>
          </div>
          <div class="table-wrap"><table>
            <thead><tr><th>Responsable entidad</th><th>Teléfono</th><th>Correo</th><th>Nacimiento</th><th>Pareja</th><th>Nacimiento pareja</th></tr></thead>
            <tbody>{contacts_html}</tbody>
          </table></div>
        </section>

        <section aria-labelledby="entity-teams-{index}">
          <h3 id="entity-teams-{index}">Equipos y jugadores</h3>
          <div class="facts">
            <article class="fact"><h4>Equipos esperados</h4><p>{_text(None)}</p><small>Requiere fuente de planeación por categoría y género.</small></article>
            <article class="fact"><h4>Equipos reales</h4><p>{int(summary.get('teams_count') or 0)}</p><small>Actualización conforme al registro operativo.</small></article>
            <article class="fact"><h4>Jugadores</h4><p>{int(summary.get('players_count') or 0)}</p><small>Desglose disponible debajo.</small></article>
          </div>
          <h4>Equipos reales por categoría y género</h4>
          <div class="table-wrap"><table><thead><tr><th>Categoría</th><th>Género/rama</th><th>Equipos</th><th>Jugadores</th><th>Nombres</th></tr></thead><tbody>{teams_html}</tbody></table></div>
          <h4>Jugadores por categoría, edad y género</h4>
          <div class="table-wrap"><table><thead><tr><th>Categoría</th><th>Género/rama</th><th>Edad</th><th>Jugadores</th></tr></thead><tbody>{players_html}</tbody></table></div>
        </section>

        <section aria-labelledby="entity-state-{index}">
          <h3 id="entity-state-{index}">Fase estatal y clasificación</h3>
          <div class="facts">
            <article class="fact"><h4>Equipos por ronda</h4><p>{_text(None)}</p></article>
            <article class="fact"><h4>Organización, arbitraje y transportes</h4><p>{_text(operations.get('state_phase_description'))}</p></article>
            <article class="fact"><h4>Clasificados al nacional</h4><p>{_text(None)}</p></article>
            <article class="fact"><h4>Entrega de uniformes</h4><p>{_text(operations.get('state_uniform_delivery'))}</p></article>
            <article class="fact"><h4>Viaje al nacional</h4><p>{_text(operations.get('national_travel_dates'))}</p></article>
            <article class="fact"><h4>Clasificación final</h4><p>{_text(None)}</p></article>
          </div>
          <h4>Información operativa pendiente</h4>
          <ul class="pending">{_rows(operations.get('pending_fields') or [])}</ul>
        </section>

        <section aria-labelledby="entity-finance-{index}">
          <h3 id="entity-finance-{index}">Finanzas por entidad</h3>
          <p class="section-note">La vista no interpreta presupuesto agregado como transferencia o pago realizado.</p>
          <div class="facts">{finance_html}</div>
          <h4>Información financiera pendiente</h4>
          <ul class="pending">{_rows(finance.get('pending_fields') or [])}</ul>
        </section>
      </div>
    </details>
    """


def _national_phase(dossier: dict[str, Any]) -> str:
    national = dict(dossier.get("national_phase") or {})
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
    <section id="fase-nacional" class="panel">
      <div class="section-heading"><div><span class="eyebrow">Fase nacional</span><h2>Operación y finanzas de finales</h2></div>{_status(national.get('status'))}</div>
      <div class="table-wrap"><table><thead><tr><th>Fase</th><th>Fecha</th><th>Sede/cancha</th><th>Estado</th></tr></thead><tbody>{match_rows}</tbody></table></div>
      <h3>Información pendiente de integración o captura</h3>
      <ul class="pending">{_rows(missing)}</ul>
    </section>
    """


def _marketing(dossier: dict[str, Any]) -> str:
    marketing = dict(dossier.get("marketing") or {})
    media = dict(marketing.get("media") or {})
    evidence_count = sum(
        int(media.get(key) or 0)
        for key in ("photos_count", "videos_count", "streams_count")
    )
    return f"""
    <section id="mercadotecnia" class="panel">
      <div class="section-heading"><div><span class="eyebrow">Mercadotecnia</span><h2>Activaciones y evidencia</h2></div>{_status('with_data' if evidence_count else 'pending_data')}</div>
      <div class="facts">
        <article class="fact"><h4>Proveedores presentes</h4><p>Sin información registrada</p></article>
        <article class="fact"><h4>Visitantes de patrocinadores</h4><p>Sin información registrada</p></article>
        <article class="fact"><h4>Fotografías</h4><p>{int(media.get('photos_count') or 0)}</p></article>
        <article class="fact"><h4>Videos</h4><p>{int(media.get('videos_count') or 0)}</p></article>
      </div>
      <p class="section-note">La existencia de fotografías no prueba por sí sola una activación ni su resultado. Falta relacionar proveedor, patrocinador, actividad y evidencia.</p>
    </section>
    """


def _tournament(card: dict[str, Any], edition_year: int, index: int) -> str:
    dossier = dict(card.get("dossier") or {})
    entities = list(dossier.get("entities") or [])
    source_status = dossier.get("source_status") or "unavailable"
    entity_html = (
        "".join(
            _entity_detail(entity, item_index)
            for item_index, entity in enumerate(entities)
        )
        or '<div class="empty">No fue posible obtener entidades del alcance operativo de este torneo.</div>'
    )
    return f"""
    <article class="tournament" id="torneo-{index}">
      <header class="tournament-header">
        <div><span class="eyebrow">Torneo · edición {edition_year}</span><h2>{_text(card.get('tournament_name'), 'Torneo sin nombre')}</h2><p>Corte: {_text(card.get('as_of'))} · <a href="/direccion/tableros/torneos/{quote(str(card.get('tournament_id') or ''))}?edition_year={edition_year}">Abrir sólo este torneo</a></p></div>
        {_status(source_status)}
      </header>
      <div class="facts budget">
        <article class="fact"><h4>Presupuesto</h4><p>{_money(card.get('budget'))}</p></article>
        <article class="fact"><h4>Ejercido</h4><p>{_money(card.get('actual'))}</p></article>
        <article class="fact"><h4>Comprometido</h4><p>{_money(card.get('committed'))}</p></article>
        <article class="fact"><h4>Proyección</h4><p>{_money(card.get('projected'))}</p></article>
      </div>
      <section id="entidades-{index}" class="panel nested">
        <div class="section-heading"><div><span class="eyebrow">Operaciones por entidad</span><h2>Responsables, equipos, jugadores y avance</h2></div><span>{len(entities)} entidades</span></div>
        {entity_html}
      </section>
      {_national_phase(dossier)}
      {_marketing(dossier)}
    </article>
    """


def render_direction_dashboard(payload: dict[str, Any]) -> str:
    """Render the assigned Direction scope as a usable executive dossier."""
    edition_year = int(payload.get("edition_year") or 0)
    cards = list(payload.get("cards") or [])
    tournaments = (
        "".join(
            _tournament(card, edition_year, index) for index, card in enumerate(cards)
        )
        or '<section class="panel empty">No hay torneos activos en el alcance asignado.</section>'
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
        :root {{ --ink:#102a43; --muted:#486581; --line:#bcccdc; --paper:#fff; --soft:#f0f4f8; --accent:#0b5f73; --accent-dark:#073b4c; --warn:#8a4b08; --danger:#9b1c1c; }}
        * {{ box-sizing:border-box; }}
        body {{ margin:0; background:#eaf0f5; color:var(--ink); font:15px/1.5 Inter,system-ui,sans-serif; }}
        a {{ color:#075985; }}
        .shell {{ width:min(1480px,calc(100% - 32px)); margin:0 auto; padding:28px 0 56px; }}
        .hero,.panel,.tournament {{ background:var(--paper); border:1px solid var(--line); border-radius:18px; box-shadow:0 8px 24px rgba(16,42,67,.08); }}
        .hero {{ padding:26px; display:flex; justify-content:space-between; gap:24px; align-items:end; }}
        .hero h1,.tournament h2,.panel h2 {{ margin:.25rem 0; line-height:1.15; }}
        .hero p,.tournament-header p,.section-note {{ color:var(--muted); margin:.35rem 0 0; }}
        .eyebrow {{ color:var(--accent); text-transform:uppercase; letter-spacing:.11em; font-size:12px; font-weight:800; }}
        .filters {{ display:flex; gap:10px; align-items:end; }}
        label {{ display:grid; gap:5px; color:var(--muted); font-size:12px; font-weight:700; }}
        select,button {{ min-height:42px; border:1px solid #829ab1; border-radius:10px; padding:8px 12px; background:white; color:var(--ink); }}
        button {{ background:var(--accent-dark); color:white; font-weight:800; cursor:pointer; }}
        nav {{ margin:16px 0; display:flex; flex-wrap:wrap; gap:8px; }}
        nav a {{ background:var(--accent-dark); color:#fff; padding:9px 12px; border-radius:999px; text-decoration:none; font-weight:700; }}
        .tournament {{ padding:22px; margin-top:18px; }}
        .tournament-header,.section-heading {{ display:flex; justify-content:space-between; gap:16px; align-items:start; }}
        .facts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(205px,1fr)); gap:12px; margin:16px 0; }}
        .fact {{ background:var(--soft); border:1px solid var(--line); border-radius:14px; padding:14px; }}
        .fact h4 {{ margin:0; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
        .fact p {{ margin:6px 0 0; font-size:18px; font-weight:800; overflow-wrap:anywhere; }}
        .fact small {{ display:block; color:var(--muted); margin-top:5px; }}
        .panel {{ padding:20px; margin-top:18px; }}
        .panel.nested {{ box-shadow:none; }}
        details.entity {{ border:1px solid var(--line); border-radius:14px; margin-top:12px; overflow:hidden; }}
        details.entity > summary {{ cursor:pointer; padding:15px; background:#dde8f0; display:flex; justify-content:space-between; gap:12px; font-weight:850; }}
        .summary-meta {{ font-weight:600; color:var(--muted); text-align:right; }}
        .entity-body {{ padding:4px 18px 20px; }}
        .entity-body section {{ padding-top:16px; }}
        .table-wrap {{ width:100%; overflow:auto; border:1px solid var(--line); border-radius:12px; }}
        table {{ width:100%; border-collapse:collapse; min-width:680px; }}
        th {{ background:var(--accent-dark); color:#fff; text-align:left; font-size:12px; letter-spacing:.04em; }}
        th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
        tbody tr:nth-child(even) td {{ background:#f7fafc; }}
        .pending {{ background:#fff8e6; border-left:5px solid #c56a08; padding:13px 18px 13px 34px; color:#603808; }}
        .status {{ display:inline-flex; align-items:center; padding:5px 9px; border-radius:999px; background:#d9e2ec; color:#243b53; font-size:12px; font-weight:800; white-space:nowrap; }}
        .status-available,.status-with_data,.status-usable {{ background:#c6f6d5; color:#185c37; }}
        .status-partial,.status-pending_data,.status-needs_data {{ background:#ffecb5; color:#6b3d00; }}
        .status-unavailable {{ background:#fed7d7; color:#742a2a; }}
        .empty {{ padding:22px; color:var(--muted); }}
        @media (max-width:760px) {{ .shell {{ width:min(100% - 18px,1480px); }} .hero,.tournament-header,.section-heading,details.entity>summary {{ display:block; }} .filters {{ margin-top:16px; }} .summary-meta {{ display:block; margin-top:6px; text-align:left; }} }}
        @media (prefers-color-scheme:dark) {{ :root {{ --ink:#e6edf3; --muted:#b8c7d9; --line:#4d6478; --paper:#142738; --soft:#20394d; --accent:#6dd5ed; --accent-dark:#07566b; }} body {{ background:#0b1722; }} tbody tr:nth-child(even) td {{ background:#1a3144; }} select {{ background:#142738; color:#fff; }} .pending {{ background:#422e12; color:#ffe3a3; border-color:#ffb547; }} details.entity>summary {{ background:#20394d; }} }}
      </style>
    </head>
    <body>
      <main class="shell">
        <header class="hero">
          <div><span class="eyebrow">Plataforma Sports</span><h1>Tablero ejecutivo de Dirección</h1><p>Expediente de Operaciones, Finanzas y Mercadotecnia dentro de la cartera y torneos asignados. Vista de sólo lectura.</p></div>
          <form class="filters" method="get" action="/direccion/tableros"><label>Edición<select name="edition_year">{year_options}</select></label><button type="submit">Actualizar</button></form>
        </header>
        <nav aria-label="Secciones"><a href="#torneo-0">Resumen</a><a href="#entidades-0">Entidades</a><a href="#fase-nacional">Fase nacional</a><a href="#mercadotecnia">Mercadotecnia</a><a href="/direccion/reportes">Reportes publicados</a></nav>
        {tournaments}
      </main>
    </body>
    </html>
    """
