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

    assert "Cashflow Planning read-only" in html
    assert "Caja real neta" in html
    assert "Obligaciones aprobadas" in html
    assert "Cobranza AR probada" in html
    assert "Forecast derivado" in html
    assert "No usa candidatos AR como cobranza" in html


def test_render_cashflow_planning_html_escapes_source_notes():
    html = render_cashflow_planning_html(_payload())

    assert "nota &lt;riesgo&gt;" in html
    assert "nota <riesgo>" not in html


def test_render_cashflow_planning_html_does_not_treat_candidates_as_collection():
    html = render_cashflow_planning_html(_payload()).lower()

    assert "candidate_match cobrado" not in html
    assert "candidato ar cobrado" not in html
    assert "candidatos ar como cobranza" in html
