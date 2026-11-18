"""Balance reconciliation and failure states for Direction."""

from datetime import date, datetime

import pytest

from samchat.cashflow.bank_statements import (
    INVALID_SOURCE,
    NO_COVERAGE,
    NO_OPENING,
    _project,
    bank_series_sha256,
)


def date_in_october(day):
    return date(2026, 10, day)


def statement(
    statement_id=1,
    bounds=(1, 5),
    anchors=(0, 75),
    series=((1, "+", 100, 100), (4, "-", 25, 75)),
    **overrides,
):
    movements = [
        dict(
            movement_id=index,
            source_row_number=index,
            fecha=datetime.combine(date_in_october(day), datetime.min.time()),
            hora=None,
            signo=sign,
            importe=amount,
            saldo=balance,
            cuenta_bancaria="1111111234",
            movement_company_id=None,
            movement_account_id=None,
            movement_currency=None,
        )
        for index, (day, sign, amount, balance) in enumerate(series, 2)
    ]
    certificate = dict(
        id=statement_id,
        account_id=1,
        account_fingerprint="account-1",
        account_last4="1234",
        currency="MXN",
        portfolio_id=1,
        company_id=1,
        scope_account_ids=None,
        coverage_start=date_in_october(bounds[0]),
        coverage_end=date_in_october(bounds[1]),
        opening_balance=anchors[0],
        closing_balance=anchors[1],
        evidence_ref=f"receipt-{statement_id}",
        live_file_sha="source-file",
        source_sha256="source-file",
        series_sha256=bank_series_sha256(movements),
        current_run=True,
        uncontested_attribution=True,
        descending_rows=False,
    )
    certificate.update(overrides)
    return [
        dict(certificate, **movement)
        for movement in movements or [dict(movement_id=None)]
    ]


def report(rows, start=1, end=5):
    return _project(rows, date_in_october(start), date_in_october(end))


def money(report):
    period = report["periods"][0]
    return [
        period[field]
        for field in ("opening", "origins_total", "applications_total", "closing")
    ]


def test_current_period_and_currency_are_derived_from_the_certified_rows():
    data = report(statement())
    assert money(data) == ["0.00", "100.00", "25.00", "75.00"]
    assert data["revision"] == report(statement())["revision"]
    assert "1111111234" not in str(data)
    assert "account-1" not in str(data)
    assert "•••• 1234" in str(data)
    assert "receipt-1" in str(data)


def test_cut_with_no_movements_uses_the_certified_balance():
    data = report(statement(), 2, 3)
    assert money(data) == ["100.00", "0.00", "0.00", "100.00"]
    assert data["gap"] is None


def test_no_future_close_when_coverage_ends_but_opening_is_proven():
    data = report(statement(), 6, 8)
    assert money(data) == ["75.00", None, None, None]
    assert data["gap"] == NO_COVERAGE
    assert data["revision"] is not None


def test_empty_previous_statement_can_anchor_a_verified_documented_close():
    prior = statement(anchors=(None, 75), series=())
    successor = statement(2, (6, 10), (None, 82), ((8, "+", 7, 82),))
    assert money(report(prior)) == [None] * 4
    assert money(report(prior + successor, 6, 10)) == ["75.00", "7.00", "0.00", "82.00"]


def test_contiguous_closed_periods_use_only_endpoints_for_balance():
    successor = statement(2, (6, 10), (None, 82), ((8, "+", 7, 82),))
    data = report(statement() + successor, 1, 10)
    assert money(data) == ["0.00", "107.00", "25.00", "82.00"]
    assert money(report(statement() + successor, 6, 10)) == [
        "75.00",
        "7.00",
        "0.00",
        "82.00",
    ]


def test_gap_blocks_a_total_even_if_both_statements_have_a_balance():
    successor = statement(2, (7, 10), (75, 82), ((8, "+", 7, 82),))
    data = report(statement() + successor, 1, 10)
    assert money(data) == ["0.00", None, None, None]
    assert data["gap"] == NO_COVERAGE


def test_rolling_balance_with_no_opening_anchor_cannot_invent_an_opening():
    rows = statement(anchors=(None, 75))
    assert money(report(rows)) == [None] * 4
    assert report(rows)["gap"] == NO_OPENING
    assert report(rows)["revision"] is not None
    assert money(report(rows, 2, 5)) == ["100.00", "0.00", "25.00", "75.00"]


def test_attribution_change_invalidates_revision_even_when_figures_are_identical():
    before = report(statement(company_id=1))
    after = report(statement(company_id=2))
    assert before["periods"] == after["periods"]
    assert before["revision"] != after["revision"]


def test_bank_file_can_supply_an_explicit_reverse_order():
    rows = statement(
        series=((4, "-", 25, 75), (1, "+", 100, 100)), descending_rows=True
    )
    assert money(report(rows)) == ["0.00", "100.00", "25.00", "75.00"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"anchors": (0, 80)},
        {"series": ((1, "+", 100, 99),)},
        {"series": ((1, "+", 100.001, 100),)},
        {"series": ((1, "+", -100, -100),)},
        {"series": ((1, "+", None, 100),)},
        {"series": ((4, "-", 25, 75), (1, "+", 100, 100))},
        {"series": ((1, "?", 100, 100),)},
        {"series": ((1, "+", 100, "NaN"),)},
        {"current_run": False},
        {"uncontested_attribution": False},
        {"live_file_sha": "new-file"},
        {"series_sha256": "row-was-changed"},
        {"account_last4": "4321"},
    ],
)
def test_invalid_source_is_not_rendered_as_cash(overrides):
    data = report(statement(**overrides))
    assert money(data) == [None] * 4
    assert data["gap"] == INVALID_SOURCE
    assert data["revision"] is None


def test_duplicate_ordinal_is_invalid_even_when_the_digest_matches():
    rows = statement()
    rows[1]["source_row_number"] = rows[0]["source_row_number"]
    new_hash = bank_series_sha256(rows)
    for item in rows:
        item["series_sha256"] = new_hash
    assert report(rows)["gap"] == INVALID_SOURCE


def test_identical_certified_series_is_counted_once_but_other_overlap_is_rejected():
    rows = statement() + statement(2)
    assert money(report(rows)) == ["0.00", "100.00", "25.00", "75.00"]
    rows = statement() + statement(2, (3, 5), (0, 75), ((3, "+", 75, 75),))
    assert report(rows)["gap"] == INVALID_SOURCE


def test_contiguous_statements_with_conflicting_anchors_are_rejected():
    successor = statement(2, (6, 10), (80, 87), ((8, "+", 7, 87),))
    assert report(statement() + successor, 1, 10)["gap"] == INVALID_SOURCE


def test_total_requires_all_certified_accounts_and_a_single_company():
    first = statement(scope_account_ids=[1, 2])
    second = statement(
        2, account_id=2, account_fingerprint="account-2", scope_account_ids=[1, 2]
    )
    assert money(report(first)) == [None] * 4
    assert money(report(first + second)) == ["0.00", "200.00", "50.00", "150.00"]
    second[0]["company_id"] = 2
    assert report(first + second)["gap"] == INVALID_SOURCE


def test_currency_and_new_company_columns_fail_closed():
    assert "MXN" in report(statement(currency="USD"))["gap"]
    for field in ("movement_company_id", "movement_account_id", "movement_currency"):
        rows = statement()
        rows[0][field] = "wrong"
        assert report(rows)["gap"] == INVALID_SOURCE
