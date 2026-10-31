"""Isolated PostgreSQL tests for protected challenge state, not authority."""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261028_ctt_staff_review_state.sql"
ROLLBACK = ROOT / "database/migrations/20261028_ctt_staff_review_state.rollback.sql"
SCOPE = "sha256:" + "a" * 64


async def connect(socket, user="postgres"):
    return await asyncpg.connect(
        host=socket, port=int(PORT), user=user, database="postgres"
    )


@pytest.fixture(scope="module")
def state_pg(request):
    security_pg = request.getfixturevalue("isolated_pg")

    async def apply():
        connection = await connect(security_pg)
        try:
            await connection.execute(MIGRATION.read_text())
        finally:
            await connection.close()

    asyncio.run(apply())
    yield security_pg


async def synthetic_state(conn, lifetime=timedelta(minutes=3)):
    principal, edition, challenge_id = uuid4(), uuid4(), uuid4()
    credential = secrets.token_bytes(32)
    now = datetime.now(timezone.utc)
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.review_principals("
            "principal_id,active) VALUES($1,true)"
        ),
        principal,
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.review_credentials"
            "(credential_id,principal_id,attested_credential,"
            "user_handle) VALUES($1,$2,$3,$4)"
        ),
        credential,
        principal,
        b"synthetic-credential",
        b"opaque",
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.review_faculties(p"
            "rincipal_id,tenant_id,edition_id,faculty,valid_f"
            "rom,valid_until) VALUES($1,'synthetic',$2,'captu"
            "re_staff_v2',$3,$4)"
        ),
        principal,
        edition,
        now - timedelta(minutes=1),
        now + timedelta(minutes=10),
    )
    await conn.execute(
        (
            "INSERT INTO ctt_staff_private.review_challenges("
            "challenge_id,principal_id,credential_id,tenant_i"
            "d,edition_id,principal_authority_version,faculty"
            "_authority_version,credential_authority_version,"
            "scope_hash,challenge_bytes,issued_at,expires_at)"
            " VALUES($1,$2,$3,'synthetic',$4,1,1,1,$5,$6,$7,$"
            "8)"
        ),
        challenge_id,
        principal,
        credential,
        edition,
        SCOPE,
        secrets.token_bytes(32),
        now,
        now + lifetime,
    )
    return principal, edition, challenge_id, credential


async def clean(conn):
    # Test-only superuser cleanup; production evidence is append-only.
    triggers = (
        ("review_challenge_consumptions", "ctt_review_consumption_immutable"),
        ("review_challenges", "ctt_review_challenge_immutable"),
        ("review_faculties", "ctt_review_faculty_no_delete"),
        ("review_credentials", "ctt_review_credential_no_delete"),
        ("review_principals", "ctt_review_principal_no_delete"),
        ("review_authority_audit", "ctt_review_audit_immutable"),
    )
    for table, trigger in triggers:
        await conn.execute(
            "ALTER TABLE ctt_staff_private." + table + " DISABLE TRIGGER " + trigger
        )
    try:
        for table in (
            "review_challenge_consumptions",
            "review_challenges",
            "review_authority_audit",
            "review_faculties",
            "review_credentials",
            "review_principals",
        ):
            await conn.execute("DELETE FROM ctt_staff_private." + table)
    finally:
        for table, trigger in triggers:
            await conn.execute(
                "ALTER TABLE ctt_staff_private." + table + " ENABLE TRIGGER " + trigger
            )


async def consume(conn, state, scope=SCOPE, prior=0, new=1):
    principal, _edition, challenge, credential = state
    return await conn.fetchval(
        "SELECT ctt_staff_private.consume_review_challenge($1,$2,$3,$4,$5,$6)",
        challenge,
        principal,
        credential,
        scope,
        prior,
        new,
    )


def test_application_cannot_read_write_or_invoke_protected_state(state_pg):
    async def run():
        admin = await connect(state_pg)
        app = await connect(state_pg, "devnous_user")
        try:
            for table in (
                "review_principals",
                "review_credentials",
                "review_faculties",
                "review_challenges",
                "review_challenge_consumptions",
                "review_authority_audit",
            ):
                assert not await admin.fetchval(
                    (
                        "SELECT has_table_privilege('devnous_user',$1,'SE"
                        "LECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                    ),
                    "ctt_staff_private." + table,
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.fetchval(
                    (
                        "SELECT ctt_staff_private.consume_review_challeng"
                        "e($1,$2,$3,$4,$5,$6)"
                    ),
                    uuid4(),
                    uuid4(),
                    b"credential",
                    SCOPE,
                    0,
                    1,
                )
        finally:
            await app.close()
            await admin.close()

    asyncio.run(run())


def test_consume_requires_matching_scope_counter_and_current_faculty(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            state = await synthetic_state(conn)
            for scope, prior, new in [
                (SCOPE, 0, 0),
                ("sha256:" + "b" * 64, 0, 1),
                (SCOPE, 1, 2),
            ]:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await consume(conn, state, scope, prior, new)
            assert await consume(conn, state)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await consume(conn, state)
            assert (
                await conn.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    (
                        "SELECT sign_count FROM ctt_staff_private.review_"
                        "credentials WHERE credential_id=$1"
                    ),
                    state[3],
                )
                == 1
            )
        finally:
            await clean(conn)
            await conn.close()

    asyncio.run(run())


def test_revocation_or_changed_authority_closes_pending_challenge(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            state = await synthetic_state(conn)
            await conn.execute(
                (
                    "UPDATE ctt_staff_private.review_faculties SET re"
                    "voked_at=clock_timestamp(), authority_version=au"
                    "thority_version+1 WHERE principal_id=$1"
                ),
                state[0],
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await consume(conn, state)
            assert (
                await conn.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 0
            )
            await conn.execute(
                (
                    "UPDATE ctt_staff_private.review_faculties SET re"
                    "voked_at=NULL, authority_version=authority_versi"
                    "on+1 WHERE principal_id=$1"
                ),
                state[0],
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await consume(conn, state)
        finally:
            await clean(conn)
            await conn.close()

    asyncio.run(run())


def test_concurrent_consume_commits_once(state_pg):
    async def run():
        owner = await connect(state_pg)
        first = await connect(state_pg)
        second = await connect(state_pg)
        try:
            state = await synthetic_state(owner)

            async def once(conn):
                try:
                    async with conn.transaction():
                        return await consume(conn, state)
                except asyncpg.InsufficientPrivilegeError:
                    return False

            results = await asyncio.gather(once(first), once(second))
            assert sorted(results) == [False, True]
            assert (
                await owner.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 1
            )
        finally:
            await first.close()
            await second.close()
            await clean(owner)
            await owner.close()

    asyncio.run(run())


def test_rollback_refuses_nonempty_state_and_works_empty(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            await synthetic_state(conn)
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute(ROLLBACK.read_text())
            # A failed migration transaction must be rolled back.
            await conn.execute("ROLLBACK")
            await clean(conn)
            await conn.execute(ROLLBACK.read_text())
            assert (
                await conn.fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_challenges')"
                )
                is None
            )
            # Keep the module-scoped fixture available to subsequent tests.
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(run())


def test_challenge_expiring_while_waiting_on_lock_is_denied(state_pg):
    async def run():
        owner = await connect(state_pg)
        locker = await connect(state_pg)
        consumer = await connect(state_pg)
        transaction = locker.transaction()
        try:
            state = await synthetic_state(owner, lifetime=timedelta(milliseconds=300))

            await transaction.start()
            await locker.execute(
                "SELECT 1 FROM ctt_staff_private.review_faculties "
                "WHERE principal_id=$1 FOR UPDATE",
                state[0],
            )
            task = asyncio.create_task(consume(consumer, state))
            await asyncio.sleep(0.45)
            assert not task.done()
            await transaction.commit()
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await task
            assert (
                await owner.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 0
            )
        finally:
            await locker.close()
            await consumer.close()
            await clean(owner)
            await owner.close()

    asyncio.run(run())


def test_consumption_rolls_back_with_transaction(state_pg):
    async def run():
        owner = await connect(state_pg)
        try:
            state = await synthetic_state(owner)
            tx = owner.transaction()
            await tx.start()
            assert await consume(owner, state)
            await tx.rollback()
            assert (
                await owner.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 0
            )
            assert (
                await owner.fetchval(
                    (
                        "SELECT sign_count FROM ctt_staff_private.review_"
                        "credentials WHERE credential_id=$1"
                    ),
                    state[3],
                )
                == 0
            )
            assert await consume(owner, state)
        finally:
            await clean(owner)
            await owner.close()

    asyncio.run(run())


def test_revocation_committed_while_waiting_on_lock_is_denied(state_pg):
    async def run():
        owner = await connect(state_pg)
        revoker = await connect(state_pg)
        consumer = await connect(state_pg)
        transaction = revoker.transaction()
        try:
            state = await synthetic_state(owner)
            await transaction.start()
            await revoker.execute(
                (
                    "UPDATE ctt_staff_private.review_faculties SET re"
                    "voked_at=clock_timestamp(), authority_version=au"
                    "thority_version+1 WHERE principal_id=$1"
                ),
                state[0],
            )
            task = asyncio.create_task(consume(consumer, state))
            await asyncio.sleep(0.15)
            assert not task.done()
            await transaction.commit()
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await task
            assert (
                await owner.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 0
            )
        finally:
            await revoker.close()
            await consumer.close()
            await clean(owner)
            await owner.close()

    asyncio.run(run())


def test_credential_rotation_invalidates_existing_challenge(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            state = await synthetic_state(conn)
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute(
                    "UPDATE ctt_staff_private.review_credentials "
                    "SET attested_credential=$1 WHERE credential_id=$2",
                    b"changed-public-key",
                    state[3],
                )
            await conn.execute(
                (
                    "UPDATE ctt_staff_private.review_credentials SET "
                    "attested_credential=$1, authority_version=author"
                    "ity_version+1 WHERE credential_id=$2"
                ),
                b"changed-public-key",
                state[3],
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await consume(conn, state)
        finally:
            await clean(conn)
            await conn.close()

    asyncio.run(run())


def test_commit_after_expiration_rolls_back_consumption(state_pg):
    async def run():
        owner = await connect(state_pg)
        try:
            state = await synthetic_state(owner, lifetime=timedelta(milliseconds=300))

            tx = owner.transaction()
            await tx.start()
            assert await consume(owner, state)
            await asyncio.sleep(0.45)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await tx.commit()
            assert (
                await owner.fetchval(
                    (
                        "SELECT count(*) FROM ctt_staff_private.review_ch"
                        "allenge_consumptions"
                    )
                )
                == 0
            )
        finally:
            await clean(owner)
            await owner.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["table", "function", "sequence", "schema"])
def test_unexpected_default_or_schema_acl_fails_closed(state_pg, kind):
    async def run():
        conn = await connect(state_pg)
        role = "ctt_review_rogue_test"
        try:
            await conn.execute(ROLLBACK.read_text())
            await conn.execute("CREATE ROLE " + role + " NOLOGIN")
            if kind == "table":
                await conn.execute(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE postgres "
                    "IN SCHEMA ctt_staff_private GRANT UPDATE ON TABLES TO " + role
                )
            elif kind == "sequence":
                await conn.execute(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE postgres "
                    "IN SCHEMA ctt_staff_private GRANT USAGE ON SEQUENCES TO " + role
                )
            elif kind == "function":
                await conn.execute(
                    (
                        "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SC"
                        "HEMA ctt_staff_private GRANT EXECUTE ON FUNCTION"
                        "S TO "
                    )
                    + role
                )
            else:
                await conn.execute("GRANT USAGE ON SCHEMA ctt_staff_private TO " + role)
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute(MIGRATION.read_text())
            await conn.execute("ROLLBACK")
            if kind == "table":
                await conn.execute(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE postgres "
                    "IN SCHEMA ctt_staff_private REVOKE UPDATE ON TABLES FROM " + role
                )
            elif kind == "sequence":
                await conn.execute(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE postgres "
                    "IN SCHEMA ctt_staff_private REVOKE USAGE "
                    "ON SEQUENCES FROM " + role
                )
            elif kind == "function":
                await conn.execute(
                    (
                        "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SC"
                        "HEMA ctt_staff_private REVOKE EXECUTE ON FUNCTIO"
                        "NS FROM "
                    )
                    + role
                )
            else:
                await conn.execute(
                    "REVOKE USAGE ON SCHEMA ctt_staff_private FROM " + role
                )
            await conn.execute("DROP ROLE " + role)
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A SQL transaction controller can advance deferred checks; "
        "this test deliberately bypasses the trusted executor"
    ),
)
def test_acceptance_forced_immediate_cannot_commit_after_expiry(state_pg):
    async def run():
        owner = await connect(state_pg)
        try:
            state = await synthetic_state(owner, lifetime=timedelta(milliseconds=300))

            tx = owner.transaction()
            await tx.start()
            assert await consume(owner, state)
            await owner.execute("SET CONSTRAINTS ALL IMMEDIATE")
            await asyncio.sleep(0.45)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await tx.commit()
        finally:
            await clean(owner)
            await owner.close()

    asyncio.run(run())


def test_challenge_and_consumption_evidence_are_append_only(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            state = await synthetic_state(conn)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    (
                        "UPDATE ctt_staff_private.review_challenges SET s"
                        "cope_hash=$1 WHERE challenge_id=$2"
                    ),
                    "sha256:" + "b" * 64,
                    state[2],
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    (
                        "DELETE FROM ctt_staff_private.review_challenges "
                        "WHERE challenge_id=$1"
                    ),
                    state[2],
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "TRUNCATE ctt_staff_private.review_challenges CASCADE"
                )
            assert await consume(conn, state)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    (
                        "DELETE FROM ctt_staff_private.review_challenge_c"
                        "onsumptions WHERE challenge_id=$1"
                    ),
                    state[2],
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "TRUNCATE ctt_staff_private.review_challenge_consumptions"
                )
        finally:
            await clean(conn)
            await conn.close()

    asyncio.run(run())


def test_other_sql_session_cannot_advance_gate_constraint(state_pg):
    async def run():
        owner = await connect(state_pg)
        gate = None
        app = None
        gate_role = "ctt_isolated_gate_test"
        try:
            state = await synthetic_state(owner, lifetime=timedelta(milliseconds=400))
            await owner.execute("CREATE ROLE " + gate_role + " LOGIN")
            await owner.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private TO " + gate_role
            )
            await owner.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.consume_review_challenge("
                "uuid,uuid,bytea,text,bigint,bigint) TO " + gate_role
            )
            gate = await connect(state_pg, gate_role)
            app = await connect(state_pg, "devnous_user")
            assert not await owner.fetchval(
                "SELECT pg_has_role('devnous_user',$1,'MEMBER')", gate_role
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await consume(app, state)
            tx = gate.transaction()
            await tx.start()
            assert await consume(gate, state)
            await app.execute("SET CONSTRAINTS ALL IMMEDIATE")
            await asyncio.sleep(0.55)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await tx.commit()
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_challenge_consumptions"
                )
                == 0
            )
        finally:
            if gate is not None:
                await gate.close()
            if app is not None:
                await app.close()
            await clean(owner)
            await owner.execute("DROP OWNED BY " + gate_role)
            await owner.execute("DROP ROLE " + gate_role)
            await owner.close()

    asyncio.run(run())


def test_authority_audit_is_private_append_only_and_tracks_rotation(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            state = await synthetic_state(conn)
            events = await conn.fetch(
                "SELECT entity_kind, operation, principal_id, "
                "credential_record_id, "
                "tenant_id, edition_id, authority_version FROM "
                "ctt_staff_private.review_authority_audit ORDER BY audit_id"
            )
            assert [event["entity_kind"] for event in events] == [
                "principal",
                "credential",
                "faculty",
            ]
            assert all(event["principal_id"] == state[0] for event in events)
            assert events[1]["credential_record_id"] is not None
            assert events[2]["edition_id"] == state[1]
            assert await consume(conn, state)
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM " "ctt_staff_private.review_authority_audit"
                )
                == 3
            )
            await conn.execute(
                "UPDATE ctt_staff_private.review_credentials "
                "SET attested_credential=$1, "
                "authority_version=authority_version+1 "
                "WHERE credential_id=$2",
                b"rotated-key",
                state[3],
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM " "ctt_staff_private.review_authority_audit"
                )
                == 4
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "DELETE FROM ctt_staff_private.review_authority_audit"
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("TRUNCATE ctt_staff_private.review_authority_audit")
        finally:
            await clean(conn)
            await conn.close()

    asyncio.run(run())


def test_rollback_waits_for_concurrent_enrollment_and_preserves_it(state_pg):
    async def run():
        owner = await connect(state_pg)
        writer = await connect(state_pg)
        tx = writer.transaction()
        principal = uuid4()
        try:
            await tx.start()
            await writer.execute(
                "INSERT INTO ctt_staff_private.review_principals "
                "(principal_id,active) VALUES($1,true)",
                principal,
            )
            task = asyncio.create_task(owner.execute(ROLLBACK.read_text()))
            await asyncio.sleep(0.15)
            assert not task.done()
            await tx.commit()
            with pytest.raises(asyncpg.RaiseError, match="requires empty state"):
                await task
            await owner.execute("ROLLBACK")
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_principals "
                    "WHERE principal_id=$1",
                    principal,
                )
                == 1
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM "
                    "ctt_staff_private.review_authority_audit "
                    "WHERE principal_id=$1",
                    principal,
                )
                == 1
            )
        finally:
            await writer.close()
            await clean(owner)
            await owner.close()

    asyncio.run(run())
