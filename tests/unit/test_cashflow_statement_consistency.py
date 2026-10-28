"""Cashflow statement consumes bank facts without creating another cash result."""

from datetime import date
from decimal import Decimal

import pytest

from samchat.cashflow.service import build_cashflow_report
from samchat.executive.template_reports import build_cashflow_statement_report


def annual_buckets(currency="MXN", year=2026):
    return [
        {
            "key": f"{year}-{month:02d}",
            "year": year,
            "month": month,
            "currency": currency,
            "actual_cash_in": Decimal("100.10"),
            "actual_cash_out": Decimal("30.05"),
            "collected_income": Decimal("100.10"),
            "expected_uncollected_income": Decimal("20"),
            "approved_obligations": Decimal("10"),
            "opening_balance": None,
            "closing_balance": None,
        }
        for month in range(1, 13)
    ]


def report(buckets, **options):
    return build_cashflow_statement_report(
        {"period": {"year": 2026, "month": 6}, "monthly_buckets": buckets},
        **options,
    )


def test_collected_and_approved_rows_do_not_change_bank_cash():
    result = report(annual_buckets(), scale=1)
    rows = {row["segment"]: row for row in result["rows"]}
    assert rows["Origen"]["months"][0] == Decimal("100.10")
    assert rows["Aplicaciones"]["months"][0] == Decimal("30.05")
    assert rows["Cobranza comprobada"]["months"][0] == Decimal("100.10")
    assert rows["Obligaciones aprobadas"]["months"][0] == Decimal("10")
    assert result["summary"]["origen_total"] == Decimal("1201.20")
    assert result["summary"]["aplicaciones_total"] == Decimal("360.60")
    assert result["summary"]["saldo_inicial"] is None
    assert result["summary"]["saldo_final"] is None


def test_missing_amount_and_missing_month_are_not_zero():
    buckets = annual_buckets()
    buckets[0]["actual_cash_in"] = None
    buckets.pop()
    result = report(buckets)
    assert result["rows"][1]["months"][0] is None
    assert result["rows"][1]["months"][11] is None
    assert result["rows"][1]["total"] is None
    assert result["summary"]["origen_total"] is None
    assert any("partial_coverage" in note for note in result["source_notes"])


def test_zero_remains_zero_and_nonfinite_amount_remains_unavailable():
    buckets = annual_buckets()
    buckets[0]["actual_cash_in"] = 0
    buckets[1]["actual_cash_in"] = float("nan")
    result = report(buckets)
    assert result["rows"][1]["months"][0] == 0
    assert result["rows"][1]["months"][1] is None
    assert result["summary"]["origen_total"] is None


def test_source_stock_totals_use_endpoints_without_summing_or_deriving():
    buckets = annual_buckets()
    for index, bucket in enumerate(buckets):
        bucket["opening_balance"] = Decimal(1000 + index)
        bucket["closing_balance"] = Decimal(1001 + index)
    result = report(buckets, scale=1, opening_balance=999999)
    assert result["rows"][0]["total"] == 1000
    assert result["rows"][-1]["total"] == 1012
    assert result["summary"]["saldo_inicial"] == 1000
    assert result["summary"]["saldo_final"] == 1012
    assert any("argument_not_evidence" in note for note in result["source_notes"])


def test_opening_argument_alone_is_not_evidence():
    result = report(annual_buckets(), opening_balance=100000)
    assert all(value is None for value in result["rows"][0]["months"])
    assert result["summary"]["saldo_inicial"] is None
    assert result["summary"]["saldo_final"] is None


def test_next_year_january_does_not_overwrite_requested_year():
    buckets = annual_buckets()
    following = {**buckets[0], "year": 2027, "key": "2027-01", "actual_cash_in": 999999}
    result = report([*buckets, following], scale=1)
    assert result["rows"][1]["months"][0] == Decimal("100.10")
    assert result["summary"]["origen_total"] == Decimal("1201.20")
    assert any("period_excluded" in note for note in result["source_notes"])


def test_month_without_year_does_not_inherit_a_fictitious_year():
    result = report([{"month": 1, "currency": "MXN", "actual_cash_in": 100}])
    assert result["rows"][1]["months"][0] is None
    assert result["summary"]["origen_total"] is None


def test_currency_blocks_do_not_create_nominal_consolidated_amounts():
    result = report([*annual_buckets("MXN"), *annual_buckets("USD")], scale=1)
    assert result["summary"]["origen_total"] is None
    assert all(value is None for value in result["rows"][1]["months"])
    assert set(result["currency_reports"]) == {"MXN", "USD"}
    for currency in ("MXN", "USD"):
        block = result["currency_reports"][currency]
        assert block["currency"] == currency
        assert block["summary"]["origen_total"] == Decimal("1201.20")


def test_explicit_source_currency_selects_only_that_block():
    result = build_cashflow_statement_report(
        {
            "period": {"year": 2026},
            "currency": "USD",
            "monthly_buckets": [*annual_buckets("MXN"), *annual_buckets("USD")],
        },
        scale=1,
    )
    assert set(result["currency_reports"]) == {"USD"}
    assert result["summary"]["origen_total"] == Decimal("1201.20")


def test_unknown_currency_is_unavailable_instead_of_assumed_mxn():
    buckets = annual_buckets()
    for bucket in buckets:
        bucket.pop("currency")
    result = report(buckets)
    assert result["summary"]["origen_total"] is None
    assert "N/D" in result["subtitle"]
    assert any("currency_unavailable" in note for note in result["source_notes"])


def test_duplicate_period_is_not_silently_overwritten():
    buckets = annual_buckets()
    result = report([*buckets, {**buckets[0], "actual_cash_in": 9999}])
    assert result["rows"][1]["months"][0] is None
    assert result["summary"]["origen_total"] is None
    assert any("duplicate_period" in note for note in result["source_notes"])


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_invalid_scale_cannot_produce_a_plausible_report(scale):
    with pytest.raises(ValueError, match="scale"):
        report(annual_buckets(), scale=scale)


def test_statement_preserves_actual_shared_report_without_recounting_collection():
    source = build_cashflow_report(
        {
            "source_status": {
                key: {"available": True, "complete": True}
                for key in ("bank", "ar", "obligations", "budget")
            },
            "bank_rows": [
                {
                    "id": "movement-1",
                    "fecha": date(2026, 1, 10),
                    "currency": "MXN",
                    "signo": "+",
                    "importe": "100.10",
                }
            ],
            "documents": [],
            "ar_payload": {
                "issued_linked": [
                    {
                        "ar_item_id": "income-1",
                        "currency": "MXN",
                        "collected_amount": "100.10",
                        "collection_status": "matched_collected",
                        "outstanding_amount": "0",
                        "outstanding_amount_status": "known",
                        "due_date": "2026-01-10",
                    }
                ]
            },
        },
        start=date(2026, 1, 1),
        end=date(2027, 1, 1),
        cutoff=date(2027, 1, 1),
        forecast_end=date(2027, 2, 1),
    )
    result = build_cashflow_statement_report(source, scale=1)
    assert result["rows"][1]["months"][0] == Decimal("100.10")
    assert result["summary"]["origen_total"] == source["summary"]["actual_cash_in"]
