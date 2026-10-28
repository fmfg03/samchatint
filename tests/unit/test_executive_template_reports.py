from decimal import Decimal

from samchat.executive.template_reports import (
    MONTH_LABELS_ES,
    build_budget_vs_actual_report,
    build_cashflow_statement_report,
)


def test_cashflow_statement_report_matches_uploaded_workbook_shape() -> None:
    report = build_cashflow_statement_report(
        {
            "period": {"year": 2026, "month": 6, "horizon_months": 12},
            "monthly_buckets": [
                {
                    "year": 2026,
                    "month": 1,
                    "currency": "MXN",
                    "actual_cash_in": 100000,
                    "collected_income": 50000,
                    "expected_uncollected_income": 20000,
                    "actual_cash_out": 40000,
                    "approved_obligations": 10000,
                },
                {
                    "year": 2026,
                    "month": 2,
                    "currency": "MXN",
                    "actual_cash_in": 0,
                    "collected_income": 25000,
                    "expected_uncollected_income": 5000,
                    "actual_cash_out": 30000,
                    "approved_obligations": 0,
                },
            ],
            "source_notes": ["source note"],
        },
        as_of_year=2026,
        as_of_month=6,
        opening_balance=76000000,
    )

    assert report["report_type"] == "cashflow_statement"
    assert report["columns"] == ["Segmento", *MONTH_LABELS_ES, "Total"]
    assert report["subtitle"] == "Al Junio 2026 (cifras en miles de MXN)"
    labels = [row["segment"] for row in report["rows"]]
    assert labels == [
        "SALDO INICIAL:",
        "Origen",
        "Ingresos reales",
        "Cobranza comprobada",
        "Ingreso esperado no cobrado",
        "Aplicaciones",
        "Salidas reales",
        "Obligaciones aprobadas",
        "SALDO FINAL:",
    ]
    assert report["rows"][1]["months"][0] == 100.0
    assert report["rows"][5]["months"][0] == 40.0
    assert report["summary"]["saldo_final"] is None
    assert report["summary"]["saldo_inicial"] is None
    assert report["read_only"] is True


def test_cashflow_statement_report_exposes_missing_opening_balance() -> None:
    report = build_cashflow_statement_report(
        {"period": {"year": 2026, "month": 6}, "monthly_buckets": []}
    )

    assert report["summary"]["saldo_inicial"] is None
    assert any(
        "missing_opening_bank_balance" in note for note in report["source_notes"]
    )
    assert "forecast_is_derived" in report["safety_labels"]


def test_budget_vs_actual_report_calculates_budget_minus_real_variance() -> None:
    report = build_budget_vs_actual_report(
        {
            "summary": {"edition_year": 2026},
            "breakdowns": {
                "by_phase": [
                    {
                        "label": "Fase Estatal",
                        "budget_total": 360000,
                        "actual_total": 370000,
                        "budget_month": 43560,
                        "actual_month": 42253.2,
                    },
                    {
                        "label": "Fase Nacional",
                        "budget_total": 8000000,
                        "actual_total": 7700000,
                        "budget_month": 968000,
                        "actual_month": 938960,
                    },
                ]
            },
        },
        month=6,
        year=2026,
    )

    assert report["report_type"] == "budget_vs_actual"
    assert report["columns"][1:] == [
        "Presupuesto Junio",
        "Presupuesto Enero-Junio",
        "Real Junio",
        "Real Enero-Junio",
        "Variación Junio",
        "Variación Enero-Junio",
    ]
    assert report["rows"][0]["variance_month"] == 1306.8
    assert report["rows"][0]["variance_accumulated"] == -10000.0
    assert report["summary"]["variance_accumulated_total"] == 290000.0
    assert report["read_only"] is True


def test_budget_vs_actual_report_degrades_without_monthly_granularity() -> None:
    report = build_budget_vs_actual_report(
        {
            "summary": {
                "edition_year": 2026,
                "budget_total": 100000,
                "actual_total": 25000,
            }
        },
        month=6,
        year=2026,
    )

    assert report["rows"][0]["segment"] == "Presupuesto"
    assert report["summary"]["variance_accumulated_total"] == 75000.0
    assert "missing_breakdown_granularity" in report["source_notes"]


def test_cashflow_report_keeps_unknown_expected_receipts_and_total():
    report = build_cashflow_statement_report(
        {
            "period": {"year": 2026},
            "currency": "MXN",
            "monthly_buckets": [
                {
                    "year": 2026,
                    "month": 1,
                    "currency": "MXN",
                    "expected_uncollected_income": None,
                },
                {
                    "year": 2026,
                    "month": 2,
                    "currency": "MXN",
                    "expected_uncollected_income": 1000,
                },
            ],
        },
        opening_balance=0,
    )
    row = next(
        r for r in report["rows"] if r["segment"] == "Ingreso esperado no cobrado"
    )
    assert row["months"][:2] == [None, 1.0]
    assert row["total"] is None


def test_cashflow_report_does_not_restore_unknown_collections_as_zero():
    report = build_cashflow_statement_report(
        {
            "period": {"year": 2026},
            "currency": "MXN",
            "monthly_buckets": [
                {
                    "year": 2026,
                    "currency": "MXN",
                    "month": 1,
                    "actual_cash_in": 1000,
                    "actual_cash_out": 200,
                    "collected_income": None,
                },
                {
                    "year": 2026,
                    "currency": "MXN",
                    "month": 2,
                    "actual_cash_in": 100,
                    "collected_income": 0,
                },
            ],
        },
        opening_balance=100,
    )
    rows = {row["segment"]: row for row in report["rows"]}
    assert rows["Ingresos reales"]["months"][0] == 1
    assert rows["Salidas reales"]["months"][0] == Decimal("0.2")
    assert rows["Origen"]["months"][0] == 1
    for label in ("Cobranza comprobada", "SALDO FINAL:"):
        assert rows[label]["months"][0] is None
        assert rows[label]["total"] is None
    assert report["summary"]["saldo_final"] is None
