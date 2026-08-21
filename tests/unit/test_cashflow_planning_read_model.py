from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from samchat.cashflow.service import build_cashflow_planning_read_model


@pytest.fixture(autouse=True)
def default_sources():
    with (
        patch(
            "samchat.cashflow.service.list_bank_cash_movements",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.cashflow.service.build_finance_source_snapshot",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.cashflow.service.build_finance_platform_snapshot",
            new=Mock(return_value={}),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_cashflow_empty_model_without_budget_version():
    result = await build_cashflow_planning_read_model(
        object(),
        year=2026,
        month=1,
    )

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["budget_version_id"] is None
    assert "missing_budget_version_id" in result["source_notes"]
    assert len(result["monthly_buckets"]) == 3


@pytest.mark.asyncio
async def test_cashflow_monthly_plan_separates_income_and_expense():
    with (
        patch(
            "samchat.cashflow.service.list_budget_lines",
            new=AsyncMock(
                return_value=[
                    {"id": "income-1", "budget_direction": "income"},
                    {"id": "expense-1", "budget_direction": "expense"},
                ]
            ),
        ),
        patch(
            "samchat.cashflow.service.list_monthly_plan_for_lines",
            new=AsyncMock(
                return_value={
                    "income-1": {1: {"expected_income_amount": 1200}},
                    "expense-1": {1: {"budget_expense_amount": 700}},
                }
            ),
        ),
        patch(
            "samchat.cashflow.service.build_ar_read_model",
            new=AsyncMock(return_value={"issued_linked": [], "issued_unlinked": []}),
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            budget_version_id="version-1",
            year=2026,
            month=1,
        )

    bucket = result["monthly_buckets"][0]
    assert bucket["planned_budget_income"] == 1200.0
    assert bucket["planned_budget_expense"] == 700.0
    assert result["summary"]["planned_budget_income"] == 1200.0


@pytest.mark.asyncio
async def test_cashflow_matched_collected_counts_as_collected_income():
    with (
        patch(
            "samchat.cashflow.service.list_budget_lines",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.cashflow.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.cashflow.service.build_ar_read_model",
            new=AsyncMock(
                return_value={
                    "issued_linked": [
                        {
                            "linked_income_amount": 500,
                            "recognized_income_date": "2026-01-10T00:00:00",
                            "collection_status": "matched_collected",
                            "collected_amount": 500,
                            "collection_date": "2026-01-12T00:00:00",
                        }
                    ],
                    "issued_unlinked": [],
                }
            ),
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            budget_version_id="version-1",
            year=2026,
            month=1,
        )

    assert result["summary"]["recognized_income"] == 500.0
    assert result["summary"]["collected_income"] == 500.0
    assert result["monthly_buckets"][0]["expected_uncollected_income"] == 0.0


@pytest.mark.asyncio
async def test_cashflow_collection_unknown_does_not_count_as_collected_income():
    with (
        patch(
            "samchat.cashflow.service.list_budget_lines",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.cashflow.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.cashflow.service.build_ar_read_model",
            new=AsyncMock(
                return_value={
                    "issued_linked": [
                        {
                            "linked_income_amount": 500,
                            "recognized_income_date": "2026-01-10T00:00:00",
                            "collection_status": "collection_unknown",
                        }
                    ],
                    "issued_unlinked": [],
                }
            ),
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            budget_version_id="version-1",
            year=2026,
            month=1,
        )

    assert result["summary"]["recognized_income"] == 500.0
    assert result["summary"]["collected_income"] == 0.0
    assert result["monthly_buckets"][0]["expected_uncollected_income"] == 500.0


@pytest.mark.asyncio
async def test_cashflow_does_not_consume_candidate_prematching():
    with (
        patch(
            "samchat.cashflow.service.list_budget_lines",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.cashflow.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.cashflow.service.build_ar_read_model",
            new=AsyncMock(
                return_value={
                    "issued_linked": [
                        {
                            "linked_income_amount": 400,
                            "recognized_income_date": "2026-01-10T00:00:00",
                            "collection_status": "candidate_match",
                        }
                    ],
                    "issued_unlinked": [],
                }
            ),
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            budget_version_id="version-1",
            year=2026,
            month=1,
        )

    assert result["summary"]["collected_income"] == 0.0
    assert result["summary"]["expected_uncollected_income"] == 400.0


@pytest.mark.asyncio
async def test_cashflow_bank_movements_only_impact_actual_cash():
    with patch(
        "samchat.cashflow.service.list_bank_cash_movements",
        new=AsyncMock(
            return_value=[
                {
                    "fecha": datetime(2026, 1, 5),
                    "signo": "+",
                    "importe": 1000,
                    "conciliacion_estado": "high",
                },
                {
                    "fecha": datetime(2026, 1, 6),
                    "signo": "-",
                    "importe": 300,
                    "conciliacion_estado": "high",
                },
            ]
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            year=2026,
            month=1,
        )

    assert result["summary"]["actual_cash_in"] == 1000.0
    assert result["summary"]["actual_cash_out"] == 300.0
    assert result["summary"]["actual_cash_net"] == 700.0
    assert result["summary"]["collected_income"] == 0.0


@pytest.mark.asyncio
async def test_cashflow_source_failure_adds_note_without_failing():
    with (
        patch(
            "samchat.cashflow.service.list_budget_lines",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "samchat.cashflow.service.build_ar_read_model",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            budget_version_id="version-1",
            year=2026,
            month=1,
        )

    assert result["ok"] is True
    assert "budget_plan_unavailable:RuntimeError" in result["source_notes"]
    assert "ar_unavailable:RuntimeError" in result["source_notes"]


@pytest.mark.asyncio
async def test_cashflow_uses_finance_platform_for_obligations():
    with (
        patch(
            "samchat.cashflow.service.build_finance_source_snapshot",
            new=AsyncMock(return_value={"source": "ok"}),
        ),
        patch(
            "samchat.cashflow.service.build_finance_platform_snapshot",
            new=Mock(return_value={"payment_run": {"payable_total": 900}}),
        ),
    ):
        result = await build_cashflow_planning_read_model(
            object(),
            year=2026,
            month=1,
        )

    assert result["summary"]["approved_obligations"] == 900.0
    assert result["monthly_buckets"][0]["approved_obligations"] == 900.0
