from __future__ import annotations

from samchat.cashflow.admin_ui import render_cashflow_planning_html


def _payload() -> dict:
    return {
        "summary": {
            "actual_cash_net": 700,
            "approved_obligations": 300,
            "planned_budget_income": 1200,
            "planned_budget_expense": 800,
            "recognized_income": 500,
            "collected_income": 400,
            "expected_uncollected_income": 1300,
            "forecast_net": 2100,
        },
        "monthly_buckets": [
            {
                "year": 2026,
                "month": 1,
                "actual_cash_in": 1000,
                "actual_cash_out": 300,
                "actual_cash_net": 700,
                "approved_obligations": 300,
                "planned_budget_income": 1200,
                "planned_budget_expense": 800,
                "recognized_income": 500,
                "collected_income": 400,
                "expected_uncollected_income": 1300,
                "forecast_net": 2100,
            }
        ],
        "source_notes": ["nota <riesgo>"],
    }


def test_render_cashflow_planning_html_includes_sections_and_copy():
    html = render_cashflow_planning_html(_payload())

    assert "Flujo de efectivo ejecutivo" in html
    assert "Variación neta" in html
    assert "Pagos aprobados / en proceso" in html
    assert "Ingresos presupuestados" in html
    assert "Egresos presupuestados" in html
    assert "Ingreso reconocido contable" in html
    assert "Cobranza confirmada" in html
    assert "Variación proyectada" in html
    assert "Saldo inicial" in html
    assert "Saldo final" in html
    assert "Lo que viene" in html
    assert "Notas de lectura" in html


def test_render_cashflow_planning_html_hides_internal_copy():
    html = render_cashflow_planning_html(_payload())

    assert "read-only" not in html
    assert "Finance Spine" not in html
    assert "Accepted matches" not in html
    assert "Buckets mensuales" not in html
    assert "Source notes" not in html
    assert "CFDI income" not in html
    assert "AP / payment run" not in html


def test_render_cashflow_planning_html_escapes_source_notes():
    html = render_cashflow_planning_html(_payload())

    assert "nota &lt;riesgo&gt;" in html
    assert "nota <riesgo>" not in html


def test_render_cashflow_planning_html_does_not_treat_candidates_as_collection():
    html = render_cashflow_planning_html(_payload()).lower()

    assert "candidate_match cobrado" not in html
    assert "candidato ar cobrado" not in html
    assert "cobranza confirmada" in html


def test_cashflow_unknown_forecast_is_not_rendered_as_zero():
    payload = _payload()
    for row in [payload["summary"], *payload["monthly_buckets"]]:
        row["forecast_net"] = None
        row["expected_uncollected_income"] = None
    payload["source_notes"] = ["ar_forecast_basis_and_schedule_unproven"]
    html = render_cashflow_planning_html(payload)
    # Opening/closing and other uncovered fields also remain explicitly unknown.
    assert "Variación proyectada</dt><dd>N/D</dd>" in html
    assert "Cobros pendientes</dt><dd>N/D</dd>" in html
    assert "base comparable" in html
    assert "ar_forecast_basis" not in html
    assert "$500.00" in html
    assert "$400.00" in html


def test_report_translates_collection_and_budget_coverage_notes():
    payload = _payload()
    payload["source_notes"] = [
        "ar_currency_coverage_missing",
        "ar_collection_conflict",
        "budget_distribution_requires_reconciliation",
        "budget_plan_unavailable:ProgrammingError",
    ]
    html = render_cashflow_planning_html(payload)
    assert "moneda no está acreditada" in html
    assert "evidencia en conflicto" in html
    assert "calendario de egresos presupuestados requiere conciliación" in html
    assert "calendario de egresos presupuestados no está disponible" in html
    for note in payload["source_notes"]:
        assert note not in html
