"""One business report model for HTML, PDF and workbook; exact signed cut."""

from __future__ import annotations

import hashlib
import json
from html import escape

from .analysis import deviations, recommendations
from .home import format_money

SOURCE_NAMES = {
    "budget": "Presupuesto aprobado · versión de cada torneo",
    "actual": "Gastos activos · base presupuestal del intervalo",
    "committed": "Solicitudes · estados de compromiso documental",
    "paid": "Solicitudes · pago documental; no saldo bancario",
    "forecast": "Proyección mecánica del servicio de presupuestos",
    "deviation": "Proyección mecánica menos presupuesto aprobado",
    "obligations": "Solicitudes elegibles · importe pagable y fecha programada",
    "receivables": "CFDI atribuibles menos cobros aceptados",
    "overdue": "Vencimientos contractuales pendientes de validación",
    "liquidity": "Saldo bancario conciliado pendiente de acreditación",
}
GAP_NAMES = {
    "mixed_or_unknown_currency": "Moneda distinta de MXN o sin acreditar; falta conversión validada.",
    "document_amount_missing": "Hay solicitudes sin importe acreditado.",
    "expense_amount_missing": "Hay gastos sin importe acreditado.",
    "shared_cfdi_allocation_requires_reconciliation": "Hay CFDI compartidos cuyo reparto requiere conciliación.",
}


def business_gap(gap: str) -> str:
    return GAP_NAMES.get(gap, gap)


def build_report(snapshot: dict, analysis: dict | None = None) -> dict:
    """Do not requery, recompute or invent financial values at export time."""
    advice = recommendations(snapshot)
    items, detail_gaps = deviations(snapshot)
    indicators = [
        {
            **m,
            "source": SOURCE_NAMES[m["id"]],
            "gaps": [business_gap(g) for g in m["gaps"]],
        }
        for m in snapshot["indicators"]
    ]
    gaps = sorted(
        {g for m in indicators for g in m["gaps"]}
        | {business_gap(g) for g in detail_gaps}
        | {business_gap(g) for g in (analysis or {}).get("missing_evidence", [])}
    )
    report = {
        "title": "Informe para consejo",
        "snapshot_id": snapshot["snapshot_id"],
        "cut": snapshot["as_of"],
        "period": snapshot["period"],
        "scope": ", ".join(r["name"] for r in snapshot["tournaments"])
        or "Sin torneos autorizados",
        "conclusion": (analysis or {}).get("conclusion") or snapshot["headline"],
        "indicators": indicators,
        "tournaments": snapshot["tournaments"],
        "risks": items,
        "recommendations": advice,
        "scenario": (analysis or {}).get("scenario"),
        "gaps": gaps,
        "validation": "Validación de negocio pendiente. Subtotales parciales no representan toda la cartera.",
        "method": "Lecturas secuenciales con cortes individuales. Proyección mecánica, sin causalidad acreditada. Pagado documental no equivale a salida de caja.",
        "read_only": True,
    }
    report["report_id"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return report


def render_report(report: dict) -> str:
    """Readable in isolation; no technical JSON or private package identifiers."""

    def e(value):
        return escape(str(value))

    metrics = "".join(
        f"<tr><th>{e(m['label'])}</th><td>{e(format_money(m['value']))}</td>"
        f"<td>{m['coverage']['covered']}/{m['coverage']['total']} torneos</td>"
        f"<td>{e('; '.join(m['gaps']) or m['definition'])}</td></tr>"
        for m in report["indicators"]
    )
    advice = "".join(
        f"<article><h3>{e(r['title'])}</h3><p>{e(r['evidence'])}</p>"
        f"<p><b>Recomendación:</b> {e(r['recommendation'])}</p>"
        f"<p><b>Alternativa y riesgo:</b> {e(r['alternative'])} {e(r['risk'])}</p>"
        f"<p><b>Siguiente paso:</b> {e(r['next_step'])}</p></article>"
        for r in report["recommendations"]
    )
    gaps = "".join(f"<li>{e(g)}</li>" for g in report["gaps"])
    scenario = report.get("scenario")
    hypothetical = (
        f"<h2>Escenario hipotético separado del real</h2><p>{e(scenario['title'])}: "
        f"{e(format_money(scenario['result']))}. Base: {e(format_money(scenario['base']))}. "
        f"Impacto: {e(format_money(scenario['effect']))}.</p><p>{e('; '.join(scenario['limits']))}</p>"
        if scenario
        else ""
    )
    return f"""<section class="business-report"><h1>{e(report['title'])}</h1>
    <p>{e(report['scope'])} · {e(report['period']['start'])} a {e(report['period']['end'])}</p>
    <p>Corte: {e(report['cut'])} · Identificador de cifras: {e(report['snapshot_id'])}</p>
    <h2>Conclusión</h2><p>{e(report['conclusion'])}</p><p>{e(report['validation'])}</p>
    <div style="overflow:auto"><table><thead><tr><th>Indicador</th><th>MXN</th><th>Cobertura</th><th>Interpretación y brechas</th></tr></thead><tbody>{metrics}</tbody></table></div>
    <h2>Riesgos y recomendaciones</h2>{advice}{hypothetical}<h2>Datos faltantes</h2><ul>{gaps}</ul>
    <p>{e(report['method'])}</p></section>"""


def render_published_report(label: str, summary: dict, snapshot: dict, cut: str) -> str:
    """Preserve legacy publication authority while replacing the JSON presentation."""
    if snapshot.get("schema") == "samchat.direction.home.v1":
        return render_report(build_report(snapshot))
    cards = snapshot.get("cards") or snapshot.get("tournaments") or []
    rows = []
    for card in cards:
        values = card.get("summary") or card.get("metrics") or card
        # Legacy publications remain bounded to their own persisted facts.
        if isinstance(values, dict):
            rows.append(
                f"<h3>{escape(str(card.get('tournament_name') or card.get('name') or 'Torneo'))}</h3>"
            )
            for key, title in (
                ("budget_total", "Presupuesto"),
                ("actual_total", "Ejercido"),
                ("committed_total", "Comprometido"),
                ("paid_total", "Pagado documental"),
                ("projected", "Cierre estimado"),
                ("available", "Presupuesto remanente; no caja"),
                ("requested", "Solicitado documental"),
                ("pending_to_pay", "Pendiente documental de pago"),
            ):
                rows.append(
                    f"<p>{escape(title)}: {escape(format_money(values.get(key, values.get(key.removesuffix("_total")))))}</p>"
                )
            for alert in card.get("alerts") or []:
                if isinstance(alert, dict):
                    rows.append(
                        f"<p><b>Alerta:</b> {escape(str(alert.get('title') or 'Sin título'))} · {escape(str(alert.get('severity') or 'Sin nivel'))}</p>"
                    )
    return (
        f"<article><h2>{escape(str(label))}</h2><p>{escape(str(summary.get('message') or 'Reporte de Dirección'))}</p>"
        f"<p>Corte: {escape(str(cut))}</p>{''.join(rows) or '<p>Sin indicadores homologados; consultar el tablero con cifras verificables.</p>'}</article>"
    )
