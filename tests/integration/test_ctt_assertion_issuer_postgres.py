"""Protected assertion issuer state on disposable PostgreSQL; no real identity."""

import asyncio
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_capture_anchors_postgres import anchor_pg as _anchor_fixture
from test_ctt_capture_anchors_postgres import insert_attempt, remove_synthetic, sample
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_capture_anchors_postgres",)
anchor_pg = _anchor_fixture
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database/migrations"
ISSUER = "ctt_synthetic_assertion_issuer"
WRITER = "ctt_synthetic_assertion_writer"
RECORD_SIG = "uuid,text,bigint,bigint,text,boolean,boolean,text,bigint"
PROOF = "sha256:" + "7" * 64
POLICY = "sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3"


@pytest.fixture(scope="module")
def issuer_pg(anchor_pg):
    async def setup():
        conn = await connect(anchor_pg)
        try:
            for name in (
                "20261030_ctt_signed_assertion_guard.sql",
                "20261030_ctt_platform_passkey.sql",
                "20261030_ctt_assertion_issuer.sql",
            ):
                await conn.execute((MIGRATIONS / name).read_text())
            for role in (ISSUER, WRITER):
                await conn.execute(f"CREATE ROLE {role} LOGIN")
                await conn.execute(f"GRANT USAGE ON SCHEMA ctt_staff_private TO {role}")
            await conn.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.load_review_assertion_context(uuid),"
                f"ctt_staff_private.record_review_assertion_issuance({RECORD_SIG}) "
                f"TO {ISSUER}"
            )
            await conn.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                "consume_review_challenge(uuid,uuid,bytea,text,bigint,bigint),"
                "ctt_staff_private.consume_review_challenge_v2("
                "uuid,uuid,bytea,text,bigint,bigint,text,boolean,boolean,text),"
                "ctt_staff_private.record_signed_assertion_guard(uuid,text,bigint) "
                f"TO {WRITER}"
            )
        finally:
            await conn.close()

    asyncio.run(setup())
    yield anchor_pg

    async def teardown():
        conn = await connect(anchor_pg)
        try:
            for name in (
                "20261030_ctt_assertion_issuer.rollback.sql",
                "20261030_ctt_platform_passkey.rollback.sql",
                "20261030_ctt_signed_assertion_guard.rollback.sql",
            ):
                await conn.execute((MIGRATIONS / name).read_text())
        finally:
            await conn.close()

    asyncio.run(teardown())


async def cleanup(conn):
    # Disposable fixture superuser only; production evidence remains immutable.
    for table in (
        "signed_assertion_guards",
        "review_assertion_issuances",
        "review_authenticator_audit",
    ):
        await conn.execute(
            f"ALTER TABLE ctt_staff_private.{table} DISABLE TRIGGER USER"
        )
        try:
            await conn.execute(f"DELETE FROM ctt_staff_private.{table}")
        finally:
            await conn.execute(
                f"ALTER TABLE ctt_staff_private.{table} ENABLE TRIGGER USER"
            )
    await remove_synthetic(conn)


async def state(conn, platform=False):
    scope, challenge, attempt = await sample(conn)
    if platform:
        await conn.execute(
            "UPDATE ctt_staff_private.review_credentials SET "
            "auth_profile='platform-passkey-v1',auth_policy_hash=$1,"
            "backup_eligible=true,authority_version=2 WHERE principal_id=$2",
            POLICY,
            scope.actor_id,
        )
        # Create the immutable challenge with the credential authority version.
        await conn.execute(
            "ALTER TABLE ctt_staff_private.review_challenges "
            "DISABLE TRIGGER ctt_review_challenge_immutable"
        )
        try:
            await conn.execute(
                "UPDATE ctt_staff_private.review_challenges "
                "SET credential_authority_version=2 WHERE challenge_id=$1",
                challenge,
            )
        finally:
            await conn.execute(
                "ALTER TABLE ctt_staff_private.review_challenges "
                "ENABLE TRIGGER ctt_review_challenge_immutable"
            )
    await insert_attempt(conn, scope, challenge, attempt)
    context = await load(conn, attempt)
    return scope, challenge, attempt, context


async def load(conn, attempt):
    return await conn.fetchrow(
        "SELECT * FROM ctt_staff_private.load_review_assertion_context($1)", attempt
    )


async def record(conn, attempt, context, **changes):
    values = (
        dict(
            proof=PROOF,
            prior=context["sign_count"],
            new=0 if context["auth_profile"] == "platform-passkey-v1" else 1,
            profile=context["auth_profile"],
            be=context["backup_eligible"],
            bs=context["backup_eligible"],
            policy=context["auth_policy_hash"],
            expiry=context["expires_at_seconds"],
        )
        | changes
    )
    return await conn.fetchval(
        "SELECT ctt_staff_private.record_review_assertion_issuance("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9)",
        attempt,
        *values.values(),
    )


async def consume(conn, ctx, new, bs=False):
    args = (
        ctx["challenge_id"],
        ctx["principal_id"],
        ctx["credential_id"],
        ctx["scope_hash"],
        ctx["sign_count"],
        new,
    )
    if ctx["auth_profile"] == "platform-passkey-v1":
        return await conn.fetchval(
            "SELECT ctt_staff_private.consume_review_challenge_v2("
            "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            *args,
            ctx["auth_profile"],
            ctx["backup_eligible"],
            bs,
            ctx["auth_policy_hash"],
        )
    return await conn.fetchval(
        "SELECT ctt_staff_private.consume_review_challenge($1,$2,$3,$4,$5,$6)", *args
    )


@pytest.mark.parametrize("platform", [False, True])
def test_loader_and_issuance_are_bound_idempotent_and_do_not_consume(
    issuer_pg, platform
):
    async def run():
        admin, issuer = await connect(issuer_pg), await connect(issuer_pg, ISSUER)
        try:
            scope, challenge, attempt, ctx = await state(admin, platform)
            assert dict(await load(issuer, attempt)) == dict(ctx)
            assert ctx["principal_id"] == scope.actor_id
            assert ctx["sign_count"] == 0
            assert ctx["auth_profile"] == (
                "platform-passkey-v1" if platform else "device-counter-v1"
            )
            assert await record(issuer, attempt, ctx)
            assert await record(issuer, attempt, ctx)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_assertion_issuances"
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_challenge_consumptions"
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT sign_count FROM ctt_staff_private.review_credentials "
                    "WHERE credential_id=$1",
                    ctx["credential_id"],
                )
                == 0
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await record(issuer, attempt, ctx, proof="sha256:" + "8" * 64)
            for statement in (
                "DELETE FROM ctt_staff_private.review_assertion_issuances",
                "UPDATE ctt_staff_private.review_assertion_issuances SET new_counter=2",
                "TRUNCATE ctt_staff_private.review_assertion_issuances",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await admin.execute(statement)
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(
                    (
                        MIGRATIONS / "20261030_ctt_assertion_issuer.rollback.sql"
                    ).read_text()
                )
            await admin.execute("ROLLBACK")
        finally:
            await cleanup(admin)
            await issuer.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("platform", [False, True])
def test_consumption_guard_requires_exact_previously_issued_proof(issuer_pg, platform):
    async def run():
        admin, issuer, writer = (
            await connect(issuer_pg),
            await connect(issuer_pg, ISSUER),
            await connect(issuer_pg, WRITER),
        )
        try:
            _, challenge, attempt, ctx = await state(admin, platform)
            async with writer.transaction():
                await consume(writer, ctx, 0 if platform else 1, platform)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with writer.transaction():
                        await writer.execute(
                            "SELECT ctt_staff_private."
                            "record_signed_assertion_guard($1,$2,$3)",
                            challenge,
                            PROOF,
                            ctx["expires_at_seconds"],
                        )
                # Roll back this isolated negative probe; issuance is a
                # separate operation before the writer's later consumption.
                await writer.execute("ROLLBACK")
            assert await record(issuer, attempt, ctx)
            async with writer.transaction():
                await consume(writer, ctx, 0 if platform else 1, platform)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with writer.transaction():
                        await writer.execute(
                            "SELECT ctt_staff_private."
                            "record_signed_assertion_guard($1,$2,$3)",
                            challenge,
                            "sha256:" + "9" * 64,
                            ctx["expires_at_seconds"],
                        )
                await writer.execute(
                    "SELECT ctt_staff_private."
                    "record_signed_assertion_guard($1,$2,$3)",
                    challenge,
                    PROOF,
                    ctx["expires_at_seconds"],
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await load(issuer, attempt)
        finally:
            await cleanup(admin)
            await writer.close()
            await issuer.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"prior": 1},
        {"new": -1},
        {"new": 4294967296},
        {"profile": "unknown"},
        {"be": False},
        {"bs": None},
        {"policy": None},
        {"expiry": 1},
        {"proof": None},
    ],
)
def test_issuance_rejects_altered_protected_metadata(issuer_pg, change):
    async def run():
        admin, issuer = await connect(issuer_pg), await connect(issuer_pg, ISSUER)
        try:
            _, _, attempt, ctx = await state(admin, platform=True)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await record(issuer, attempt, ctx, **change)
        finally:
            await cleanup(admin)
            await issuer.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("what", ["principal", "credential", "faculty", "missing"])
def test_loader_revalidates_revocation_and_scope(issuer_pg, what):
    async def run():
        admin, issuer = await connect(issuer_pg), await connect(issuer_pg, ISSUER)
        try:
            _, _, attempt, ctx = await state(admin)
            if what == "principal":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_principals "
                    "SET active=false,authority_version=2"
                )
            elif what == "credential":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_credentials "
                    "SET revoked_at=clock_timestamp(),authority_version=2"
                )
            elif what == "faculty":
                await admin.execute(
                    "UPDATE ctt_staff_private.review_faculties "
                    "SET revoked_at=clock_timestamp(),authority_version=2"
                )
            else:
                attempt = uuid4()
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await load(issuer, attempt)
        finally:
            await cleanup(admin)
            await issuer.close()
            await admin.close()

    asyncio.run(run())


def test_application_and_issuer_have_no_unintended_privileges(issuer_pg):
    async def run():
        admin, issuer, app = (
            await connect(issuer_pg),
            await connect(issuer_pg, ISSUER),
            await connect(issuer_pg, "devnous_user"),
        )
        try:
            _, _, attempt, ctx = await state(admin)
            for conn in (issuer, app):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetch(
                        "SELECT * FROM ctt_staff_private.review_credentials"
                    )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute("SET ROLE samchat_ctt_staff_owner")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await consume(conn, ctx, 1)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await load(app, attempt)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await record(app, attempt, ctx)
        finally:
            await cleanup(admin)
            for conn in (app, issuer, admin):
                await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("same", [True, False])
def test_concurrent_issuance_serializes_one_immutable_proof(issuer_pg, same):
    async def run():
        admin = await connect(issuer_pg)
        try:
            _, _, attempt, ctx = await state(admin, platform=True)

            async def emit(proof):
                conn = await connect(issuer_pg, ISSUER)
                try:
                    async with conn.transaction():
                        return await record(conn, attempt, ctx, proof=proof)
                except asyncpg.InsufficientPrivilegeError:
                    return False
                finally:
                    await conn.close()

            result = await asyncio.gather(
                emit(PROOF), emit(PROOF if same else "sha256:" + "6" * 64)
            )
            assert sum(result) == (2 if same else 1)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_assertion_issuances"
                )
                == 1
            )
        finally:
            await cleanup(admin)
            await admin.close()

    asyncio.run(run())


def test_issuance_rollback_keeps_challenge_unused(issuer_pg):
    async def run():
        admin, issuer = await connect(issuer_pg), await connect(issuer_pg, ISSUER)
        try:
            _, _, attempt, ctx = await state(admin, platform=True)
            tx = issuer.transaction()
            await tx.start()
            assert await record(issuer, attempt, ctx)
            await tx.rollback()
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_assertion_issuances"
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_challenge_consumptions"
                )
                == 0
            )
            assert await record(issuer, attempt, ctx)
        finally:
            await cleanup(admin)
            await issuer.close()
            await admin.close()

    asyncio.run(run())


def test_consumption_metadata_cannot_differ_from_issued_assertion(issuer_pg):
    async def run():
        admin, issuer, writer = (
            await connect(issuer_pg),
            await connect(issuer_pg, ISSUER),
            await connect(issuer_pg, WRITER),
        )
        try:
            _, challenge, attempt, ctx = await state(admin, platform=True)
            await record(issuer, attempt, ctx, bs=True)
            async with writer.transaction():
                await consume(writer, ctx, 0, bs=False)
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with writer.transaction():
                        await writer.execute(
                            "SELECT ctt_staff_private."
                            "record_signed_assertion_guard($1,$2,$3)",
                            challenge,
                            PROOF,
                            ctx["expires_at_seconds"],
                        )
                await writer.execute("ROLLBACK")
        finally:
            await cleanup(admin)
            for conn in (writer, issuer, admin):
                await conn.close()

    asyncio.run(run())


@pytest.mark.parametrize("what", ["expired", "future", "hash"])
def test_loader_rejects_invalid_time_or_source_bytes(issuer_pg, what):
    async def run():
        admin, issuer = await connect(issuer_pg), await connect(issuer_pg, ISSUER)
        try:
            _, challenge, attempt, _ = await state(admin)
            if what == "hash":
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.capture_attempt_anchors "
                    "DISABLE TRIGGER ctt_capture_anchor_immutable"
                )
                try:
                    await admin.execute(
                        "UPDATE ctt_staff_private.capture_attempt_anchors "
                        "SET scope_bytes='{}'::bytea WHERE attempt_id=$1",
                        attempt,
                    )
                finally:
                    await admin.execute(
                        "ALTER TABLE ctt_staff_private.capture_attempt_anchors "
                        "ENABLE TRIGGER ctt_capture_anchor_immutable"
                    )
            else:
                await admin.execute(
                    "ALTER TABLE ctt_staff_private.review_challenges "
                    "DISABLE TRIGGER ctt_review_challenge_immutable"
                )
                try:
                    times = (
                        "issued_at=clock_timestamp()-interval '2 minutes',"
                        "expires_at=clock_timestamp()-interval '1 minute'"
                        if what == "expired"
                        else "issued_at=clock_timestamp()+interval '1 minute',"
                        "expires_at=clock_timestamp()+interval '2 minutes'"
                    )
                    await admin.execute(
                        "UPDATE ctt_staff_private.review_challenges SET "
                        + times
                        + " WHERE challenge_id=$1",
                        challenge,
                    )
                finally:
                    await admin.execute(
                        "ALTER TABLE ctt_staff_private.review_challenges "
                        "ENABLE TRIGGER ctt_review_challenge_immutable"
                    )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await load(issuer, attempt)
        finally:
            await cleanup(admin)
            await issuer.close()
            await admin.close()

    asyncio.run(run())


def test_preflight_rejects_disabled_guard_and_restores_safely(issuer_pg):
    async def run():
        admin = await connect(issuer_pg)
        try:
            await admin.execute(
                (MIGRATIONS / "20261030_ctt_assertion_issuer.rollback.sql").read_text()
            )
            await admin.execute(
                "REVOKE EXECUTE ON FUNCTION ctt_staff_private."
                "record_signed_assertion_guard(uuid,text,bigint) FROM " + WRITER
            )
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "DISABLE TRIGGER ctt_assertion_guard_commit"
            )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(
                    (MIGRATIONS / "20261030_ctt_assertion_issuer.sql").read_text()
                )
            await admin.execute("ROLLBACK")
        finally:
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "ENABLE TRIGGER ctt_assertion_guard_commit"
            )
            await admin.execute(
                (MIGRATIONS / "20261030_ctt_assertion_issuer.sql").read_text()
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.load_review_assertion_context(uuid),"
                f"ctt_staff_private.record_review_assertion_issuance({RECORD_SIG}) "
                f"TO {ISSUER}"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                "record_signed_assertion_guard(uuid,text,bigint) TO " + WRITER
            )
            await admin.close()

    asyncio.run(run())
