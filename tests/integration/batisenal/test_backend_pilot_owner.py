"""Explicit owner consent lifecycle on synthetic PostgreSQL, no browser claim."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend_harness import IDS
from backend_native_fixtures import app as app_fixture
from backend_web_helpers import payload
from sqlalchemy import insert, select, text

from devnous.gastos.services import batisenal_pilot_service as pilots
from devnous.gastos.services import batisenal_service as owner

app = app_fixture


@pytest.fixture
async def owned(app):
    principal = owner.Principal(IDS["alice"], frozenset({("acme", "demo", "request")}))
    async with app.state.maker() as s:
        case = await owner.create_case(s, principal, payload(), owner.Policy())
    async with app.state.engine.begin() as c:
        raw = await c.get_raw_connection()
        await raw.driver_connection.execute(
            Path("database/migrations/20261024_batisenal_pilot.sql")
            .read_text()
            .replace("BEGIN;", "")
            .replace("COMMIT;", "")
        )
        await c.execute(
            insert(pilots.fixtures).values(
                ticket_id=case, attestation_sha256="0" * 64, active=True
            )
        )
    clock = [1900000000]
    pilot = pilots.Pilot(
        case_id=case,
        organization="acme",
        project="demo",
        issuer="https://pilot.example/oauth",
        installation_id="synthetic-installation",
        clock=lambda: clock[0],
    )
    link = SimpleNamespace(
        employee_id=str(IDS["alice"]),
        active=True,
        issuer=pilot.issuer,
        installation_id=pilot.installation_id,
        organization_id="acme",
        profile_id="synthetic-profile",
    )
    identity = SimpleNamespace(
        actor_id=link.employee_id,
        profile_id=link.profile_id,
        issuer=pilot.issuer,
        installation_id=pilot.installation_id,
        organization_id="acme",
    )

    async def authorize():
        return principal

    return SimpleNamespace(
        app=app,
        principal=principal,
        pilot=pilot,
        link=link,
        identity=identity,
        clock=clock,
        authorize=authorize,
    )


async def test_enroll_resolve_repeat_revoke_never_extends_window(owned):
    x = owned
    async with x.app.state.maker() as s:
        first = await x.pilot.enroll(s, x.principal, x.link, reauthorize=x.authorize)
        x.clock[0] += 20
        same = await x.pilot.enroll(s, x.principal, x.link, reauthorize=x.authorize)
        assert first["expires_at"] == same.expires_at == 1900003600
        assert (await x.pilot.resolve(s, x.identity, lock=True)).expires_at == first[
            "expires_at"
        ]
        assert await x.pilot.revoke(s, x.link.employee_id) == {"revoked": True}
        assert await x.pilot.revoke(s, x.link.employee_id) == {"revoked": True}
        with pytest.raises(owner.BatisenalError, match="PILOT_EXPIRED_OR_REVOKED"):
            await x.pilot.enroll(s, x.principal, x.link, reauthorize=x.authorize)
        assert await pilots.private_review_only(s, x.pilot.case_id)
        assert not await pilots.private_review_only(s, str(uuid4()))
        assert (
            await s.scalar(select(text("count(*)")).select_from(pilots.receipts)) == 2
        )


async def test_enroll_denies_bad_link_scope_expiry_and_missing_fixture(owned):
    x = owned
    async with x.app.state.maker() as s:
        with pytest.raises(owner.BatisenalError, match="PILOT_IDENTITY_DENIED"):
            await x.pilot.enroll(s, x.principal, None, reauthorize=x.authorize)
        x.pilot.project = "another-project"
        with pytest.raises(owner.BatisenalError, match="PILOT_SCOPE_DENIED"):
            await x.pilot.enroll(s, x.principal, x.link, reauthorize=x.authorize)
        x.pilot.project = "demo"
        with pytest.raises(owner.BatisenalError, match="PILOT_NOT_AUTHORIZED"):
            await x.pilot.resolve(s, x.identity)
        await x.pilot.enroll(s, x.principal, x.link, reauthorize=x.authorize)
        x.clock[0] = 1900003600
        with pytest.raises(owner.BatisenalError, match="PILOT_EXPIRED_OR_REVOKED"):
            await x.pilot.resolve(s, x.identity)
        await s.execute(
            text("UPDATE support_batisenal_pilot_fixtures SET active=false")
        )
        with pytest.raises(owner.BatisenalError, match="SYNTHETIC_FIXTURE_REQUIRED"):
            await x.pilot.fixture(s)


def test_pilot_configuration_and_clock_fail_closed():
    args = dict(
        case_id=str(uuid4()),
        organization="acme",
        project="demo",
        issuer="https://pilot.example",
        installation_id="synthetic",
        clock=lambda: -1,
    )
    with pytest.raises(ValueError):
        pilots.Pilot(**args, source_kind="untrusted")
    pilot = pilots.Pilot(**args)
    with pytest.raises(owner.BatisenalError, match="PILOT_CLOCK_INVALID"):
        pilot.now()
