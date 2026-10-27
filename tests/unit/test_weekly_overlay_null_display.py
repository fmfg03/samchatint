"""Unknown planning and forecast values must never be presented as zero."""

from samchat.assistant.finance_read_answer import render_finance_read_answer
from samchat.cashflow.admin_ui import render_cashflow_planning_html
from samchat.executive.template_reports import build_cashflow_statement_report


def test_cashflow_display_distinguishes_unknown_from_confirmed_zero():
    payload = {
        "summary": {
            "planned_budget_income": 0,
            "planned_budget_expense": None,
            "expected_uncollected_income": None,
            "forecast_net": None,
        },
        "monthly_buckets": [
            {
                "year": 2026,
                "month": 9,
                "planned_budget_income": 0,
                "planned_budget_expense": None,
                "expected_uncollected_income": None,
                "forecast_net": None,
            }
        ],
    }
    html = render_cashflow_planning_html(payload)
    assert "Ingresos presupuestados</span><strong>$0.00</strong>" in html
    assert "Egresos presupuestados</span><strong>N/D</strong>" in html
    assert "Ingreso esperado no cobrado</span><strong>N/D</strong>" in html
    assert "Proyección neta</span><strong>N/D</strong>" in html
    assert html.count("<td>N/D</td>") >= 3


def test_assistant_preserves_unknown_cashflow_without_masking_real_zero():
    answer = render_finance_read_answer(
        {
            "ok": True,
            "intent": "cashflow.summary",
            "payload": {
                "summary": {
                    "actual_cash_net": 0,
                    "expected_uncollected_income": None,
                    "forecast_net": None,
                }
            },
        }
    )
    assert "Caja real neta: $0.00." in answer
    assert "Ingreso esperado no cobrado: N/D." in answer
    assert "Forecast derivado: N/D." in answer


def test_cashflow_report_preserves_unknown_month_and_total():
    report = build_cashflow_statement_report(
        {
            "period": {"year": 2026, "month": 1},
            "monthly_buckets": [
                {"month": 1, "expected_uncollected_income": None},
                {"month": 2, "expected_uncollected_income": 1250},
                {"month": 3, "expected_uncollected_income": 0},
            ],
        },
        opening_balance=10000,
    )
    row = next(
        row for row in report["rows"] if row["segment"] == "Ingreso esperado no cobrado"
    )
    assert row["months"][:3] == [None, 1.25, 0.0]
    assert row["total"] is None
