"""Contracts for explicit periods, documentary/accounting amounts and conflicts."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from samchat.ar import service
from samchat.ar.admin_ui import render_ar_read_model_html
from samchat.budgets.distribution import week_bounds


@pytest.mark.parametrize("year", [2023, 2024, 2026, 2027])
def test_weekly_income_uses_start_month_and_includes_week_52(year):
    weekly = {w: {"expected_income_amount": w} for w in range(1, 53)}
    monthly = service._month_rows(weekly, year)
    assert sum(row["expected_income_amount"] for row in monthly) == 1378
    for row in monthly:
        assert row["expected_income_amount"] == sum(
            w for w in range(1, 53) if week_bounds(year, w)[0].month == row["month"]
        )
    assert monthly[-1]["month"] == 12


def test_week_straddling_month_is_not_split_and_missing_year_is_unknown():
    assert service._month_rows({14: {"expected_income_amount": 1160}}, 2026) == [
        {"month": 3, "expected_income_amount": 1160}
    ]  # Week starts Mar 30, crosses Apr 1.
    assert service._month_rows({14: {"expected_income_amount": 1160}}, 2024) == [
        {"month": 4, "expected_income_amount": 1160}
    ]
    assert service._month_rows({52: {"expected_income_amount": 1160}}, None) == []
    with pytest.raises(ValueError, match="Semana fuera"):
        service._month_rows({53: {"expected_income_amount": 1}}, 2026)


@pytest.mark.parametrize(
    "year,priority",
    [(2025, "alta"), (2026, "media"), (2027, "baja"), (None, "sin periodo")],
)
def test_billing_priority_uses_explicit_budget_year(year, priority):
    rows = service.build_ar_billing_schedule(
        {
            "expected_income": [
                {
                    "status": "planned",
                    "expected_income_amount": 100,
                    "calendar_year": year,
                    "monthly_plan": [{"month": 1, "expected_income_amount": 100}],
                }
            ]
        },
        as_of_date=date(2026, 1, 15),
    )
    assert rows[0]["priority"] == priority


@pytest.fixture
def sources(monkeypatch):
    link = {
        "id": "link",
        "cfdi_report_id": "invoice",
        "budget_line_id": "line",
        "budget_version_id": "version",
        "tournament_id": "tournament",
        "status": "approved",
        "amount": 1160,
        "cfdi_total": 1160,
        "recognized_income_amount": 1000,
        "recognized_poliza_id": "receivable-policy",
        "moneda": "MXN",
        "income_date": "2026-01-01",
    }
    mocks = {
        "list_budget_lines": AsyncMock(
            return_value=[
                {"id": "line", "budget_amount": 1500, "tournament_id": "tournament"}
            ]
        ),
        "list_monthly_plan_for_lines": AsyncMock(
            return_value={"line": {52: {"expected_income_amount": 1500}}}
        ),
        "list_budget_cfdi_income_links": AsyncMock(return_value=[link]),
        "list_psp_cfdi_income_candidates": AsyncMock(return_value=[]),
        "list_ar_collection_matches": AsyncMock(
            return_value=[
                {
                    "id": "receipt",
                    "ar_item_id": "linked:link",
                    "budget_version_id": "version",
                    "status": "accepted_collection_match",
                    "accepted_amount": 400,
                    "collection_date": "2026-02-01",
                    "accounting_poliza_id": "collection-policy",
                }
            ]
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(service, name, mock)
    return link, mocks


async def read():
    return await service.build_ar_read_model(
        object(),
        budget_version_id="version",
        tournament_id="tournament",
        calendar_year=2026,
        strict_tournament_scope=True,
        ensure_schema=False,
        as_of_date=date(2026, 1, 1),
    )


@pytest.mark.asyncio
async def test_gross_recognition_collection_balance_and_period_are_separate(sources):
    payload = await read()
    summary = payload["summary"]
    assert summary["expected_income_total"] == 1500
    assert summary["invoiced_gross_total"] == 1160
    assert summary["recognized_income_total"] == 1000
    assert summary["collected_total"] == 400
    assert summary["balance_total"] == 760
    assert payload["expected_income"][0]["monthly_plan"] == [
        {"month": 12, "expected_income_amount": 1500}
    ]
    row = service.build_ar_operational_rows(payload)[0]
    assert row["invoiced_gross_amount"] == 1160
    assert row["recognized_income_amount"] == 1000
    assert row["balance_amount"] == 760


@pytest.mark.asyncio
async def test_unknown_balance_is_not_zero_in_ui_or_flatten(sources):
    _, mocks = sources
    mocks["list_ar_collection_matches"].return_value = []
    payload = await read()
    assert payload["summary"]["balance_total"] is None
    assert payload["source_coverage"]["unknown_balance_count"] == 1
    assert service.build_ar_operational_rows(payload)[0]["balance_amount"] is None
    html = render_ar_read_model_html(payload)
    assert "sin saldo confirmado" in html
    assert "N/D no significa cero" in html


@pytest.mark.asyncio
async def test_conflicting_collection_channels_preserve_both_receipts(sources):
    link, _ = sources
    link.update(collection_date="2026-02-03", collection_poliza_id="direct-policy")
    payload = await read()
    item = payload["issued_linked"][0]
    assert item["collection_status"] == "collection_conflict"
    assert item["direct_collection_evidence"]["accounting_poliza_id"] == "direct-policy"
    assert item["accepted_collection_evidence"]["collection_match_ids"] == ["receipt"]
    assert item["collected_amount"] is None
    assert payload["summary"]["collected_total"] is None
    assert payload["summary"]["balance_total"] is None
    preview = service.build_ar_accounting_preview(item)["collection_policy_preview"]
    assert preview["lines"] == []
    assert preview["gaps"] == ["conflicting_collection_sources"]
    assert "Existe evidencia de cobros en conflicto" in render_ar_read_model_html(
        payload
    )


@pytest.mark.asyncio
async def test_pending_invoice_gross_is_visible_without_recognition(sources):
    link, mocks = sources
    link.update(
        status="pending_approval",
        recognized_income_amount=None,
        recognized_poliza_id=None,
    )
    mocks["list_budget_cfdi_income_links"].side_effect = [[], [link]]
    mocks["list_ar_collection_matches"].return_value = []
    payload = await read()
    assert payload["summary"]["invoiced_gross_total"] == 1160
    assert payload["summary"]["recognized_income_total"] == 0
    assert payload["summary"]["balance_total"] is None
    assert payload["summary"]["pending_link_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,gap",
    [
        ({"cfdi_total": None}, "gross_amount_gap"),
        ({"recognized_income_amount": None}, "recognized_income_gap"),
        ({"moneda": "USD"}, "currency_gap"),
    ],
)
async def test_missing_evidence_never_gets_tax_or_exchange_estimate(
    sources, change, gap
):
    link, _ = sources
    link.update(change)
    payload = await read()
    assert payload["source_coverage"][gap] is True
    key = (
        "invoiced_gross_total"
        if gap != "recognized_income_gap"
        else "recognized_income_total"
    )
    assert payload["summary"][key] is None
    assert link["amount"] == 1160


@pytest.mark.asyncio
async def test_unlink_preserves_accounting_gap_without_claiming_reversal(sources):
    link, mocks = sources
    link["unlinked_at"] = "2026-03-01"
    link["fecha"] = "2025-12-01"
    mocks["list_budget_cfdi_income_links"].side_effect = [[], [link]]
    mocks["list_ar_collection_matches"].return_value = []
    payload = await read()
    assert payload["summary"]["recognized_income_total"] is None
    assert payload["source_coverage"]["unlinked_accounting_count"] == 1
    for key in ("balance_total", "collected_total", "overdue_total"):
        assert payload["summary"][key] is None
    assert (
        payload["unlinked_income_links"][0]["recognized_poliza_id"]
        == "receivable-policy"
    )
    assert (
        payload["unlinked_income_links"][0]["accounting_reversal_status"]
        == "not_verified"
    )
    assert (
        "no acredita cancelación fiscal ni reversa contable"
        in render_ar_read_model_html(payload)
    )


@pytest.mark.asyncio
async def test_missing_year_does_not_read_current_year_invoices(sources):
    _, mocks = sources
    payload = await service.build_ar_read_model(
        object(),
        budget_version_id="version",
        ensure_schema=False,
    )
    mocks["list_psp_cfdi_income_candidates"].assert_not_awaited()
    assert payload["source_coverage"]["candidates_year_missing"] is True
    assert payload["expected_income"][0]["monthly_plan"] == []
    assert payload["summary"]["invoiced_gross_total"] is None


def test_cashflow_preserves_receipt_periods_across_year_boundary():
    from samchat.cashflow.service import build_cashflow_report

    payload = {
        "issued_linked": [
            {
                "ar_item_id": "linked:invoice",
                "currency": "MXN",
                "recognized_income_amount": 1000,
                "recognized_income_date": "2026-12-01",
                "collection_status": "matched_collected",
                "collection_source": "accepted_match",
                "collected_amount": 1160,
                "collection_receipts": [
                    {
                        "id": "receipt-1",
                        "accepted_amount": 400,
                        "collection_date": "2026-12-30",
                    },
                    {
                        "id": "receipt-2",
                        "accepted_amount": 760,
                        "collection_date": "2027-01-02",
                    },
                ],
            }
        ]
    }

    def report():
        return build_cashflow_report(
            {
                "ar_payload": payload,
                "source_status": {"ar": {"available": True, "complete": True}},
            },
            start=date(2026, 12, 1),
            end=date(2027, 2, 1),
            cutoff=date(2027, 1, 31),
            forecast_end=date(2027, 2, 1),
        )

    result = report()
    buckets = {row["key"]: row for row in result["monthly_buckets"]}
    assert buckets["2026-12"]["recognized_income"] == 1000
    assert buckets["2026-12"]["collected_income"] == 400
    assert buckets["2027-01"]["collected_income"] == 760
    # An undated receipt is unknown, without an invoice-date or zero fallback.
    payload["issued_linked"][0]["collection_receipts"][1]["collection_date"] = None
    fresh = report()
    assert fresh["summary"]["collected_income"] is None
    assert all(row["collected_income"] is None for row in fresh["monthly_buckets"])


@pytest.mark.asyncio
async def test_cashflow_weekly_plan_uses_edition_and_does_not_fold_years(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    import devnous.gastos  # noqa: F401  # Match production startup import order.
    from samchat.budgets import service as budgets
    from samchat.budgets import tournament_weekly_overlay
    from samchat.cashflow import service as cashflow

    async def source_rows(session, table, *args):
        return [{"id": "version"}] if table == "budget_versions" else []

    async def savepoint(session, operation):
        return await operation()

    monkeypatch.setattr(cashflow, "_cashflow_legacy_rows", source_rows)
    monkeypatch.setattr(cashflow, "_cashflow_source_savepoint", savepoint)
    monkeypatch.setattr(
        budgets,
        "list_budget_versions",
        AsyncMock(
            return_value=[{"id": "version", "status": "approved", "edition_year": 2026}]
        ),
    )
    monkeypatch.setattr(
        budgets,
        "get_budget_version",
        AsyncMock(side_effect=[{"edition_year": 2026}, {"edition_year": None}]),
    )
    monkeypatch.setattr(
        budgets,
        "list_budget_lines",
        AsyncMock(
            return_value=[{"id": "line", "line_direction": "income", "currency": "MXN"}]
        ),
    )
    monkeypatch.setattr(
        budgets,
        "list_monthly_plan_for_lines",
        AsyncMock(
            return_value={
                "line": {
                    1: {"expected_income_amount": 100},
                    52: {"expected_income_amount": 200},
                }
            }
        ),
    )
    monkeypatch.setattr(
        tournament_weekly_overlay,
        "effective_plans_for_lines",
        AsyncMock(side_effect=lambda session, **kwargs: (kwargs["baseline_plans"], {})),
    )
    monkeypatch.setattr(service, "build_ar_read_model", AsyncMock(return_value={}))

    async def read_report():
        loaded = await cashflow.load_cashflow_sources(
            SimpleNamespace(no_autoflush=nullcontext()),
            start=date(2026, 12, 1),
            end=date(2027, 2, 1),
            forecast_end=date(2027, 2, 1),
            budget_version_id="version",
        )
        return cashflow.build_cashflow_report(
            loaded,
            start=date(2026, 12, 1),
            end=date(2027, 2, 1),
            cutoff=date(2027, 1, 31),
            forecast_end=date(2027, 2, 1),
            currency="MXN",
        )

    result = await read_report()
    buckets = {row["key"]: row for row in result["monthly_buckets"]}
    assert buckets["2026-12"]["planned_budget_income"] == 200
    assert buckets["2027-01"]["planned_budget_income"] == 0
    missing = await read_report()
    assert missing["summary"]["planned_budget_income"] is None
    assert missing["summary"]["planned_budget_expense"] is None


@pytest.mark.asyncio
async def test_cross_version_collection_conflict_is_not_hidden_by_projection(sources):
    link, mocks = sources
    link.update(
        collection_date="2026-02-03",
        collection_poliza_id="direct-policy",
        has_accepted_collection=True,
    )
    mocks["list_ar_collection_matches"].return_value = []
    payload = await read()
    item = payload["issued_linked"][0]
    assert item["collection_status"] == "collection_conflict"
    assert item["accepted_collection_evidence"] == {
        "cfdi_report_id": "invoice",
        "status": "accepted_collection_outside_projection",
    }
    assert payload["summary"]["collected_total"] is None


@pytest.mark.asyncio
async def test_legacy_accounting_ui_receipt_is_visible_without_guessing_amount(sources):
    link, mocks = sources
    link["manual_collection_poliza_ids"] = ["historical-policy"]
    mocks["list_ar_collection_matches"].return_value = []
    payload = await read()
    row = payload["issued_linked"][0]
    assert row["external_collection_evidence"]["accounting_poliza_ids"] == [
        "historical-policy"
    ]
    assert row["accepted_collection_evidence"] is None
    assert row["collection_status"] == "collection_conflict"
    assert row["balance_amount"] is None
    assert payload["summary"]["collected_total"] is None


@pytest.mark.asyncio
async def test_receipt_outside_projection_is_unknown_not_zero_even_without_manual_receipt(
    sources,
):
    link, mocks = sources
    link["has_accepted_collection"] = True
    mocks["list_ar_collection_matches"].return_value = []
    payload = await read()
    assert (
        payload["issued_linked"][0]["accepted_collection_evidence"]["status"]
        == "accepted_collection_outside_projection"
    )
    assert payload["summary"]["collected_total"] is None
    assert payload["summary"]["balance_total"] is None


def test_acceptance_timestamp_does_not_become_bank_collection_date():
    aggregate = service._aggregate_collection_matches(
        [
            {
                "id": "receipt",
                "ar_item_id": "linked:item",
                "status": "accepted_collection_match",
                "accepted_amount": 400,
                "collection_date": None,
                "accepted_at": "2026-02-01",
            }
        ]
    )["linked:item"]
    assert aggregate["collection_date"] is None
    assert aggregate["collection_receipts"][0]["accepted_at"] == "2026-02-01"
