"""Responsive Direction home with a single, signed conversational context."""

from __future__ import annotations

import json
from html import escape
from urllib.parse import urlencode

from .home import amount, format_money


def _json(value: object) -> str:
    # Script data must not be able to close its containing element.
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("&", "\\u0026")
    )


def render_home(snapshot: dict, scope: dict, *, token: str, csrf: str) -> str:
    """Render only the already-authorized, reduced executive read model."""
    metrics = snapshot["indicators"]
    period = snapshot["period"]

    def options(items: list, selected: str | None, key: str) -> str:
        return "".join(
            f'<option value="{escape(row["id"], quote=True)}" {"selected" if row["id"] == selected else ""}>{escape(row[key])}</option>'
            for row in items
        )

    portfolio_options = options(scope["portfolios"], scope["portfolio_id"], "label")
    tournament_options = options(scope["tournaments"], scope["tournament_id"], "name")
    cards = ""
    for metric in metrics:
        coverage = metric["coverage"]
        qualifier = (
            "Subtotal cubierto"
            if metric["status"] == "partial"
            else (
                "Sin dato"
                if metric["value"] is None
                else "Fuente disponible · validación pendiente"
            )
        )
        cards += f"""<button type="button" class="home-metric" data-metric="{metric['id']}" aria-pressed="false">
            <span>{escape(metric['label'])}</span><strong>{escape(format_money(metric['value']))}</strong>
            <span class="coverage">{qualifier} · {coverage['covered']}/{coverage['total']} torneos</span>
            <small>{escape(metric['period'])}</small><small>Corte: {escape(metric['as_of'])}</small>
            <small>{escape(metric['definition'])}</small><span class="ask-hint">Consultar esta cifra con Sam →</span></button>"""
    comparisons = ""
    for row in snapshot["tournaments"]:
        budget, actual = row["values"].get("budget"), row["values"].get("actual")
        b, a = amount(budget), amount(actual)
        ratio = (
            min(100, max(0, float(a / b * 100)))
            if a is not None and b and b > 0
            else None
        )
        bar = (
            f'<div class="home-track"><span style="width:{ratio:.3f}%"></span></div>'
            if ratio is not None
            else '<p class="muted">Sin base comparable para graficar.</p>'
        )
        comparisons += f"""<div class="comparison"><h3>{escape(row['name'])}</h3>
            <p>Ejercido: {escape(format_money(actual))} · presupuesto anual: {escape(format_money(budget))}</p>{bar}
            <p class="muted">Comprometido: {escape(format_money(row['values'].get('committed')))} · pagado documental: {escape(format_money(row['values'].get('paid')))}</p>
            <details><summary>Fuente y cobertura</summary><p>Versión: {escape(str(row.get('version_id') or 'Sin versión acreditada'))}</p><p>{escape('; '.join(row['gaps'].get('actual', [])) or 'Sin brechas automáticas detectadas; conciliación de negocio pendiente.')}</p></details></div>"""
    evolution = ""
    for row in snapshot["tournaments"]:
        series = row.get("monthly_execution") or {}
        points = series.get("rows") or []
        maximum = max((amount(p["value"]) or 0 for p in points), default=0)
        bars = ""
        for point in points:
            height = (
                float((amount(point["value"]) or 0) / maximum * 100)
                if maximum > 0
                else 0
            )
            bars += f'<div class="month-point"><span>{escape(format_money(point["value"]))}</span><div class="month-track"><i style="height:{height:.3f}%"></i></div><span>{snapshot["edition_year"]}-{point["month"]:02d}</span></div>'
        content = (
            f'<div class="table-wrap"><div class="month-series">{bars}</div></div>'
            if series.get("status") == "available"
            else f'<p class="muted">{escape(series.get("gap") or "Serie mensual sin fuente acreditada.")}</p>'
        )
        evolution += (
            f'<div class="comparison"><h3>{escape(row["name"])}</h3>{content}</div>'
        )
    priorities = (
        "".join(
            f"""<li><div><strong>{escape(row['title'])}</strong><p>{escape(row['detail'])}</p>
        <button type="button" data-priority="{row['metric_id']}">Abrir con Sam</button></div></li>"""
            for row in snapshot["priorities"]
        )
        or "<li>No hay asuntos verificables para ordenar en este alcance.</li>"
    )
    operations = "".join(
        f"""<tr><th scope="row">{escape(row['name'])}</th><td>{escape(str(row['operations']['teams'])) if row['operations']['teams'] is not None else 'Sin dato'}</td>
        <td>{escape(str(row['operations']['players'])) if row['operations']['players'] is not None else 'Sin dato'}</td><td>Sin meta aprobada</td></tr>"""
        for row in snapshot["tournaments"]
    )
    dossier_query = urlencode({"edition_year": snapshot["edition_year"]})
    data = _json({"snapshot": snapshot, "token": token, "csrf": csrf})
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="referrer" content="same-origin"><title>Dirección · SamChat</title>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f1f5f8;color:#173044;font:15px/1.5 system-ui,sans-serif}}button,input,select{{font:inherit}}button,summary{{cursor:pointer}}button,input,select{{border:1px solid #b9cbd6;border-radius:6px;padding:10px;background:#fff;color:#173044}}button:hover{{background:#e3f0f2}}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{{outline:3px solid #078590;outline-offset:3px}}a{{color:#086f81}}h1,h2,h3,p{{margin-top:0}}h1{{font-size:30px;line-height:1.25}}h2{{font-size:21px}}h3{{font-size:16px}}header{{background:#143548;color:white;padding:20px 24px;display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap}}header a{{color:#b0eee6}}header b{{font-size:22px}}main{{max-width:1400px;margin:auto;padding:24px}}.home-filters{{display:flex;flex-wrap:wrap;gap:12px;align-items:end}}label{{display:grid;gap:4px}}label select,label input{{max-width:230px;min-width:130px}}.hero{{margin-top:25px}}.muted,.coverage,small{{color:#506a7c}}.notice{{padding:12px 16px;background:#fff1d7;color:#6c490c;border-radius:6px}}.home-metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0}}.home-metric{{text-align:left;display:grid;gap:8px;padding:18px;align-content:start;min-width:0;overflow-wrap:anywhere}}.home-metric strong{{font-size:23px;font-variant-numeric:tabular-nums}}.home-metric small{{font-size:12px}}.home-metric[aria-pressed=true]{{outline:2px solid #078590}}.ask-hint{{font-size:12px;color:#076677}}.layout{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:20px}}.panel{{background:white;border:1px solid #d1dfe8;border-radius:9px;padding:22px;min-width:0}}.panel+.panel{{margin-top:20px}}.comparison{{padding:15px 0;border-top:1px solid #e1e9ef}}.comparison p{{margin-bottom:8px}}.home-track{{height:10px;background:#e5edf2;border-radius:4px;overflow:hidden;margin:12px 0}}.month-series{{display:flex;gap:12px;align-items:end}}.month-point{{min-width:100px;text-align:center;font-size:11px}}.month-track{{height:100px;width:25px;margin:8px auto;background:#e5edf2;display:flex;align-items:end}}.month-track i{{display:block;width:100%;background:#078590}}.home-track span{{display:block;height:100%;background:#078590}}.priorities{{list-style:decimal;padding-left:22px}}.priorities li{{padding:12px 0;border-top:1px solid #e1e9ef}}.priorities p{{margin:6px 0}}.context{{padding:12px;background:#e9f3f5;font-size:13px;border-radius:6px;overflow-wrap:anywhere}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}}.chat-form{{display:flex;gap:8px;flex-wrap:wrap}}.chat-form input{{flex:1;min-width:120px}}.primary{{background:#075c71;color:white;border-color:#075c71}}.primary:hover{{background:#054b5c}}.answer{{white-space:pre-wrap;overflow-wrap:anywhere;border-left:3px solid #078590;padding-left:15px;margin:20px 0}}.evidence{{padding:15px;background:#f0f5f8;overflow-wrap:anywhere}}.evidence dt{{font-weight:600;margin-top:8px}}.evidence dd{{margin:0}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #d5e2ea}}footer{{margin-top:24px;font-size:12px;color:#506a7c}}[hidden]{{display:none!important}}.empty{{padding:18px}}.error{{color:#a52d19}}.history article{{border-top:1px solid #d5e2ea;margin-top:15px;padding-top:12px;white-space:pre-wrap;overflow-wrap:anywhere}}
    @media(max-width:1000px){{.home-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.layout{{grid-template-columns:1fr}}}}
    @media(max-width:500px){{main{{padding:14px}}.home-metrics{{grid-template-columns:1fr}}.home-filters label{{width:100%}}label select,label input{{width:100%;max-width:none}}.panel{{padding:16px}}h1{{font-size:25px}}header{{padding:16px}}}}
    </style></head><body><header><b>SamChat · Dirección</b><nav><a href="/panel">Mi panel</a> · <a href="/direccion/tableros?{dossier_query}">Expedientes por torneo</a> · <a href="/direccion/reportes">Reportes publicados</a></nav></header>
    <main><form class="home-filters" action="/direccion/inicio" method="get"><label>Edición<input type="number" name="edition_year" value="{snapshot['edition_year']}" min="2000" max="2100"></label>
    <label>Cartera<select name="portfolio_id"><option value="">Todas las asignadas</option>{portfolio_options}</select></label>
    <label>Torneo<select name="tournament_id"><option value="">Todos los autorizados</option>{tournament_options}</select></label>
    <label>Desde<input type="date" name="date_from" value="{period['start']}"></label><label>Hasta<input type="date" name="date_to" value="{period['end']}"></label><button class="primary">Actualizar contexto</button></form>
    <section class="hero"><h1>Cómo vamos y qué requiere atención</h1><p>{escape(snapshot['headline'])}</p><p class="muted">Periodo de registros: {period['start']} a {period['end']} · {len(snapshot['tournaments'])} torneos autorizados · MXN</p>
    <p class="notice">Validación de negocio pendiente. Los indicadores ausentes conservan su brecha. Los saldos actuales y el presupuesto anual muestran su propio periodo.</p></section>
    <section aria-label="Indicadores ejecutivos" class="home-metrics">{cards}</section>
    <div class="layout"><div><section class="panel"><h2>Ejecución contra presupuesto</h2><p class="muted">La barra compara ejercido del intervalo con presupuesto anual. No apila ejercido, comprometido y pagado.</p>{comparisons or '<p>No hay torneos en este alcance.</p>'}</section>
    <section class="panel"><h2>Evolución mensual del ejercido</h2><p class="muted">Misma base y alcance del agregado; meses de inicio y fin pueden ser parciales. No representa salida de caja.</p>{evolution}</section>
    <section class="panel"><h2>Asuntos prioritarios</h2><ol class="priorities">{priorities}</ol></section>
    <section class="panel"><h2>Operación respaldada</h2><p class="muted">Conteos de la edición {snapshot['edition_year']}; no se filtran por el intervalo financiero. Conteos no equivalen a porcentaje de avance.</p><div class="table-wrap"><table><thead><tr><th>Torneo</th><th>Equipos</th><th>Jugadores</th><th>Avance</th></tr></thead><tbody>{operations}</tbody></table></div></section></div>
    <aside class="panel" style="margin-top:0"><h2>Sam, en este contexto</h2><p class="muted">Consulta cifras, definición, fuente y comparación de este tablero.</p><div id="home-context" class="context"></div><div id="home-answer" class="answer" aria-live="polite">Selecciona una cifra para comenzar.</div>
    <div class="actions"><button type="button" id="home-explain">¿Qué explica esto?</button><button type="button" id="home-compare">Comparar torneos</button><button type="button" id="home-source">Fuente y definición</button></div>
    <form id="home-chat" class="chat-form"><input id="home-question" maxlength="2000" aria-label="Pregunta sobre la cifra seleccionada" placeholder="Pregunta sobre esta cifra" required><button class="primary" id="home-send">Preguntar</button></form>
    <p id="home-error" class="error" role="alert" hidden></p><dl id="home-evidence" class="evidence" hidden></dl><div id="home-history" class="history"></div><p class="muted">Investigación causal, recomendaciones y escenarios: siguiente entrega. Esta consulta no aprueba ni paga.</p></aside></div>
    <footer>Los cortes individuales de fuentes pueden diferir; no se afirma atomicidad entre Postgres y Supabase. Contexto válido durante 15 minutos. Actualizar filtros inicia un contexto nuevo.</footer></main>
    <script id="home-data" type="application/json">{data}</script><script src="/static/direction_home.js" defer></script></body></html>"""
