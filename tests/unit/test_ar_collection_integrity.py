from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from samchat.ar.collection_integrity import (
    CollectionSourceConflict,
    guard_collection_channel,
    load_collection_source_context,
    require_collection_period_open,
)


def result(row):
    return SimpleNamespace(
        mappings=lambda: SimpleNamespace(
            first=lambda: row, all=lambda: [] if row is None else [row]
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["match", "direct"])
async def test_unknown_source_cannot_post_on_either_channel(channel):
    session = SimpleNamespace(execute=AsyncMock(return_value=result(None)))
    with pytest.raises(CollectionSourceConflict, match="source_not_found"):
        await guard_collection_channel(
            session,
            cfdi_report_id="00000000-0000-0000-0000-000000000001",
            channel=channel,
        )
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_missing_source_and_unknown_channel_do_not_query():
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(CollectionSourceConflict, match="source_required"):
        await guard_collection_channel(session, cfdi_report_id="", channel="match")
    with pytest.raises(ValueError, match="Unknown collection channel"):
        await guard_collection_channel(session, cfdi_report_id="", channel="unknown")
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_cross_channel_evidence_fails_closed():
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[result({"id": "source"}), result(None)])
    )
    with pytest.raises(CollectionSourceConflict, match="sources_unavailable"):
        await guard_collection_channel(
            session, cfdi_report_id="source", channel="match"
        )
    assert all(
        "INSERT" not in str(call.args[0]) for call in session.execute.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("date", [None, "", "2026-02-30"])
async def test_invalid_collection_date_rejected_before_query(date):
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(CollectionSourceConflict, match="collection_date_required"):
        await require_collection_period_open(session, date)
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "open", " CLOSED "])
async def test_collection_uses_its_own_date_and_preserves_closed_period(status):
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=result(None if status is None else {"status": status})
        )
    )
    if status == " CLOSED ":
        with pytest.raises(CollectionSourceConflict, match="accounting_period_closed"):
            await require_collection_period_open(session, "2025-12-31T23:50:00-06:00")
    else:
        await require_collection_period_open(session, "2025-12-31T23:50:00-06:00")
    assert session.execute.await_args.args[1] == {"year": 2025, "month": 12}
    assert session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item,linked_row,rejected",
    [
        ({"ar_item_id": "candidate:source", "cfdi_report_id": "source"}, None, False),
        ({"ar_item_id": "candidate:other", "cfdi_report_id": "source"}, None, True),
        (
            {"ar_item_id": "candidate:source", "cfdi_report_id": "source"},
            {"id": "new-link"},
            True,
        ),
        (
            {
                "ar_item_id": "candidate:source",
                "cfdi_report_id": "source",
                "budget_line_id": "line",
            },
            None,
            True,
        ),
    ],
)
async def test_candidate_must_still_be_unlinked_to_accept_collection(
    item, linked_row, rejected
):
    session = SimpleNamespace(execute=AsyncMock(return_value=result(linked_row)))
    if rejected:
        with pytest.raises(CollectionSourceConflict, match="ar_source_context_changed"):
            await load_collection_source_context(session, item)
    else:
        assert await load_collection_source_context(session, item) == {
            "cfdi_report_id": "source",
            "source": "unlinked_candidate",
        }
    assert all(
        str(call.args[0]).lstrip().startswith("SELECT")
        for call in session.execute.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_key", [None, "budget_version_id", "budget_line_id", "cfdi_report_id"]
)
async def test_linked_source_revalidates_each_canonical_object_identifier(changed_key):
    item = {
        "ar_item_id": "linked:link",
        "budget_version_id": "version",
        "budget_line_id": "line",
        "cfdi_report_id": "source",
    }
    row = dict(
        item,
        id="link",
        status="approved",
        accounting_poliza_id="original",
        unlinked_at=None,
    )
    if changed_key:
        row[changed_key] = "different-object"
    session = SimpleNamespace(execute=AsyncMock(return_value=result(row)))
    if changed_key:
        with pytest.raises(CollectionSourceConflict, match="ar_source_context_changed"):
            await load_collection_source_context(session, item)
    else:
        assert await load_collection_source_context(session, item) == row
    assert session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel,has_matches,has_direct,rejected",
    [
        ("match", True, False, False),
        ("match", False, True, True),
        ("direct", True, False, True),
        ("direct", False, True, True),
    ],
)
async def test_partial_matches_can_continue_but_cross_channel_conflicts_preserve_evidence(
    channel, has_matches, has_direct, rejected
):
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                result({"id": "source"}),
                result({"has_matches": has_matches, "has_other_direct": has_direct}),
            ]
        )
    )
    if rejected:
        with pytest.raises(
            CollectionSourceConflict, match="collection_channel_conflict"
        ):
            await guard_collection_channel(
                session, cfdi_report_id="source", channel=channel
            )
    else:
        await guard_collection_channel(
            session, cfdi_report_id="source", channel=channel
        )
    assert session.execute.await_count == 2
    assert all(
        str(call.args[0]).lstrip().startswith("SELECT")
        for call in session.execute.await_args_list
    )
