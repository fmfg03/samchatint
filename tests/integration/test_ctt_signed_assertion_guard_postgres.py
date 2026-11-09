"""Signed proof expiry guard tests; PostgreSQL and identities are synthetic."""

import asyncio
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest

from test_ctt_staff_review_state_postgres import (
    clean,
    connect,
    consume,
    synthetic_state,
)

pytest_plugins = ("test_ctt_staff_review_state_postgres",)
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261030_ctt_signed_assertion_guard.sql"
ROLLBACK = ROOT / "database/migrations/20261030_ctt_signed_assertion_guard.rollback.sql"


@pytest.fixture(scope="module")
def guard_pg(state_pg):
    async def apply():
        conn = await connect(state_pg)
        try:
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(apply())
    yield state_pg

    async def rollback():
        conn = await connect(state_pg)
        try:
            await conn.execute(ROLLBACK.read_text())
        finally:
            await conn.close()

    asyncio.run(rollback())


async def clean_guard(conn):
    await conn.execute(
        "ALTER TABLE ctt_staff_private.signed_assertion_guards "
        "DISABLE TRIGGER ctt_assertion_guard_immutable"
    )
    try:
        await conn.execute("DELETE FROM ctt_staff_private.signed_assertion_guards")
    finally:
        await conn.execute(
            "ALTER TABLE ctt_staff_private.signed_assertion_guards "
            "ENABLE TRIGGER ctt_assertion_guard_immutable"
        )
    await clean(conn)


def test_guard_matches_challenge_and_blocks_app_and_rollback(guard_pg):
    async def run():
        conn, app = await connect(guard_pg), await connect(guard_pg, "devnous_user")
        try:
            state = await synthetic_state(conn)
            expiry = await conn.fetchval(
                "SELECT floor(extract(epoch FROM expires_at))::bigint "
                "FROM ctt_staff_private.review_challenges "
                "WHERE challenge_id=$1",
                state[2],
            )
            await consume(conn, state)
            sql = (
                "INSERT INTO ctt_staff_private.signed_assertion_guards "
                "VALUES($1,$2,$3)"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql, state[2], "sha256:" + "a" * 64, expiry - 1)
            await conn.execute(sql, state[2], "sha256:" + "a" * 64, expiry)
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(sql, state[2], "sha256:" + "a" * 64, expiry)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(
                    "SELECT * FROM ctt_staff_private.signed_assertion_guards"
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "DELETE FROM ctt_staff_private.signed_assertion_guards"
                )
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute(ROLLBACK.read_text())
            await conn.execute("ROLLBACK")
        finally:
            await clean_guard(conn)
            await app.close()
            await conn.close()

    asyncio.run(run())


def test_signed_expiry_is_checked_at_commit_and_rolls_back(guard_pg):
    async def run():
        conn = await connect(guard_pg)
        try:
            state = await synthetic_state(conn, lifetime=timedelta(seconds=6))
            expiry = await conn.fetchval(
                "SELECT floor(extract(epoch FROM expires_at))::bigint "
                "FROM ctt_staff_private.review_challenges "
                "WHERE challenge_id=$1",
                state[2],
            )
            tx = conn.transaction()
            await tx.start()
            await consume(conn, state)
            # Check the independent challenge guard while it is current. The
            # signed-proof guard below remains deferred until actual COMMIT,
            # so the test cannot depend on which expiry guard fires first.
            await conn.execute(
                "SET CONSTRAINTS "
                "ctt_staff_private.ctt_review_consumption_commit_guard IMMEDIATE"
            )
            await conn.execute(
                "INSERT INTO ctt_staff_private.signed_assertion_guards "
                "VALUES($1,$2,$3)",
                state[2],
                "sha256:" + "a" * 64,
                expiry,
            )
            await conn.execute(
                "SELECT pg_sleep_until(to_timestamp($1::double precision) "
                "+ interval '1 second')",
                float(expiry),
            )
            with pytest.raises(
                asyncpg.InsufficientPrivilegeError,
                match="signed assertion expired",
            ):
                await tx.commit()
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_challenge_consumptions"
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private." "signed_assertion_guards"
                )
                == 0
            )
        finally:
            await clean_guard(conn)
            await conn.close()

    asyncio.run(run())
