"""Technical ai_agent binding, not evidence of a real Chatty conversation."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_backend_case_authority import app as app_fixture
from test_backend_case_authority import authorized as authorized_fixture
from test_backend_case_authority import graph as graph_fixture
from test_backend_case_operations import context, invoke, proposal
from test_backend_case_operations import service as service_fixture

from devnous.gastos.services.batisenal_service import BatisenalError

app, authorized, graph, service = (
    app_fixture,
    authorized_fixture,
    graph_fixture,
    service_fixture,
)


@pytest.mark.parametrize("graph", ["ai_agent"], indirect=True)
async def test_registered_provenance_is_persisted_without_public_receipt_changes(
    service, authorized
):
    snapshot = await context(service, authorized)
    result = await invoke(service, authorized, "submit_case_draft", proposal(snapshot))
    assert set(result["receipt"]) == {
        "operation_id",
        "draft_id",
        "committed_at",
        "snapshot_version",
    }
    with authorized.graph.engine.begin() as c:
        row = c.execute(
            text("SELECT source_kind,response_json FROM support_batisenal_agent_drafts")
        ).one()
        assert row.source_kind == "ai_agent"
        assert (
            json.loads(row.response_json)["provenance"]["agent_registration_sha256"]
            == "a" * 64
        )
    service.registration_sha256 = "b" * 64
    with pytest.raises(BatisenalError):
        await context(service, authorized)


@pytest.mark.parametrize("graph", ["ai_agent"], indirect=True)
async def test_binding_provenance_cannot_be_relabelled(authorized):
    with pytest.raises(DBAPIError, match="V01_BINDING_IMMUTABLE"):
        with authorized.graph.engine.begin() as c:
            c.execute(
                text(
                    "UPDATE support_batisenal_v01_case_binding SET"
                    " agent_registration_sha256=:hash"
                ),
                {"hash": "b" * 64},
            )


@pytest.mark.parametrize("revoked", [False, True])
async def test_attestation_blocks_requester_and_staff_publication_after_revoke(
    authorized, revoked
):
    from uuid import uuid4

    from backend_harness import IDS

    from devnous.gastos.services import batisenal_agent_service as drafts
    from devnous.gastos.services import batisenal_service as owner

    if revoked:
        with authorized.graph.engine.begin() as c:
            c.execute(text("UPDATE support_batisenal_pilots SET revoked=true"))
            c.execute(text("UPDATE support_batisenal_pilot_fixtures SET active=false"))
    principals = [
        owner.Principal(IDS["alice"], frozenset({("acme", "demo", "request")})),
        owner.Principal(IDS["support"], frozenset({("acme", "demo", "support")})),
    ]
    for principal in principals:
        async with authorized.graph.app.state.maker() as session:
            with pytest.raises(owner.BatisenalError, match="PILOT_PRIVATE_REVIEW_ONLY"):
                await owner.reply_case(
                    session,
                    principal,
                    authorized.graph.case_id,
                    dict(
                        confirmed=True,
                        request_key=str(uuid4()),
                        body="Must remain private",
                    ),
                )
            if principal.employee == IDS["support"]:

                async def reauthorize():
                    return principal

                with pytest.raises(
                    owner.BatisenalError, match="PILOT_PRIVATE_REVIEW_ONLY"
                ):
                    await drafts.publish_draft(
                        session,
                        principal,
                        authorized.graph.case_id,
                        str(uuid4()),
                        confirmed=True,
                        reauthorize=reauthorize,
                    )
    with authorized.graph.engine.begin() as c:
        assert (
            c.execute(text("SELECT count(*) FROM support_ticket_comments")).scalar_one()
            == 0
        )
