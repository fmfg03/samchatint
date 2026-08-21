from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from samchat.ar.matching import build_ar_matching_workbench


@pytest.fixture(autouse=True)
def no_active_collection_matches():
    with patch(
        "samchat.ar.matching.list_ar_collection_matches",
        new=AsyncMock(return_value=[]),
    ):
        yield


def _read_model_item(
    *,
    amount: float = 100,
    payer_rfc: str = "CLI010101AAA",
    payer_name: str = "Cliente Uno",
) -> dict:
    return {
        "ok": True,
        "issued_linked": [
            {
                "ar_item_id": "linked:1",
                "issued_amount": amount,
                "payer_rfc": payer_rfc,
                "payer_name": payer_name,
                "recognized_income_date": "2026-01-15T00:00:00",
                "collection_status": "collection_unknown",
            }
        ],
        "issued_unlinked": [],
    }


def _bank_movement(
    *,
    movement_id: str = "bank-1",
    amount: float = 100,
    rfc: str = "CLI010101AAA",
    name: str = "Cliente Uno",
    description: str | None = None,
) -> dict:
    return {
        "id": movement_id,
        "importe": amount,
        "rfc_ordenante": rfc,
        "nombre_ordenante": name,
        "descripcion": description or f"Transferencia {name}",
        "fecha": datetime(2026, 1, 16),
    }


@pytest.mark.asyncio
async def test_candidate_match_requires_amount_and_identity():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item()),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(return_value=[_bank_movement()]),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["items"][0]["status"] == "candidate_match"
    assert result["items"][0]["reason"] == "amount_and_identity_candidate"
    assert result["summary"]["candidate_match_count"] == 1


@pytest.mark.asyncio
async def test_amount_only_requires_manual_review():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item()),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(
                return_value=[
                    _bank_movement(rfc="OTR010101AAA", name="Otra Persona")
                ]
            ),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["items"][0]["status"] == "manual_match_required"
    assert result["items"][0]["reason"] == "amount_only"


@pytest.mark.asyncio
async def test_identity_only_requires_manual_review():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item(amount=250)),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(return_value=[_bank_movement(amount=100)]),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["items"][0]["status"] == "manual_match_required"
    assert result["items"][0]["reason"] == "identity_only"


@pytest.mark.asyncio
async def test_multiple_candidates_require_manual_review():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item()),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(
                return_value=[
                    _bank_movement(movement_id="bank-1"),
                    _bank_movement(movement_id="bank-2"),
                ]
            ),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["items"][0]["status"] == "manual_match_required"
    assert result["items"][0]["reason"] == "multiple_candidate_bank_inflows"


@pytest.mark.asyncio
async def test_without_candidates_keeps_collection_unknown():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item()),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["items"][0]["status"] == "collection_unknown"
    assert result["items"][0]["reason"] == "no_candidate_bank_evidence"


@pytest.mark.asyncio
async def test_unmatched_bank_inflows_are_reported():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value={"issued_linked": [], "issued_unlinked": []}),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(return_value=[_bank_movement()]),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["unmatched_bank_inflows"][0]["status"] == "unmatched_bank_inflow"
    assert result["summary"]["unmatched_bank_inflow_count"] == 1


@pytest.mark.asyncio
async def test_matching_workbench_never_confirms_collection_statuses():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item()),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(return_value=[_bank_movement()]),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    serialized = str(result).lower()
    assert "matched_collected" not in serialized
    assert "'collected'" not in serialized
    assert "'paid'" not in serialized
    assert result["collection_authority"] is False


@pytest.mark.asyncio
async def test_matching_workbench_skips_items_with_active_matches():
    with (
        patch(
            "samchat.ar.matching.build_ar_read_model",
            new=AsyncMock(return_value=_read_model_item()),
        ),
        patch(
            "samchat.ar.matching.list_candidate_bank_inflows",
            new=AsyncMock(return_value=[_bank_movement()]),
        ),
        patch(
            "samchat.ar.matching.list_ar_collection_matches",
            new=AsyncMock(
                return_value=[
                    {"id": "match-1", "ar_item_id": "linked:1"}
                ]
            ),
        ),
    ):
        result = await build_ar_matching_workbench(
            object(),
            budget_version_id="version-1",
        )

    assert result["items"] == []
    assert result["accepted_matches"][0]["id"] == "match-1"
    assert result["summary"]["accepted_match_count"] == 1
