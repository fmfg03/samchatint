"""Admin HTML rendering for cashflow planning."""

from __future__ import annotations

from html import escape
from typing import Any


def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"${amount:,.2f}"


def _text(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return escape(text or fallback)


def _metric(label: str, value: Any, note: str) -> str:
    return (
        "<div>"
        f"<span>{escape(label)}</span>"
        f"<strong>{_money(value)}</strong>"
        f"<small>{escape(note)}</small>"
        "</div>"
    )


def _monthly_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="11" class="cashflow-muted">Sin buckets.</td></tr>'
    rendered = []
    for row in rows:
        rendered.append(
            "<tr>"
            f"<td>{int(row.get('year') or 0)}-{int(row.get('month') or 0):02d}</td>"
            f"<td>{_money(row.get('actual_cash_in'))}</td>"
            f"<td>{_money(row.get('actual_cash_out'))}</td>"
            f"<td>{_money(row.get('actual_cash_net'))}</td>"
            f"<td>{_money(row.get('approved_obligations'))}</td>"
            f"<td>{_money(row.get('planned_budget_income'))}</td>"
            f"<td>{_money(row.get('planned_budget_expense'))}</td>"
            f"<td>{_money(row.get('recognized_income'))}</td>"
            f"<td>{_money(row.get('collected_income'))}</td>"
            f"<td>{_money(row.get('expected_uncollected_income'))}</td>"
            f"<td>{_money(row.get('forecast_net'))}</td>"
            "</tr>"
        )
    return "".join(rendered)


def _source_notes(notes: list[Any]) -> str:
    if not notes:
        return '<li class="cashflow-muted">Fuentes completas sin notas.</li>'
    return "".join(f"<li>{_text(note)}</li>" for note in notes)


def render_cashflow_planning_html(payload: dict[str, Any]) -> str:
    """Render the cashflow planning read model as an admin body fragment."""

    summary = payload.get("summary") or {}
    rows = list(payload.get("monthly_buckets") or [])
    notes = list(payload.get("source_notes") or [])
    metrics_html = "".join(
        [
            _metric("Caja real neta", summary.get("actual_cash_net"), "Banco"),
            _metric(
                "Obligaciones aprobadas",
                summary.get("approved_obligations"),
                "AP / payment run",
            ),
            _metric(
                "Plan ingresos",
                summary.get("planned_budget_income"),
                "Presupuesto",
            ),
            _metric(
                "Plan egresos",
                summary.get("planned_budget_expense"),
                "Presupuesto",
            ),
            _metric(
                "Ingreso reconocido",
                summary.get("recognized_income"),
                "CFDI income",
            ),
            _metric(
                "Cobranza AR probada",
                summary.get("collected_income"),
                "Accepted matches",
            ),
            _metric(
                "Ingreso esperado no cobrado",
                summary.get("expected_uncollected_income"),
                "No caja",
            ),
            _metric("Forecast derivado", summary.get("forecast_net"), "Calculado"),
        ]
    )
    return f"""
        <section class="workspace-card cashflow-warning" style="margin-bottom:18px;">
            <div class="workspace-section-title">Cashflow Planning read-only</div>
            <div class="workspace-section-subtitle">
                Finance Spine. No usa candidatos AR como cobranza. Cobranza AR
                probada viene solo de matches aceptados; forecast derivado se
                mantiene separado de caja real y plan.
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Resumen</div>
            <div class="cashflow-metrics">
                {metrics_html}
            </div>
        </section>
        <section class="workspace-card" style="margin-bottom:18px;">
            <div class="workspace-section-title">Buckets mensuales</div>
            <table class="cashflow-table">
                <thead>
                    <tr>
                        <th>Mes</th>
                        <th>Cash in</th>
                        <th>Cash out</th>
                        <th>Caja neta</th>
                        <th>Obligaciones</th>
                        <th>Plan ingresos</th>
                        <th>Plan egresos</th>
                        <th>Ingreso reconocido</th>
                        <th>Cobranza AR probada</th>
                        <th>Esperado no cobrado</th>
                        <th>Forecast derivado</th>
                    </tr>
                </thead>
                <tbody>{_monthly_rows(rows)}</tbody>
            </table>
        </section>
        <section class="workspace-card">
            <div class="workspace-section-title">Source notes</div>
            <ul>{_source_notes(notes)}</ul>
        </section>
    """


def cashflow_admin_styles() -> str:
    """CSS for the cashflow admin fragment."""

    return """
        .cashflow-warning {
            border-color:#bfdbfe;
            background:#eff6ff;
        }
        .cashflow-metrics {
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
            gap:12px;
            margin-top:14px;
        }
        .cashflow-metrics div {
            border:1px solid #e2e8f0;
            border-radius:8px;
            padding:14px;
            background:#ffffff;
        }
        .cashflow-metrics span,
        .cashflow-metrics small {
            display:block;
            color:#64748b;
            font-size:12px;
            font-weight:800;
            text-transform:uppercase;
        }
        .cashflow-metrics strong {
            display:block;
            margin:6px 0;
            color:#0f172a;
            font-size:1.15rem;
        }
        .cashflow-table {
            width:100%;
            border-collapse:separate;
            border-spacing:0;
        }
        .cashflow-table th,
        .cashflow-table td {
            text-align:left;
            padding:11px 12px;
            border-bottom:1px solid #e2e8f0;
            vertical-align:top;
        }
        .cashflow-table th {
            color:#64748b;
            font-size:11px;
            text-transform:uppercase;
            background:#f8fafc;
        }
        .cashflow-muted {
            color:#64748b;
        }
    """
