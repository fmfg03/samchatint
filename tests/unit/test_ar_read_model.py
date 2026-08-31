from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from samchat.ar.service import build_ar_read_model


@pytest.fixture(autouse=True)
def no_active_collection_matches():
    with patch(
        "samchat.ar.service.list_ar_collection_matches",
        new=AsyncMock(return_value=[]),
    ):
        yield


@pytest.mark.asyncio
async def test_ar_read_model_groups_expected_income_lines():
    session = object()
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "line-1",
                        "budget_version_id": "version-1",
                        "budget_concept_id": "concept-1",
                        "tournament_id": "tournament-1",
                        "tournament_code": "COPA",
                        "tournament_name": "Copa",
                        "phase": "Nacional",
                        "concept_name": "Patrocinio",
                        "budget_amount": 1200,
                    }
                ]
            ),
        ) as list_lines,
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(
                return_value={
                    "line-1": {
                        1: {"expected_income_amount": 500},
                        2: {"expected_income_amount": 700},
                    }
                }
            ),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(
            session,
            budget_version_id="version-1",
            tournament_id="tournament-1",
        )

    list_lines.assert_awaited_once_with(
        session,
        version_id="version-1",
        tournament_id="tournament-1",
        tournament_code=None,
        line_direction="income",
        limit=500,
        ensure_schema=True,
    )
    assert result["read_only"] is True
    assert result["expected_income"][0]["expected_income_amount"] == 1200.0
    assert result["expected_income"][0]["monthly_plan"] == [
        {"month": 1, "expected_income_amount": 500.0},
        {"month": 2, "expected_income_amount": 700.0},
    ]
    assert result["summary"]["expected_income_total"] == 1200.0


@pytest.mark.asyncio
async def test_ar_read_model_marks_linked_cfdi_as_collection_unknown():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 2000}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-1",
                        "cfdi_report_id": "cfdi-1",
                        "budget_line_id": "line-1",
                        "budget_version_id": "version-1",
                        "amount": 1500,
                        "income_date": datetime(2026, 1, 15),
                        "cfdi_uuid": "uuid-1",
                        "receptor_rfc": "CLI010101AAA",
                        "receptor_nombre": "Cliente SA",
                        "concept_name": "Patrocinio",
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    linked = result["issued_linked"][0]
    assert linked["status"] == "recognized"
    assert linked["collection_status"] == "collection_unknown"
    assert linked["outstanding_amount"] is None
    assert linked["outstanding_amount_status"] == "unknown"
    assert result["collection_gaps"][0]["item_id"] == "linked:link-1"
    assert result["summary"]["linked_income_total"] == 1500.0


@pytest.mark.asyncio
async def test_ar_read_model_includes_unlinked_psp_candidates():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "cfdi-2",
                        "cfdi_uuid": "uuid-2",
                        "fecha": datetime(2026, 2, 1),
                        "total": 900,
                        "emisor_rfc": "PSP010101AAA",
                        "emisor_nombre": "PSP",
                        "receptor_rfc": "CLI020202BBB",
                        "receptor_nombre": "Cliente Dos",
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    candidate = result["issued_unlinked"][0]
    assert candidate["status"] == "issued_unlinked"
    assert candidate["collection_status"] == "collection_unknown"
    assert candidate["outstanding_amount_status"] == "unknown"
    assert any(
        gap["reason"] == "missing_budget_income_link"
        for gap in result["matching_gaps"]
    )


@pytest.mark.asyncio
async def test_ar_read_model_reports_matching_gaps_for_missing_payer_or_line():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-2",
                        "budget_line_id": "missing-line",
                        "amount": 100,
                        "receptor_rfc": "",
                        "receptor_nombre": "",
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    reasons = {gap["reason"] for gap in result["matching_gaps"]}
    assert "payer_gap" in reasons
    assert "budget_line_not_in_scope" in reasons


@pytest.mark.asyncio
async def test_ar_read_model_never_sets_outstanding_without_collection_source():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 100}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {"id": "link-1", "budget_line_id": "line-1", "amount": 100}
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[{"id": "cfdi-1", "total": 50}]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    assert result["collection_source"] == "unknown"
    assert result["outstanding_amount_status"] == "unknown"
    all_items = (
        result["expected_income"]
        + result["issued_linked"]
        + result["issued_unlinked"]
        + result["collection_gaps"]
    )
    assert all(item["outstanding_amount"] is None for item in all_items)
    assert all(
        item["outstanding_amount_status"] == "unknown" for item in all_items
    )


@pytest.mark.asyncio
async def test_ar_read_model_is_read_only_shape():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    assert result == {
        "ok": True,
        "read_only": True,
        "budget_version_id": "version-1",
        "tournament_id": None,
        "tournament_code": None,
        "collection_source": "unknown",
        "outstanding_amount_status": "unknown",
        "summary": {
            "expected_income_count": 0,
            "expected_income_total": 0.0,
            "issued_linked_count": 0,
            "linked_income_total": 0.0,
            "issued_unlinked_count": 0,
            "issued_unlinked_total": 0.0,
            "collection_gap_count": 0,
            "matching_gap_count": 0,
        },
        "expected_income": [],
        "issued_linked": [],
        "issued_unlinked": [],
        "collection_gaps": [],
        "matching_gaps": [],
    }


@pytest.mark.asyncio
async def test_ar_read_model_marks_active_match_as_collected():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 100}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-1",
                        "budget_line_id": "line-1",
                        "budget_version_id": "version-1",
                        "amount": 100,
                        "receptor_rfc": "CLI010101AAA",
                        "receptor_nombre": "Cliente",
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "match-1",
                        "ar_item_id": "linked:link-1",
                        "accepted_amount": 100,
                        "collection_date": "2026-01-16T00:00:00",
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    linked = result["issued_linked"][0]
    assert linked["collection_status"] == "matched_collected"
    assert linked["collected_amount"] == 100.0
    assert linked["outstanding_amount"] == 0.0
    assert linked["outstanding_amount_status"] == "known"
    assert result["collection_gaps"] == []


@pytest.mark.asyncio
async def test_ar_read_model_can_skip_schema_ensure_for_render_paths():
    session = object()
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[]),
        ) as list_lines,
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ) as list_monthly,
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ) as list_candidates,
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(return_value=[]),
        ) as list_matches,
    ):
        await build_ar_read_model(
            session,
            budget_version_id="version-1",
            ensure_schema=False,
        )

    assert list_lines.await_args.kwargs["ensure_schema"] is False
    assert list_monthly.await_args.kwargs["ensure_schema"] is False
    assert list_candidates.await_args.kwargs["ensure_schema"] is False
    assert list_matches.await_args.kwargs["ensure_schema"] is False
