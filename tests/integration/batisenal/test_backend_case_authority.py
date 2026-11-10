"""Real owners with synthetic grant rows; these fixtures are not transport E2E."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from backend_harness import IDS
from backend_native_fixtures import app as app_fixture
from backend_native_fixtures import graph as graph_fixture
from sqlalchemy import insert, text

from devnous.gastos.models import AccessControlRule
from devnous.gastos.services import batisenal_pilot_service as pilot
from devnous.gastos.services.batisenal_case_authority import current_case_authority
from devnous.gastos.services.batisenal_service import BatisenalError
from samchat.private_plugin.contracts import Identity
from samchat.private_plugin.oauth_store import GRANTS, LINKS

app, graph = app_fixture, graph_fixture
ISSUER = "https://samchat.example.test/oauth"
AUDIENCE = "https://samchat.example.test/mcp"
CLIENT = "synthetic-client"


@pytest.fixture
async def authorized(graph, request):
    with graph.engine.begin() as c:
        now = int(
            c.execute(text("SELECT extract(epoch FROM clock_timestamp())")).scalar_one()
        )
        remaining = getattr(request, "param", 3600)
        window_start, window_end = now + remaining - 3600, now + remaining
        c.execute(
            text("UPDATE access_profiles SET permissions=:p WHERE id='alice'"),
            {
                "p": json.dumps(
                    ["batisenal:acme:demo:request", "batisenal:acme:demo:agent"]
                )
            },
        )
        c.execute(
            insert(LINKS).values(
                link_id="l" * 32,
                issuer=ISSUER,
                subject="s" * 32,
                installation_id="synthetic-installation",
                employee_id=str(IDS["alice"]),
                organization_id="acme",
                profile_id="p" * 32,
                active=True,
            )
        )
        c.execute(
            insert(GRANTS).values(
                grant_id="g" * 43,
                issuer=ISSUER,
                subject="s" * 32,
                token_id="t" * 43,
                client_id=CLIENT,
                installation_id="synthetic-installation",
                link_id="l" * 32,
                scopes_json=["batisenal.support"],
                expires_at=now + 3600,
                active=True,
                revoked=False,
                employee_id=str(IDS["alice"]),
                organization_id="acme",
                profile_id="p" * 32,
            )
        )
        c.execute(
            insert(pilot.fixtures).values(
                ticket_id=graph.case_id,
                attestation_sha256="0" * 64,
                active=True,
                starts_at=window_start,
                expires_at=window_end,
            )
        )
        c.execute(
            insert(pilot.pilots).values(
                id="pilot-synthetic-owner",
                ticket_id=graph.case_id,
                employee=str(IDS["alice"]),
                profile_id="p" * 32,
                issuer=ISSUER,
                installation_id="synthetic-installation",
                organization="acme",
                project="demo",
                agent_id=str(IDS["alice"]),
                source_kind=graph.agent_kind,
                starts_at=window_start,
                expires_at=window_end,
                revoked=False,
            )
        )
    identity = Identity(
        str(IDS["alice"]),
        "synthetic-installation",
        "acme",
        "g" * 43,
        now + 3600,
        ISSUER,
        AUDIENCE,
        frozenset({"batisenal.support"}),
        True,
        False,
        "p" * 32,
    )
    return SimpleNamespace(graph=graph, identity=identity)


async def authorize(value, identity=None, **kwargs):
    async with value.graph.app.state.maker() as session:
        result = await current_case_authority(
            session,
            identity or value.identity,
            case_ref=kwargs.get("case_ref", value.graph.case_id),
            issuer=ISSUER,
            audience=AUDIENCE,
            client_id=kwargs.get("client_id", CLIENT),
        )
        assert result.actor.rol == "empleado"
        assert result.report.id == value.graph.report
        return result


async def test_ordinary_owner_authorized_by_business_ownership(authorized):
    value = await authorize(authorized)
    assert value.binding["organization_namespace"] == "acme"
    assert value.binding["tournament_id"] == authorized.graph.project
    assert value.grant["client_id"] == CLIENT
    assert value.observed_at.timestamp() < value.expires_at


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", str(IDS["bob"])),
        ("organization_id", "other"),
        ("installation_id", "another-installation"),
        ("profile_id", "other-profile"),
        ("grant_id", "another-grant"),
        ("issuer", "https://other.example.test"),
        ("audience", "https://other.example.test"),
        ("active", False),
        ("revoked", True),
    ],
)
async def test_cross_binding_denied(authorized, field, value):
    with pytest.raises(BatisenalError, match="CASE_AUTHORITY_UNAVAILABLE"):
        await authorize(authorized, replace(authorized.identity, **{field: value}))


@pytest.mark.parametrize(
    "kwargs", [{"case_ref": "another-case"}, {"client_id": "another-client"}]
)
async def test_case_and_client_not_authority_from_model(authorized, kwargs):
    with pytest.raises(BatisenalError, match="CASE_AUTHORITY_UNAVAILABLE"):
        await authorize(authorized, **kwargs)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE empleados SET activo=false WHERE nombre='Synthetic alice'",
        "UPDATE access_profiles SET active=false WHERE id='alice'",
        "UPDATE private_oauth_grants SET revoked=true",
        "UPDATE private_oauth_links SET active=false",
        "UPDATE support_batisenal_pilots SET revoked=true",
        "UPDATE support_batisenal_pilot_fixtures SET active=false",
        "UPDATE tournaments SET active=false",
        "UPDATE documentos SET tipo='SOLICITUD'",
        "UPDATE support_batisenal_pilot_fixtures SET attestation_sha256=repeat('f',64)",
        (
            "UPDATE documentos SET empleado_id=(SELECT id FROM empleados WHERE"
            " nombre='Synthetic bob')"
        ),
    ],
)
async def test_current_revocation_and_business_changes_deny(authorized, statement):
    await authorize(authorized)
    with authorized.graph.engine.begin() as c:
        c.execute(text(statement))
    with pytest.raises(BatisenalError):
        await authorize(authorized)


async def test_absolute_expiry_and_explicit_finance_denial(authorized):
    with pytest.raises(BatisenalError, match="CASE_GRANT_EXPIRED"):
        await authorize(authorized, replace(authorized.identity, expires_at=1))
    with authorized.graph.engine.begin() as c:
        c.execute(
            insert(AccessControlRule).values(
                tool_key="gastos.informes",
                action_key="ver",
                role_key="empleado",
                area_key="",
                allowed=False,
            )
        )
    with pytest.raises(BatisenalError):
        await authorize(authorized)
