"""Local PG/JWT/owners with synthetic identity; not real Chatty acceptance."""

import asyncio
import json
from pathlib import Path

import pytest
from backend_http_helpers import Form, confirm, result, rpc, token
from backend_modern_wire import mcp_request
from backend_native_fixtures import app as base_app_fixture
from backend_native_fixtures import bind_graph
from backend_oauth_scenario import scenario as scenario_fixture
from backend_web_helpers import login
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_backend_case_operations import proposal, request

from devnous.gastos.routes.batisenal_pilot_routes import PREFIX, mount_pilot
from devnous.gastos.routes.dependencies import get_current_empleado
from devnous.gastos.services.batisenal_case_service import CaseService
from devnous.gastos.services.batisenal_service import BatisenalError
from samchat.batisenal_pilot import SupportPilot
from samchat.private_plugin.oauth_flow import JWTSigner
from samchat.private_plugin.oauth_store import OAuthStore

base_app, scenario = base_app_fixture, scenario_fixture


@pytest.fixture
async def app(base_app):
    base_app.state.pilot_http_origin = "https://test"
    return base_app


@pytest.fixture
async def mounted(scenario):
    s = scenario
    graph = await bind_graph(s.app, s.case_id, prerequisites=False)
    graph.protocol.install(s.app.state.engine.sync_engine, synchronous=False)
    graph.protocol.install(s.engine, synchronous=True)
    try:
        with s.engine.begin() as c:
            s.clock[0] = int(
                c.execute(
                    text("SELECT extract(epoch FROM clock_timestamp())")
                ).scalar_one()
            )
            c.execute(
                text("UPDATE access_profiles SET permissions=:p WHERE id='alice'"),
                {
                    "p": json.dumps(
                        ["batisenal:acme:demo:request", "batisenal:acme:demo:agent"]
                    )
                },
            )
        async with s.app.state.engine.begin() as c:
            raw = await c.get_raw_connection()
            await raw.driver_connection.execute(
                Path("database/migrations/20261025_batisenal_v01_case_storage.sql")
                .read_text()
                .replace("BEGIN;", "")
                .replace("COMMIT;", "")
            )
        readonly = OAuthStore(
            graph.raw.execution_options(postgresql_readonly=True),
            issuer=s.config.issuer,
            installation_id="synthetic-installation",
            organization_id="acme",
            support_only=True,
        )
        prior = s.composition
        s.composition = SupportPilot(
            sessions=s.app.state.maker,
            employee_dependency=get_current_empleado,
            authorization=s.config,
            resource=prior.resource,
            oauth_store=prior.oauth_store,
            identity_records_store=readonly,
            signer=JWTSigner("synthetic-key", s.key),
            audit=s.audit,
            pilot=prior.pilot,
            clock=lambda: s.clock[0],
            enabled=True,
            case_service=CaseService(
                sessions=s.app.state.maker,
                issuer=s.config.issuer,
                audience=s.config.resource,
                client_id=s.config.client_id,
                enabled=True,
            ),
        )
        mount_pilot(s.app, s.composition)
        async with AsyncClient(
            transport=ASGITransport(app=s.app), base_url="https://test"
        ) as web:
            s.http = web
            await login(web, "alice")
            await web.get("/fixture/browser")
            web.headers["Origin"] = "https://test"
            yield s
    finally:
        graph.raw.dispose()
        graph.engine.dispose()


async def ready(s):
    _, redirect = await confirm(s)
    return await token(s, redirect)


async def test_context_submit_retry_receipt_render_and_no_business_write(mounted):
    s = mounted
    bearer = await ready(s)
    with s.engine.begin() as c:
        before = c.execute(text("SELECT to_jsonb(d) FROM documentos d")).scalar_one()
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    args = proposal(snapshot)
    saved = result(await rpc(s, bearer, "submit_case_draft", args))
    assert result(await rpc(s, bearer, "submit_case_draft", args)) == saved
    for selector in (
        {"idempotency_key": args["idempotency_key"]},
        {"operation_id": saved["receipt"]["operation_id"]},
    ):
        assert (
            result(
                await rpc(
                    s, bearer, "get_case_operation", request(s.case_id, **selector)
                )
            )
            == saved
        )
    page = await s.http.get(PREFIX + "/case")
    assert page.status_code == 200, page.text
    assert args["proposal"]["summary"] in page.text
    assert "Borrador privado guardado" in page.text
    assert "Sólo tu cuenta puede revisar" in page.text
    assert "No publicado" in page.text and "Publicar" not in page.text
    assert "Stub sintético · no es Chatty" in page.text
    with s.engine.begin() as c:
        assert (
            c.execute(text("SELECT to_jsonb(d) FROM documentos d")).scalar_one()
            == before
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(text("SELECT count(*) FROM support_ticket_comments")).scalar_one()
            == 0
        )
    for who in ("bob", "other_org", "support"):
        await login(s.http, who)
        await s.http.get("/fixture/browser")
        page = await s.http.get(PREFIX + "/case")
        assert page.status_code in (403, 404)
        assert args["proposal"]["summary"] not in page.text


async def test_concurrent_retry_policy_stale_and_revoke(mounted):
    s = mounted
    bearer = await ready(s)
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    args = proposal(snapshot)
    responses = await asyncio.gather(
        *(rpc(s, bearer, "submit_case_draft", args) for _ in range(3))
    )
    receipts = [result(r) for r in responses]
    assert receipts[0] == receipts[1] == receipts[2]
    with s.engine.begin() as c:
        c.execute(
            text("UPDATE access_profiles SET permissions=permissions WHERE id='alice'")
        )
    stale = result(
        await rpc(
            s,
            bearer,
            "get_case_operation",
            request(s.case_id, idempotency_key=args["idempotency_key"]),
        )
    )
    assert stale["usability"] == "stale" and stale["receipt"] == receipts[0]["receipt"]
    args["idempotency_key"] = "another-key"
    assert (await rpc(s, bearer, "submit_case_draft", args)).json()["error"][
        "message"
    ] == "STALE_CONTEXT"
    page = await s.http.get(PREFIX + "/case")
    assert "El contexto cambió" in page.text
    r = await s.http.post(PREFIX + "/revoke", data=Form(page.text).values)
    assert r.status_code == 303
    assert (
        "error" in (await rpc(s, bearer, "get_case_context", request(s.case_id))).json()
    )
    page = await s.http.get(PREFIX + "/case")
    assert page.status_code == 200 and "Propuesta privada pendiente" not in page.text


async def test_invalid_inputs_cross_case_csrf_and_catalog(mounted):
    s = mounted
    from backend_http_helpers import begin

    page = await begin(s)
    fields = Form(page.text).values
    fields["csrf"] = "wrong"
    assert (await s.http.post(PREFIX + "/consent", data=fields)).status_code == 403
    bearer = await ready(s)
    for args in (
        request(s.other_case),
        request(s.case_id, actor_id="server-owner"),
        [],
    ):
        assert "error" in (await rpc(s, bearer, "get_case_context", args)).json()
    async with AsyncClient(
        transport=ASGITransport(app=s.app), base_url="https://test"
    ) as c:
        wire_headers, message = mcp_request("tools/list")
        r = await c.post(
            PREFIX + "/mcp",
            headers={**wire_headers, "Authorization": "Bearer " + bearer},
            json=message,
        )
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert names == {
            "samchat_batisenal_" + x
            for x in (
                "get_case_context",
                "submit_case_draft",
                "get_case_operation",
            )
        }
        assert (await c.post(PREFIX + "/mcp", json={})).status_code == 401
    assert "error" in (await rpc(s, bearer, "submit_draft", {})).json()
    assert "error" in (await rpc(s, bearer, "profile", {})).json()


async def test_long_context_xss_and_uncertain_write_recovery(mounted, monkeypatch):
    s = mounted
    bearer = await ready(s)
    with s.engine.begin() as c:
        c.execute(
            text("UPDATE support_tickets SET descripcion=:value WHERE id=:id"),
            {"value": "Contexto sintético extenso. " * 150, "id": s.case_id},
        )
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    assert len(snapshot["sources"][1]["excerpt"]) <= 2000
    assert any("2000" in x for x in snapshot["omissions"])
    args = proposal(snapshot)
    args["proposal"][
        "summary"
    ] = '<script>alert("synthetic")</script> Revisión pendiente.'
    original = s.composition.bridge.audit_attempt
    lost = [False]

    def fail_once(actor, action, outcome):
        from devnous.gastos.services.batisenal_service import BatisenalError

        if (
            action.endswith("submit_case_draft")
            and outcome == "operation_complete"
            and not lost[0]
        ):
            lost[0] = True
            raise BatisenalError("AUDIT_UNAVAILABLE", 503)
        return original(actor, action, outcome)

    monkeypatch.setattr(s.composition.bridge, "audit_attempt", fail_once)
    uncertain = await rpc(s, bearer, "submit_case_draft", args)
    assert uncertain.json()["error"]["message"] == "AUDIT_UNAVAILABLE"
    recovered = result(
        await rpc(
            s,
            bearer,
            "get_case_operation",
            request(s.case_id, idempotency_key=args["idempotency_key"]),
        )
    )
    assert recovered["status"] == "stored"
    assert result(await rpc(s, bearer, "submit_case_draft", args)) == recovered
    page = await s.http.get(PREFIX + "/case")
    assert page.status_code == 200 and "<script>" not in page.text
    assert "Revisión pendiente" in page.text
    args["proposal"]["summary"] = "Different submission"
    assert (await rpc(s, bearer, "submit_case_draft", args)).json()["error"][
        "message"
    ] == "IDEMPOTENCY_CONFLICT"


async def test_impersonation_revoked_identity_and_disabled_service(mounted):
    from fastapi import Request

    s = mounted
    bearer = await ready(s)

    @s.app.get("/fixture/impersonate-v01")
    async def impersonate(request: Request):
        request.session["impersonator_empleado_id"] = "synthetic-only"
        return {}

    await s.http.get("/fixture/impersonate-v01")
    assert (await s.http.get(PREFIX + "/case")).status_code == 401
    await login(s.http, "alice")
    await s.http.get("/fixture/browser")
    s.composition.case_service.enabled = False
    r = await rpc(s, bearer, "get_case_context", request(s.case_id))
    assert (
        r.status_code == 503 and r.json()["error"]["message"] == "ACTIVATION_REQUIRED"
    )
    s.composition.case_service.enabled = True
    with s.engine.begin() as c:
        c.execute(text("UPDATE private_oauth_grants SET revoked=true"))
    r = await rpc(s, bearer, "get_case_context", request(s.case_id))
    assert r.status_code == 401
    page = await s.http.get(PREFIX + "/case")
    assert page.status_code == 200 and "Borrador privado" not in page.text


async def test_absolute_expiry_denies_read_write_and_private_render(mounted):
    s = mounted
    bearer = await ready(s)
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    with s.engine.begin() as c:
        c.execute(text("UPDATE private_oauth_grants SET expires_at=1"))
    for name, args in (
        ("get_case_context", request(s.case_id)),
        ("submit_case_draft", proposal(snapshot)),
        ("get_case_operation", request(s.case_id, idempotency_key="one-proposal")),
    ):
        assert "error" in (await rpc(s, bearer, name, args)).json()
    page = await s.http.get(PREFIX + "/case")
    assert page.status_code == 200 and "Borrador privado" not in page.text


async def test_utf8_payload_and_latest_private_draft_visible(mounted):
    s = mounted
    bearer = await ready(s)
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    args = proposal(snapshot)
    args["proposal"]["summary"] = "á" * 1900
    args["proposal"]["recommendations"] = ["é" * 1900]
    args["proposal"]["uncertainties"] = ["í" * 1900]
    assert len(json.dumps(args, ensure_ascii=False).encode()) < 16384
    assert len(json.dumps(args, ensure_ascii=True).encode()) > 16384
    assert result(await rpc(s, bearer, "submit_case_draft", args))["status"] == "stored"
    for index in range(20):
        args = proposal(snapshot, key=f"proposal-{index}")
        args["proposal"]["summary"] = f"Propuesta sintética número {index:02d}."
        assert (
            result(await rpc(s, bearer, "submit_case_draft", args))["status"]
            == "stored"
        )
    page = await s.http.get(PREFIX + "/case")
    assert "Propuesta sintética número 19." in page.text
    assert "á" * 1900 not in page.text
    assert page.text.count("<h2>Borrador privado guardado</h2>") == 20
    with s.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 21
        )


async def test_ambiguous_connection_hides_private_draft_without_losing_history(mounted):
    """Never guess between active grants; restore access only when unambiguous."""
    from sqlalchemy import insert, select, update

    from samchat.private_plugin.oauth_store import GRANTS

    s = mounted
    bearer = await ready(s)
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    args = proposal(snapshot, key="ambiguity-private-history")
    saved = result(await rpc(s, bearer, "submit_case_draft", args))
    before = await s.http.get(PREFIX + "/case")
    assert before.status_code == 200 and args["proposal"]["summary"] in before.text
    # Fixture-only second connection; no client registration or live credential.
    duplicate_id = "synthetic-ambiguous-grant"
    with s.engine.begin() as c:
        original = dict(c.execute(select(GRANTS)).mappings().one())
        duplicate = dict(
            original, grant_id=duplicate_id, token_id="synthetic-unissued-token"
        )
        c.execute(insert(GRANTS).values(**duplicate))
    ambiguous = await s.http.get(PREFIX + "/case")
    assert ambiguous.status_code == 403
    assert args["proposal"]["summary"] not in ambiguous.text
    assert saved["receipt"]["draft_id"] not in ambiguous.text
    # The original verified MCP connection remains valid; only browser selection
    # is ambiguous, so the page must not choose a grant by timestamp or row order.
    recovered = result(
        await rpc(
            s,
            bearer,
            "get_case_operation",
            request(s.case_id, idempotency_key=args["idempotency_key"]),
        )
    )
    assert recovered["receipt"] == saved["receipt"]
    with s.engine.begin() as c:
        c.execute(
            update(GRANTS).where(GRANTS.c.grant_id == duplicate_id).values(revoked=True)
        )
    restored = await s.http.get(PREFIX + "/case")
    assert restored.status_code == 200
    assert args["proposal"]["summary"] in restored.text
    with s.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_agent_drafts")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(text("SELECT count(*) FROM support_ticket_comments")).scalar_one()
            == 0
        )


async def test_revoke_during_postcommit_transport_audit_suppresses_receipt(
    mounted, monkeypatch
):
    s = mounted
    bearer = await ready(s)
    snapshot = result(await rpc(s, bearer, "get_case_context", request(s.case_id)))
    args = proposal(snapshot)
    append = s.audit.append
    revoked = False

    def revoke_after_committed_audit(**entry):
        nonlocal revoked
        receipt = append(**entry)
        if (
            entry["action"] == "samchat_batisenal_submit_case_draft"
            and entry["outcome"] == "operation_complete"
        ):
            with s.engine.begin() as c:
                c.execute(text("UPDATE private_oauth_grants SET revoked=true"))
            revoked = True
        return receipt

    monkeypatch.setattr(s.audit, "append", revoke_after_committed_audit)
    response = await rpc(s, bearer, "submit_case_draft", args)
    assert revoked and "error" in response.json()
    assert (
        "receipt" not in response.text
        and args["proposal"]["summary"] not in response.text
    )
    with s.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 1
        )
    assert (
        "error"
        in (
            await rpc(
                s,
                bearer,
                "get_case_operation",
                request(s.case_id, idempotency_key=args["idempotency_key"]),
            )
        ).json()
    )


async def test_case_dispatcher_rejects_unsupported_commands_before_identity(mounted):
    s = mounted
    bridge = s.composition.bridge
    for message in [
        {"id": 1, "method": "arbitrary-command"},
        {"id": 2, "method": "tools/call", "params": {"name": 42, "arguments": {}}},
        {"id": 3, "method": "tools/call", "params": {"name": "shell", "arguments": {}}},
        {"id": 4, "method": "tools/list", "params": {"actor": "root"}},
    ]:
        result = await bridge.handle(message)
        assert "error" in result and "result" not in result
    with pytest.raises(BatisenalError, match="UNAUTHENTICATED"):
        bridge.identity()
    bridge.enabled = False
    async with s.app.state.maker() as session:
        with pytest.raises(BatisenalError, match="ACTIVATION_REQUIRED"):
            await bridge.authorize(session)


async def test_composition_requires_case_service_and_matching_authority(mounted):
    from dataclasses import replace

    from samchat.batisenal_pilot import SupportAuthorizationConfig

    s = mounted
    pilot = s.composition
    arguments = dict(
        sessions=pilot.sessions,
        employee_dependency=pilot.employee_dependency,
        authorization=s.config,
        resource=pilot.resource,
        oauth_store=pilot.oauth_store,
        identity_records_store=pilot.records.store,
        signer=JWTSigner("synthetic-key", s.key),
        audit=s.audit,
        pilot=pilot.pilot,
        clock=lambda: s.clock[0],
        enabled=True,
        case_service=pilot.case_service,
    )
    with pytest.raises(ValueError, match="CASE_COMPOSITION_REQUIRED"):
        SupportPilot(**dict(arguments, case_service=None))
    with pytest.raises(ValueError, match="CASE_COMPOSITION_INVALID"):
        SupportPilot(**dict(arguments, sessions=object()))
    with pytest.raises(ValueError, match="SUPPORT_COMPOSITION_INVALID"):
        SupportPilot(**dict(arguments, authorization=object()))
    with pytest.raises(ValueError, match="SUPPORT_SCOPE_REQUIRED"):
        replace(s.config, scopes=frozenset({"batisenal:read"}))
    assert isinstance(s.config, SupportAuthorizationConfig)
    pilot.enabled = False
    from samchat.private_plugin.contracts import Denied

    with pytest.raises(Denied, match="ACTIVATION_REQUIRED"):
        await pilot.exchange({})


async def test_bridge_rejects_connection_switched_during_authorization(
    mounted, monkeypatch
):
    from dataclasses import replace

    from samchat.private_plugin.contracts import Identity

    s = mounted
    bearer = await ready(s)
    # Resolve through the actual JWT verifier first; only the subsequent race is
    # injected to prove the connection cannot be exchanged during authorization.
    from samchat.batisenal_pilot import _bearer

    monkeypatch.setattr(
        s.composition.bridge, "current_connection", lambda: "support-request"
    )
    token_context = _bearer.set(bearer)
    try:
        first = s.composition.bridge.identity()
        assert isinstance(first, Identity)
        identities = iter([first, replace(first, profile_id="different-profile")])
        monkeypatch.setattr(s.composition.bridge, "identity", lambda: next(identities))
        async with s.app.state.maker() as session:
            with pytest.raises(BatisenalError, match="AUTHORITY_CHANGED"):
                await s.composition.bridge.authorize(session)
    finally:
        _bearer.reset(token_context)
