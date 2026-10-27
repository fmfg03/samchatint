"""Synthetic collection lifecycle through the canonical budget-backed AR read."""

from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock

import pytest

from samchat.ar import service


@pytest.fixture
def sources(monkeypatch):
    line = {
        "id": "line-1",
        "budget_version_id": "version-1",
        "tournament_id": "tournament-1",
        "budget_amount": 1500,
    }
    link = {
        "id": "link-1",
        "budget_line_id": "line-1",
        "budget_version_id": "version-1",
        "tournament_id": "tournament-1",
        "cfdi_report_id": "invoice-1",
        "amount": 1000,
        "income_date": "2026-01-01",
        "status": "approved",
        "moneda": "MXN",
    }
    mocks = {
        "list_budget_lines": AsyncMock(return_value=[line]),
        "list_monthly_plan_for_lines": AsyncMock(
            return_value={
                "line-1": {1: {"expected_income_amount": 1500}},
            }
        ),
        "list_budget_cfdi_income_links": AsyncMock(return_value=[link]),
        "list_psp_cfdi_income_candidates": AsyncMock(return_value=[]),
        "list_ar_collection_matches": AsyncMock(return_value=[]),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(service, name, mock)
    return mocks


def receipt(number, amount, **overrides):
    return {
        "id": f"receipt-{number}",
        "ar_item_id": "linked:link-1",
        "budget_version_id": "version-1",
        "accepted_amount": amount,
        "status": "accepted_collection_match",
        "collection_date": f"2026-0{number}-02T10:00:00+00:00",
        "bank_movement_id": f"bank-{number}",
        "accounting_poliza_id": f"policy-{number}",
        **overrides,
    }


async def read():
    # No DB connection, settings, credentials, DDL or financial writes.
    return await service.build_ar_read_model(
        object(),
        budget_version_id="version-1",
        tournament_id="tournament-1",
        strict_tournament_scope=True,
        ensure_schema=False,
        as_of_date=date(2026, 1, 1),
        calendar_year=2026,
    )


@pytest.mark.asyncio
async def test_two_partial_receipts_replay_and_reversal_preserve_budget_and_invoice(
    sources,
):
    first, second = receipt(1, 400), receipt(2, 600)
    original = deepcopy([first, second])
    stages = [
        ([], 0, None, "Cobranza desconocida"),
        ([first], 400, 600, "Parcialmente cobrado"),
        ([second, first], 1000, 0, "Cobrado"),
        ([first, second, first], 1000, 0, "Cobrado"),
        (
            [first, {**second, "status": "match_reversed"}],
            400,
            600,
            "Parcialmente cobrado",
        ),
        ([], 0, None, "Cobranza desconocida"),
    ]
    for matches, collected, balance, status in stages:
        sources["list_ar_collection_matches"].return_value = matches
        payload = await read()
        assert payload == await read()
        assert payload["summary"]["expected_income_total"] == 1500
        assert payload["summary"]["invoiced_total"] == 1000
        assert payload["summary"]["collected_total"] == collected
        item = payload["issued_linked"][0]
        assert item["outstanding_amount"] == balance
        assert item["balance_amount"] == balance
        assert item["operational_status"] == status
        if collected == 400:
            gap = payload["collection_gaps"][0]
            assert gap["collection_status"] == "partially_collected"
            assert gap["collected_amount"] == 400
            assert gap["outstanding_amount"] == 600
            assert gap["outstanding_amount_status"] == "known"
        if collected == 1000:
            assert item["collection_match_ids"] == ["receipt-1", "receipt-2"]
            assert item["collection_match_id"] is None
            assert item["collection_date"] is None
            assert [r["accounting_poliza_id"] for r in item["collection_receipts"]] == [
                "policy-1",
                "policy-2",
            ]
            assert [r["collection_date"] for r in item["collection_receipts"]] == [
                first["collection_date"],
                second["collection_date"],
            ]
            preview = service.build_ar_accounting_preview(item)[
                "collection_policy_preview"
            ]
            assert preview["lines"] == []
            assert preview["gaps"] == [
                "multiple_collection_matches_use_posted_policies"
            ]
    assert [first, second] == original
    assert sources["list_ar_collection_matches"].await_args.kwargs["ar_item_ids"] == [
        "linked:link-1"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"ar_item_id": "linked:foreign-tournament"},
        {"budget_version_id": "foreign-version"},
    ],
)
async def test_strict_read_rejects_collection_outside_selected_scope(
    sources, overrides
):
    sources["list_ar_collection_matches"].return_value = [receipt(1, 400, **overrides)]
    with pytest.raises(
        ValueError, match="collection matches escaped budget/tournament scope"
    ):
        await read()


@pytest.mark.asyncio
async def test_unlinked_invoice_partials_do_not_create_budget_assignment(sources):
    sources["list_budget_cfdi_income_links"].return_value = []
    sources["list_psp_cfdi_income_candidates"].return_value = [
        {
            "id": "invoice-2",
            "total": 1000,
            "assigned_tournament_id": "tournament-1",
            "fecha": "2026-01-01",
            "moneda": "MXN",
        }
    ]
    sources["list_ar_collection_matches"].return_value = [
        receipt(1, 250, ar_item_id="candidate:invoice-2"),
        receipt(2, 350, ar_item_id="candidate:invoice-2"),
    ]
    payload = await read()
    item = payload["issued_unlinked"][0]
    assert item["collected_amount"] == 600
    assert item["outstanding_amount"] == 400
    assert not item.get("budget_line_id")
    assert payload["summary"]["linked_income_total"] == 0
    assert payload["summary"]["expected_income_total"] == 1500


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "matches, message",
    [
        (
            [receipt(1, 400), receipt(1, 401)],
            "Conflicting AR collection receipt identity",
        ),
        ([receipt(1, 400, id="")], "Missing AR collection receipt identity"),
    ],
)
async def test_inconsistent_receipt_identity_fails_closed(sources, matches, message):
    sources["list_ar_collection_matches"].return_value = matches
    with pytest.raises(ValueError, match=message):
        await read()


@pytest.mark.asyncio
async def test_single_receipt_preserves_accounting_preview_identity(sources):
    sources["list_ar_collection_matches"].return_value = [receipt(1, 1000)]
    item = (await read())["issued_linked"][0]
    assert item["collection_match_id"] == "receipt-1"
    assert item["collection_date"] == receipt(1, 1000)["collection_date"]
    assert item["outstanding_amount"] == 0
