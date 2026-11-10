"""Integrated local owners/storage; synthetic identity fixture, not real Chatty."""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_backend_case_authority import AUDIENCE, CLIENT, ISSUER
from test_backend_case_authority import app as app_fixture
from test_backend_case_authority import authorized as authorized_fixture
from test_backend_case_authority import graph as graph_fixture

from devnous.gastos.services.batisenal_case_service import CaseService
from devnous.gastos.services.batisenal_service import BatisenalError

app, graph, authorized = app_fixture, graph_fixture, authorized_fixture


@pytest.fixture
async def service(authorized):
    async with authorized.graph.app.state.engine.begin() as c:
        raw = await c.get_raw_connection()
        sql = Path(
            "database/migrations/20261025_batisenal_v01_case_storage.sql"
        ).read_text()
        await raw.driver_connection.execute(
            sql.replace("BEGIN;", "").replace("COMMIT;", "")
        )
    return CaseService(
        sessions=authorized.graph.app.state.maker,
        issuer=ISSUER,
        audience=AUDIENCE,
        client_id=CLIENT,
        enabled=True,
        registration_sha256=(
            "a" * 64 if authorized.graph.agent_kind == "ai_agent" else None
        ),
    )


def request(case, **kwargs):
    return {"schema_version": "1", "case_ref": case, **kwargs}


async def invoke(service, auth, tool, body):
    return await service.call(
        tool, auth.identity, json.dumps(body, ensure_ascii=False).encode()
    )


async def context(service, auth):
    return await invoke(service, auth, "get_case_context", request(auth.graph.case_id))


def proposal(snapshot, key="one-proposal"):
    return request(
        snapshot["case_ref"],
        snapshot_id=snapshot["snapshot_id"],
        expected_snapshot_version=snapshot["snapshot_version"],
        idempotency_key=key,
        proposal={
            "summary": "Revisión sintética, sin diagnóstico causal.",
            "claims": [
                {
                    "text": "El estado registrado es borrador.",
                    "source_refs": [
                        {
                            "source_id": snapshot["sources"][0]["source_id"],
                            "version": snapshot["sources"][0]["version"],
                        }
                    ],
                }
            ],
            "recommendations": ["Conservar el borrador mientras se revisa."],
            "uncertainties": [
                "Falta evidencia para explicar el impedimento; revisión humana"
                " pendiente."
            ],
        },
    )


async def test_context_draft_receipt_recovery_and_no_business_write(
    service, authorized
):
    auth = authorized
    with auth.graph.engine.begin() as c:
        before = c.execute(text("SELECT to_jsonb(d) FROM documentos d")).scalar_one()
    snapshot = await context(service, auth)
    assert snapshot["report"] == {
        "status": "borrador",
        "currency": "MXN",
        "claimed_total": "1200",
    }
    assert snapshot["omissions"] and snapshot["complete_for_scope"] is False
    body = proposal(snapshot)
    stored = await invoke(service, auth, "submit_case_draft", body)
    assert stored["status"] == "stored" and stored["usability"] == "current"
    assert await invoke(service, auth, "submit_case_draft", body) == stored
    assert (
        await invoke(
            service,
            auth,
            "get_case_operation",
            request(auth.graph.case_id, idempotency_key="one-proposal"),
        )
        == stored
    )
    assert (
        await invoke(
            service,
            auth,
            "get_case_operation",
            request(auth.graph.case_id, operation_id=stored["receipt"]["operation_id"]),
        )
        == stored
    )
    with auth.graph.engine.begin() as c:
        assert (
            c.execute(text("SELECT to_jsonb(d) FROM documentos d")).scalar_one()
            == before
        )
        for table in (
            "support_batisenal_agent_drafts",
            "support_batisenal_v01_operations",
        ):
            assert c.execute(text("SELECT count(*) FROM " + table)).scalar_one() == 1
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM agent_action_receipts WHERE"
                    " action_id='submit_case_draft'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(text("SELECT count(*) FROM support_ticket_comments")).scalar_one()
            == 0
        )


async def test_concurrent_same_key_single_transaction_result(service, authorized):
    snapshot = await context(service, authorized)
    body = proposal(snapshot)
    results = await asyncio.gather(
        *(invoke(service, authorized, "submit_case_draft", body) for _ in range(3))
    )
    assert results[0] == results[1] == results[2]
    with authorized.graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 1
        )


async def test_stale_before_submit_and_dynamic_receipt_after_change(
    service, authorized
):
    snapshot = await context(service, authorized)
    body = proposal(snapshot)
    stored = await invoke(service, authorized, "submit_case_draft", body)
    with authorized.graph.engine.begin() as c:
        c.execute(text("UPDATE documentos SET estado='enviado'"))
    status = await invoke(
        service,
        authorized,
        "get_case_operation",
        request(authorized.graph.case_id, idempotency_key="one-proposal"),
    )
    assert status["receipt"] == stored["receipt"] and status["usability"] == "stale"
    assert (await invoke(service, authorized, "submit_case_draft", body))[
        "usability"
    ] == "stale"
    with pytest.raises(BatisenalError, match="STALE_CONTEXT"):
        await invoke(
            service, authorized, "submit_case_draft", proposal(snapshot, "another-key")
        )


async def test_idempotency_conflict_does_not_write(service, authorized):
    snapshot = await context(service, authorized)
    body = proposal(snapshot)
    await invoke(service, authorized, "submit_case_draft", body)
    body["proposal"]["summary"] = "Different payload"
    with pytest.raises(BatisenalError, match="IDEMPOTENCY_CONFLICT"):
        await invoke(service, authorized, "submit_case_draft", body)
    with authorized.graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_agent_drafts")
            ).scalar_one()
            == 1
        )


async def test_audit_failure_rolls_back_draft_operation(service, authorized):
    snapshot = await context(service, authorized)
    with authorized.graph.engine.begin() as c:
        c.exec_driver_sql(
            "CREATE FUNCTION fail_synthetic_audit() RETURNS trigger LANGUAGE plpgsql AS"
            " $$ BEGIN RAISE EXCEPTION 'SYNTHETIC_AUDIT_FAILURE'; END $$"
        )
        c.exec_driver_sql(
            "CREATE TRIGGER fail_synthetic_audit BEFORE INSERT ON agent_action_receipts"
            " FOR EACH STATEMENT EXECUTE FUNCTION fail_synthetic_audit()"
        )
    with pytest.raises(DBAPIError, match="SYNTHETIC_AUDIT_FAILURE"):
        await invoke(service, authorized, "submit_case_draft", proposal(snapshot))
    with authorized.graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_agent_drafts")
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 0
        )


async def test_immutable_snapshot_and_receipt(service, authorized):
    snapshot = await context(service, authorized)
    await invoke(service, authorized, "submit_case_draft", proposal(snapshot))
    for statement in (
        "DELETE FROM support_batisenal_v01_snapshots",
        "UPDATE support_batisenal_v01_operations SET receipt_json=receipt_json",
        "TRUNCATE support_batisenal_v01_operations",
    ):
        with pytest.raises(DBAPIError, match="V01_RECORD_IMMUTABLE"):
            with authorized.graph.engine.begin() as c:
                c.execute(text(statement))


async def test_revocation_blocks_receipt_and_new_reads(service, authorized):
    snapshot = await context(service, authorized)
    await invoke(service, authorized, "submit_case_draft", proposal(snapshot))
    with authorized.graph.engine.begin() as c:
        c.execute(text("UPDATE private_oauth_grants SET revoked=true"))
    for tool, body in (
        ("get_case_context", request(authorized.graph.case_id)),
        (
            "get_case_operation",
            request(authorized.graph.case_id, idempotency_key="one-proposal"),
        ),
        ("submit_case_draft", proposal(snapshot)),
    ):
        with pytest.raises(BatisenalError, match="CASE_AUTHORITY_UNAVAILABLE"):
            await invoke(service, authorized, tool, body)


async def test_private_view_tracks_receipt_staleness_and_revocation(
    service, authorized
):
    from types import SimpleNamespace

    from backend_harness import IDS

    employee = SimpleNamespace(id=IDS["alice"], activo=True)
    snapshot = await context(service, authorized)
    saved = await invoke(service, authorized, "submit_case_draft", proposal(snapshot))
    async with service.sessions() as session:
        view = await service.private_view(session, employee, authorized.graph.case_id)
    assert len(view) == 1
    assert view[0]["operation_id"] == saved["receipt"]["operation_id"]
    assert view[0]["stale"] is False
    assert view[0]["source_kind"] == "synthetic_stub"
    with authorized.graph.engine.begin() as c:
        c.execute(text("UPDATE documentos SET estado='en_revision'"))
    async with service.sessions() as session:
        view = await service.private_view(session, employee, authorized.graph.case_id)
    assert view[0]["stale"] is True
    with authorized.graph.engine.begin() as c:
        c.execute(text("UPDATE private_oauth_grants SET revoked=true"))
    async with service.sessions() as session:
        assert (
            await service.private_view(session, employee, authorized.graph.case_id)
            == []
        )


async def test_private_view_rejects_ambiguous_grants_and_invalid_snapshot(
    service, authorized
):
    from types import SimpleNamespace
    from uuid import uuid4

    from backend_harness import IDS
    from sqlalchemy import insert, select

    from samchat.private_plugin.oauth_store import GRANTS

    employee = SimpleNamespace(id=IDS["alice"], activo=True)
    async with service.sessions() as session:
        auth = await service.authority(
            session, authorized.identity, authorized.graph.case_id
        )
        for identifier in ("not-a-uuid", str(uuid4())):
            with pytest.raises(BatisenalError, match="SNAPSHOT_UNAVAILABLE"):
                await service.snapshot(session, auth, identifier)
        assert await service.lookup(session, auth, {"operation_id": "invalid"}) is None
        assert await service.operation(
            session, auth, {"operation_id": str(uuid4())}
        ) == {"status": "not_found"}
    with authorized.graph.engine.begin() as c:
        grant = dict(c.execute(select(GRANTS)).mappings().one())
        grant.update(grant_id="h" * 43, token_id="u" * 43)
        c.execute(insert(GRANTS).values(**grant))
    async with service.sessions() as session:
        with pytest.raises(BatisenalError, match="CASE_CONNECTION_AMBIGUOUS"):
            await service.private_view(session, employee, authorized.graph.case_id)


async def test_private_view_hides_revoked_pilot_and_actor_mismatch(service, authorized):
    from types import SimpleNamespace

    from backend_harness import IDS

    async with service.sessions() as session:
        with pytest.raises(BatisenalError, match="CASE_AUTHORITY_UNAVAILABLE"):
            await service.private_view(
                session,
                SimpleNamespace(id=IDS["bob"], activo=True),
                authorized.graph.case_id,
            )
    with authorized.graph.engine.begin() as c:
        c.execute(text("UPDATE support_batisenal_pilots SET revoked=true"))
    async with service.sessions() as session:
        assert (
            await service.private_view(
                session,
                SimpleNamespace(id=IDS["alice"], activo=True),
                authorized.graph.case_id,
            )
            == []
        )
