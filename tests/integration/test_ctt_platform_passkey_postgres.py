"""Protected platform metadata and zero-counter replay on ephemeral PostgreSQL."""

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_staff_review_state_postgres import state_pg as _state_fixture
from test_ctt_staff_review_state_postgres import SCOPE, clean, connect, synthetic_state

pytest_plugins = ("test_ctt_staff_review_state_postgres",)
state_pg = _state_fixture
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261030_ctt_platform_passkey.sql"
ROLLBACK = ROOT / "database/migrations/20261030_ctt_platform_passkey.rollback.sql"
PROFILE = "platform-passkey-v1"
POLICY = "sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3"
WRITER = "ctt_platform_synthetic_writer"
SIGNATURE = "uuid,uuid,bytea,text,bigint,bigint,text,boolean,boolean,text"


@pytest.fixture(scope="module")
def platform_pg(state_pg):
    async def setup():
        admin = await connect(state_pg)
        try:
            # Existing positive device evidence survives the migration unchanged.
            legacy = await synthetic_state(admin)
            original = await admin.fetchval(
                "SELECT pg_get_functiondef('ctt_staff_private."
                "consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint)'"
                "::regprocedure)"
            )
            await admin.fetchval(
                "SELECT ctt_staff_private.consume_review_challenge(" "$1,$2,$3,$4,0,1)",
                legacy[2],
                legacy[0],
                legacy[3],
                SCOPE,
            )
            await admin.execute(MIGRATION.read_text())
            assert (
                await admin.fetchval(
                    "SELECT pg_get_functiondef('ctt_staff_private."
                    "consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint)'"
                    "::regprocedure)"
                )
                == original
            )
            upgraded = await admin.fetchrow(
                "SELECT auth_profile,auth_policy_hash,backup_eligible,backup_state,"
                "new_counter FROM ctt_staff_private.review_challenge_consumptions"
            )
            assert dict(upgraded) == dict(
                auth_profile="device-counter-v1",
                auth_policy_hash=None,
                backup_eligible=False,
                backup_state=False,
                new_counter=1,
            )
            await clean(admin)
            await admin.execute(f"CREATE ROLE {WRITER} LOGIN")
            await admin.execute(f"GRANT USAGE ON SCHEMA ctt_staff_private TO {WRITER}")
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                f"ctt_staff_private.consume_review_challenge_v2({SIGNATURE}) "
                f"TO {WRITER}"
            )
        finally:
            await admin.close()

    asyncio.run(setup())
    yield state_pg

    async def teardown():
        admin = await connect(state_pg)
        try:
            await admin.execute(ROLLBACK.read_text())
            assert (
                await admin.fetchval(
                    "SELECT to_regprocedure('ctt_staff_private."
                    "consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint)')"
                )
                is not None
            )
        finally:
            await admin.close()

    asyncio.run(teardown())


async def setup_state(admin, prior=0, be=True, lifetime=timedelta(minutes=3)):
    principal, edition, old_challenge, credential = await synthetic_state(admin)
    await admin.execute(
        "UPDATE ctt_staff_private.review_credentials "
        "SET auth_profile=$1,auth_policy_hash=$2,backup_eligible=$3,"
        "authority_version=2,sign_count=$4 WHERE credential_id=$5",
        PROFILE,
        POLICY,
        be,
        prior,
        credential,
    )
    challenge = uuid4()
    await admin.execute(
        "INSERT INTO ctt_staff_private.review_challenges "
        "SELECT $1,principal_id,credential_id,tenant_id,edition_id,faculty,"
        "principal_authority_version,faculty_authority_version,2,scope_hash,"
        "public.gen_random_bytes(32),clock_timestamp(),clock_timestamp()+$2 "
        "FROM ctt_staff_private.review_challenges WHERE challenge_id=$3",
        challenge,
        lifetime,
        old_challenge,
    )
    return principal, edition, challenge, credential


async def consume(
    conn, state, prior=0, new=0, profile=PROFILE, be=True, bs=False, policy=POLICY
):
    principal, _, challenge, credential = state
    return await conn.fetchval(
        "SELECT ctt_staff_private.consume_review_challenge_v2("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        challenge,
        principal,
        credential,
        SCOPE,
        prior,
        new,
        profile,
        be,
        bs,
        policy,
    )


async def cleanup(admin):
    # Test-only removal; the migration never provides an evidence-delete API.
    await admin.execute(
        "ALTER TABLE ctt_staff_private.review_authenticator_audit "
        "DISABLE TRIGGER ctt_authenticator_audit_immutable"
    )
    await admin.execute("DELETE FROM ctt_staff_private.review_authenticator_audit")
    await admin.execute(
        "ALTER TABLE ctt_staff_private.review_authenticator_audit "
        "ENABLE TRIGGER ctt_authenticator_audit_immutable"
    )
    await clean(admin)


@pytest.mark.parametrize(
    "prior,new,accepted",
    [
        (0, 0, True),
        (0, 1, True),
        (1, 2, True),
        (1, 1, False),
        (1, 0, False),
        (3, 2, False),
        (0, -1, False),
        (0, 4294967296, False),
    ],
)
def test_profile_counter_policy(platform_pg, prior, new, accepted):
    async def run():
        admin = await connect(platform_pg)
        writer = await connect(platform_pg, WRITER)
        try:
            state = await setup_state(admin, prior)
            if accepted:
                async with writer.transaction():
                    assert await consume(writer, state, prior, new)
                row = await admin.fetchrow(
                    "SELECT * FROM ctt_staff_private.review_challenge_consumptions"
                )
                assert row["auth_profile"] == PROFILE
                assert row["auth_policy_hash"] == POLICY
                assert row["new_counter"] == new
            else:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await consume(writer, state, prior, new)
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private."
                        "review_challenge_consumptions"
                    )
                    == 0
                )
        finally:
            await writer.close()
            await cleanup(admin)
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "profile",
        "policy",
        "be",
        "bs",
        "revoked",
        "faculty_revoked",
        "principal_revoked",
        "stale_version",
        "legacy",
        "app",
        "rollback",
        "concurrent",
        "deferred_expiry",
        "bs_audit",
        "version_guard",
        "null_profile",
        "null_policy",
        "null_be",
        "null_bs",
        "deferred_profile_change",
    ],
)
def test_protected_metadata_replay_and_failure_paths(platform_pg, fault):
    async def run():
        admin = await connect(platform_pg)
        writer = await connect(platform_pg, WRITER)
        try:
            state = await setup_state(
                admin,
                be=fault != "bs",
                lifetime=(
                    timedelta(milliseconds=500)
                    if fault == "deferred_expiry"
                    else timedelta(minutes=3)
                ),
            )
            if fault == "revoked":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_credentials "
                    "SET revoked_at=now(),authority_version=3"
                )
            if fault == "faculty_revoked":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_faculties "
                    "SET revoked_at=now(),authority_version=2"
                )
            if fault == "principal_revoked":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_principals "
                    "SET active=false,authority_version=2"
                )
            if fault == "stale_version":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_credentials "
                    "SET backup_eligible=false,authority_version=3"
                )
            if fault == "version_guard":
                for update in (
                    "backup_eligible=false",
                    "auth_profile='device-counter-v1',auth_policy_hash=NULL,"
                    "backup_eligible=false",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await admin.execute(
                            "UPDATE ctt_staff_private.review_credentials SET " + update
                        )
            elif fault == "deferred_profile_change":
                tx = admin.transaction()
                await tx.start()
                await consume(admin, state)
                await admin.execute(
                    "UPDATE ctt_staff_private.review_credentials "
                    "SET auth_profile='device-counter-v1',auth_policy_hash=NULL,"
                    "backup_eligible=false,authority_version=3"
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await tx.commit()
            elif fault == "bs_audit":
                before = await admin.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_authenticator_audit"
                )
                async with writer.transaction():
                    await consume(writer, state, bs=True)
                credential = await admin.fetchrow(
                    "SELECT backup_state,authority_version FROM "
                    "ctt_staff_private.review_credentials"
                )
                assert credential["backup_state"] is True
                assert credential["authority_version"] == 2
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM "
                        "ctt_staff_private.review_authenticator_audit"
                    )
                    == before + 1
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.execute(
                        "DELETE FROM ctt_staff_private.review_authenticator_audit"
                    )
                with pytest.raises(asyncpg.RaiseError):
                    await admin.execute(ROLLBACK.read_text())
                await admin.execute("ROLLBACK")
            elif fault == "rollback":
                tx = writer.transaction()
                await tx.start()
                await consume(writer, state, bs=True)
                await tx.rollback()
                assert (
                    await admin.fetchval(
                        "SELECT backup_state FROM ctt_staff_private.review_credentials"
                    )
                    is False
                )
                assert await consume(writer, state)
            elif fault == "concurrent":
                other = await connect(platform_pg, WRITER)
                tx = writer.transaction()
                await tx.start()
                await consume(writer, state)
                pending = asyncio.create_task(consume(other, state))
                await asyncio.sleep(0.03)
                assert not pending.done()
                await tx.commit()
                try:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await pending
                finally:
                    await other.close()
            elif fault == "deferred_expiry":
                tx = writer.transaction()
                await tx.start()
                await consume(writer, state)
                await asyncio.sleep(0.55)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await tx.commit()
            elif fault == "legacy":
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.fetchval(
                        "SELECT ctt_staff_private.consume_review_challenge("
                        "$1,$2,$3,$4,0,1)",
                        state[2],
                        state[0],
                        state[3],
                        SCOPE,
                    )
                assert (
                    await admin.fetchval(
                        "SELECT sign_count FROM ctt_staff_private.review_credentials"
                    )
                    == 0
                )
            elif fault == "app":
                app = await connect(platform_pg, "devnous_user")
                try:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await consume(app, state)
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await app.execute(
                            "UPDATE ctt_staff_private.review_credentials "
                            "SET auth_profile='platform-passkey-v1'"
                        )
                finally:
                    await app.close()
            else:
                kwargs = {}
                if fault.startswith("null_"):
                    argument = fault.removeprefix("null_")
                    kwargs[argument] = None
                if fault == "profile":
                    kwargs["profile"] = "device-counter-v1"
                if fault == "policy":
                    kwargs["policy"] = "sha256:" + "0" * 64
                if fault == "be":
                    kwargs["be"] = False
                if fault == "bs":
                    kwargs.update(be=False, bs=True)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await consume(writer, state, **kwargs)
        finally:
            await writer.close()
            await cleanup(admin)
            await admin.close()

    asyncio.run(run())


def test_legacy_device_profile_stays_strict(platform_pg):
    async def run():
        admin = await connect(platform_pg)
        try:
            state = await synthetic_state(admin)
            credential = await admin.fetchrow(
                "SELECT auth_profile,auth_policy_hash,backup_eligible,backup_state "
                "FROM ctt_staff_private.review_credentials"
            )
            assert dict(credential) == dict(
                auth_profile="device-counter-v1",
                auth_policy_hash=None,
                backup_eligible=False,
                backup_state=False,
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await consume(admin, state)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.fetchval(
                    "SELECT ctt_staff_private.consume_review_challenge("
                    "$1,$2,$3,$4,0,0)",
                    state[2],
                    state[0],
                    state[3],
                    SCOPE,
                )
            assert await admin.fetchval(
                "SELECT ctt_staff_private.consume_review_challenge(" "$1,$2,$3,$4,0,1)",
                state[2],
                state[0],
                state[3],
                SCOPE,
            )
        finally:
            await cleanup(admin)
            await admin.close()

    asyncio.run(run())
