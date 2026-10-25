from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from samchat.ar.collection_integrity import (
    CollectionSourceConflict,
    guard_collection_channel,
)


def result(row):
    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))


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
