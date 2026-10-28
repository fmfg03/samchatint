"""Reversal preparation fails closed without inventing date, accounts or authority."""

from unittest.mock import AsyncMock, patch

import pytest

from samchat.ar.collection_matches import (
    ARCollectionMatchError,
    reverse_ar_collection_match,
)
from samchat.ar.reversal_preview import prepare_collection_reversal


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"accounting_poliza_id": ""}, "collection_posting_missing"),
        ({"cfdi_report_id": "foreign-cfdi"}, "collection_original_posting_incomplete"),
        ({"rows": []}, "collection_original_posting_incomplete"),
        ({"credit": 99}, "collection_original_posting_unbalanced"),
    ],
)
async def test_reversal_requires_complete_balanced_original(changes, expected):
    match = {
        "id": "match",
        "accounting_poliza_id": "original",
        "cfdi_report_id": "cfdi",
    }
    match.update(changes)
    rows = changes.get(
        "rows",
        [
            {
                "original_line_id": "debit",
                "cfdi_report_id": "cfdi",
                "cuenta_contable_id": "bank",
                "debe": 100,
                "haber": 0,
            },
            {
                "original_line_id": "credit",
                "cfdi_report_id": "cfdi",
                "cuenta_contable_id": "cxc",
                "debe": 0,
                "haber": changes.get("credit", 100),
            },
        ],
    )
    session = AsyncMock()
    session.execute.return_value = Result(rows)
    with pytest.raises(ValueError, match=expected):
        await prepare_collection_reversal(session, match)
    session.commit.assert_not_awaited()
    for args in session.execute.await_args_list:
        assert "INSERT" not in str(args.args[0])
        assert "UPDATE" not in str(args.args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,reason,original,expected",
    [
        ("", "reason", {}, "reversal_actor_required"),
        ("actor", "", {}, "reversal_reason_required"),
        ("actor", "reason", {}, "collection_match_not_found"),
        (
            "actor",
            "reason",
            {"status": "match_reversed"},
            "collection_match_not_active",
        ),
        (
            "actor",
            "reason",
            {"status": "accepted_collection_match"},
            "collection_posting_missing",
        ),
    ],
)
async def test_reversal_rejects_incomplete_or_replayed_request(
    actor, reason, original, expected
):
    session = AsyncMock()
    with patch(
        "samchat.ar.collection_matches._load_match",
        new=AsyncMock(return_value=original),
    ):
        with pytest.raises(ARCollectionMatchError, match=expected):
            await reverse_ar_collection_match(
                session,
                match_id="match",
                actor_empleado_id=actor,
                reversal_reason=reason,
            )
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
