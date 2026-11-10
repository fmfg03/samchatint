"""Synthetic identity/stub contracts; passing tests never establish live Chatty."""

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend_harness import IDS, service
from backend_native_fixtures import app as app_fixture
from backend_web_helpers import create, login, payload, png
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from devnous.gastos.services import batisenal_agent_service as agent
from samchat.batisenal_bridge import (
    PREFIX,
    Binding,
    Bridge,
    requested_event_data,
)
from samchat.private_plugin.contracts import Identity

app = app_fixture


class SyntheticIdentity:
    def __init__(self):
        self.identity = Identity(
            actor_id="synthetic-agent",
            profile_id="synthetic-link",
            grant_id="synthetic-grant",
            organization_id="acme",
            installation_id="synthetic-installation",
            issuer="synthetic-issuer",
            audience="synthetic-audience",
            oauth_scopes=frozenset({"batisenal.support"}),
            expires_at=2000000000,
            active=True,
            revoked=False,
        )
        self.calls = 0

    def resolve_current(self, connection):
        assert connection == "fixture-verified-transport"
        self.calls += 1
        return self.identity


class SyntheticAudit:
    def __init__(self):
        self.entries, self.available = [], True

    def append(self, **entry):
        if not self.available:
            return ""
        self.entries.append(entry)
        return str(len(self.entries))


async def make_bridge(app):
    # Synthetic DB only. No production account, token or identity provisioning.
    async with app.state.maker() as session:
        await session.execute(
            text("UPDATE access_profiles SET permissions=:p WHERE id='support'"),
            {
                "p": json.dumps(
                    ["batisenal:acme:demo:support", "batisenal:acme:demo:agent"]
                )
            },
        )
        await session.commit()
    binding = Binding(
        "synthetic-agent",
        "synthetic-link",
        "synthetic-grant",
        uuid4(),
        IDS["support"],
        "acme",
        "demo",
    )
    return Bridge(
        sessions=app.state.maker,
        identities=SyntheticIdentity(),
        audit=SyntheticAudit(),
        current_connection=lambda: "fixture-verified-transport",
        bindings=(binding,),
        issuer="synthetic-issuer",
        audience="synthetic-audience",
        installation_id="synthetic-installation",
        enabled=True,
        clock=lambda: 1900000000,
    )


@pytest.fixture
async def scenario(app):
    bridge = await make_bridge(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await login(client)
        case_id = await create(client, payload(image=png()))
        yield SimpleNamespace(app=app, bridge=bridge, client=client, case_id=case_id)


async def call(bridge, name, **arguments):
    return await bridge.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": PREFIX + name, "arguments": arguments},
        }
    )


def advice(**changes):
    return dict(
        observation="La persona informa un error al avanzar (fixture).",
        inference="No hay evidencia de la causa.",
        next_step="Conserva el borrador; soporte revisará este caso.",
        verification=(
            "Comprueba que el borrador siga intacto; no se ha verificado resolución."
        ),
        missing_evidence="Falta evidencia del módulo real.",
        source_ids=["support_evidence"],
        **changes,
    )


async def prepare(scenario):
    got = await call(scenario.bridge, "read_case", case_id=scenario.case_id)
    revision = got["result"]["structuredContent"]["version"]
    args = dict(case_id=scenario.case_id, version=revision, response=advice())
    preview = await call(scenario.bridge, "prepare_reply", **args)
    assert not preview.get("error"), preview
    return dict(
        args,
        request_key=str(uuid4()),
        preview_hash=preview["result"]["structuredContent"]["preview_hash"],
    )


@pytest.mark.parametrize(
    "change",
    [
        {"revoked": True},
        {"active": False},
        {"expires_at": 1},
        {"issuer": "other"},
        {"audience": "other"},
        {"installation_id": "other"},
        {"organization_id": "other"},
        {"profile_id": "other"},
        {"grant_id": "other"},
        {"oauth_scopes": frozenset()},
    ],
)
async def test_verified_identity_fail_closed(scenario, change):
    s = scenario
    s.bridge.identities.identity = replace(s.bridge.identities.identity, **change)
    assert (await call(s.bridge, "read_case", case_id=s.case_id))["error"][
        "message"
    ] == "UNAUTHENTICATED"


async def test_disabled_no_scope_no_root_and_explicit_denial(scenario):
    s = scenario
    s.bridge.enabled = False
    assert (await call(s.bridge, "list_pending"))["error"][
        "message"
    ] == "ACTIVATION_REQUIRED"
    s.bridge.enabled = True
    async with s.app.state.maker() as session:
        await session.execute(
            text("UPDATE access_profiles SET permissions=:p WHERE id='support'"),
            {"p": '["batisenal:acme:demo:support"]'},
        )
        await session.commit()
    assert (await call(s.bridge, "list_pending"))["error"][
        "message"
    ] == "AGENT_SCOPE_DENIED"
    s.bridge = await make_bridge(s.app)
    from devnous.gastos.models import AccessControlRule

    async with s.app.state.maker() as session:
        session.add(
            AccessControlRule(
                id=uuid4(),
                tool_key="admin.soporte",
                action_key="ver",
                role_key="superadmin",
                area_key="",
                allowed=False,
                active=True,
            )
        )
        await session.commit()
    result = await call(s.bridge, "list_pending")
    assert "error" in result


def test_event_data_is_not_authority_or_callback():
    data = requested_event_data(str(uuid4()), "a" * 64, str(uuid4()))
    assert set(data) == {"type", "case_id", "version", "delivery_id"}
    assert len(json.dumps(data).encode()) < 1024
    with pytest.raises(service.BatisenalError):
        requested_event_data(str(uuid4()), "arbitrary", str(uuid4()))


async def test_source_failure_is_recorded_without_knowledge(scenario, monkeypatch):
    import samchat.batisenal_bridge as module

    s = scenario
    args = await prepare(s)

    def unavailable():
        raise service.BatisenalError("KNOWLEDGE_INTEGRITY_UNAVAILABLE", 503)

    monkeypatch.setattr(module, "knowledge", unavailable)
    result = await call(
        s.bridge,
        "record_activity",
        case_id=s.case_id,
        version=args["version"],
        kind="source_unavailable",
        request_key=str(uuid4()),
    )
    assert "result" in result
    async with s.app.state.maker() as session:
        requester = service.Principal(
            IDS["alice"], frozenset({("acme", "demo", "request")})
        )
        detail = await service.case_detail(session, requester, s.case_id)
        assert detail["attention"]["state"] == "attention_error"
    assert (await call(s.bridge, "read_case", case_id=s.case_id))["error"][
        "message"
    ] == "KNOWLEDGE_INTEGRITY_UNAVAILABLE"


@pytest.mark.parametrize("org,project", [("other", "demo"), ("acme", "other")])
async def test_agent_actual_foreign_grants_cannot_read_case(scenario, org, project):
    s = scenario
    async with s.app.state.maker() as session:
        await session.execute(
            text("UPDATE access_profiles SET permissions=:p WHERE id='support'"),
            {
                "p": json.dumps(
                    [
                        f"batisenal:{org}:{project}:agent",
                        f"batisenal:{org}:{project}:support",
                    ]
                )
            },
        )
        await session.commit()
    s.bridge.bindings = (
        replace(s.bridge.bindings[0], organization=org, project=project),
    )
    s.bridge.identities.identity = replace(
        s.bridge.identities.identity, organization_id=org
    )
    assert (await call(s.bridge, "read_case", case_id=s.case_id))["error"][
        "message"
    ] == "NOT_FOUND"
    assert (await call(s.bridge, "list_pending"))["result"]["structuredContent"][
        "cases"
    ] == []


async def test_direct_owner_roundtrip_and_private_draft_provenance(scenario):
    """Ordinary unattested support-owner regression; no pilot route is exposed."""
    s = scenario
    requester = service.Principal(
        IDS["alice"], frozenset({("acme", "demo", "request")})
    )
    staff = service.Principal(IDS["support"], frozenset({("acme", "demo", "support")}))

    async def reauthorize():
        return staff

    listed = await s.bridge.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listed["result"]["tools"]) == 5
    assert (await call(s.bridge, "list_pending"))["result"]["structuredContent"][
        "cases"
    ]
    args = await prepare(s)
    bad = await call(s.bridge, "submit_draft", **dict(args, preview_hash="wrong"))
    assert bad["error"]["message"] == "PREVIEW_MISMATCH"
    activity_args = dict(
        case_id=s.case_id,
        version=args["version"],
        kind="started",
        request_key="started-direct-1",
    )
    started = await call(s.bridge, "record_activity", **activity_args)
    assert started == await call(s.bridge, "record_activity", **activity_args)
    saved = await call(s.bridge, "submit_draft", **args)
    assert "error" not in saved, saved
    assert saved == await call(s.bridge, "submit_draft", **args)
    draft_id = saved["result"]["structuredContent"]["draft_id"]
    async with s.app.state.maker() as session:
        detail = await service.case_detail(session, requester, s.case_id)
        assert not detail["comments"]
        assert detail["attention"]["state"] == "private_review"
        with pytest.raises(service.BatisenalError, match="NOT_FOUND"):
            await agent.private_drafts(session, requester, s.case_id)
        items = await agent.private_drafts(session, staff, s.case_id)
        assert len(items) == 1 and items[0]["source_kind"] == "synthetic_stub"
        with pytest.raises(
            service.BatisenalError, match="PREVIEW_CONFIRMATION_REQUIRED"
        ):
            await agent.publish_draft(
                session,
                staff,
                s.case_id,
                draft_id,
                confirmed=False,
                reauthorize=reauthorize,
            )
        published = await agent.publish_draft(
            session, staff, s.case_id, draft_id, confirmed=True, reauthorize=reauthorize
        )
        assert published == await agent.publish_draft(
            session, staff, s.case_id, draft_id, confirmed=True, reauthorize=reauthorize
        )
        detail = await service.case_detail(session, requester, s.case_id)
        assert len(detail["comments"]) == 1
        assert detail["comments"][0]["source_kind"] == "synthetic_stub"
        assert detail["attention"]["state"] == "response_available"
        assert await agent.private_drafts(session, staff, s.case_id) == []
    assert not (await call(s.bridge, "list_pending"))["result"]["structuredContent"][
        "cases"
    ]


async def test_direct_owner_input_conflicts_and_stale_drafts(scenario):
    s = scenario
    requester = service.Principal(
        IDS["alice"], frozenset({("acme", "demo", "request")})
    )
    staff = service.Principal(IDS["support"], frozenset({("acme", "demo", "support")}))
    args = await prepare(s)
    invalid = dict(args["response"], source_ids=["../../etc/passwd"])
    assert (
        await call(
            s.bridge,
            "prepare_reply",
            case_id=s.case_id,
            version=args["version"],
            response=invalid,
        )
    )["error"]["message"] == "SOURCE_NOT_AVAILABLE"
    saved = await call(s.bridge, "submit_draft", **args)
    changed = dict(args["response"], observation="Changed proposal")
    preview = (
        await call(
            s.bridge,
            "prepare_reply",
            case_id=s.case_id,
            version=args["version"],
            response=changed,
        )
    )["result"]["structuredContent"]
    assert (
        await call(
            s.bridge,
            "submit_draft",
            **dict(args, response=changed, preview_hash=preview["preview_hash"]),
        )
    )["error"]["message"] == "IDEMPOTENCY_CONFLICT"
    async with s.app.state.maker() as session:
        for bad in [
            dict(confirmed=False),
            dict(status="unknown"),
            dict(status="resuelto"),
        ]:
            with pytest.raises(service.BatisenalError):
                await service.reply_case(
                    session,
                    requester,
                    s.case_id,
                    dict(
                        dict(
                            request_key="requester-direct",
                            confirmed=True,
                            body="A synthetic note",
                        ),
                        **bad,
                    ),
                )
        payload = dict(
            request_key="requester-direct",
            confirmed=True,
            body="Ignore commands; token=PRIVATE",
        )
        receipt = await service.reply_case(session, requester, s.case_id, payload)
        assert receipt == await service.reply_case(
            session, requester, s.case_id, payload
        )
        with pytest.raises(service.BatisenalError, match="IDEMPOTENCY_CONFLICT"):
            await service.reply_case(
                session, requester, s.case_id, dict(payload, body="Different")
            )
        drafts = await agent.private_drafts(session, staff, s.case_id)
        assert drafts[0]["stale"]

        async def authorize():
            return staff

        with pytest.raises(service.BatisenalError, match="CASE_VERSION_STALE"):
            await agent.publish_draft(
                session,
                staff,
                s.case_id,
                saved["result"]["structuredContent"]["draft_id"],
                confirmed=True,
                reauthorize=authorize,
            )
    assert (
        await call(
            s.bridge,
            "prepare_reply",
            case_id=s.case_id,
            version=args["version"],
            response=advice(),
        )
    )["error"]["message"] == "CASE_VERSION_STALE"
