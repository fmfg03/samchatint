"""Durable channel nonces on disposable PostgreSQL; no capture authority."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import time
from uuid import uuid4

import asyncpg
import pytest

from devnous.copa_telmex.staff_capture_transport_store import (
    PostgresTransportNonces,
)
from test_ctt_staff_review_state_postgres import connect
from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_review_state_postgres",)
ROOT = Path(__file__).resolve().parents[2]


def test_nonce_commit_replay_concurrency_and_acl(state_pg, tmp_path):
    async def run():
        conn = await connect(state_pg)
        pool = None
        try:
            await conn.execute(
                (
                    ROOT / "database/migrations/20261030_ctt_transport_nonces.sql"
                ).read_text()
            )
            await conn.execute("CREATE ROLE ctt_nonce_test LOGIN")
            await conn.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_nonce_test"
            )
            await conn.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.claim_transport_nonce(uuid,bigint) "
                "TO ctt_nonce_test"
            )
            pool = await asyncpg.create_pool(
                host=state_pg,
                port=int(PORT),
                database="postgres",
                user="ctt_nonce_test",
                min_size=1,
                max_size=4,
            )
            nonce = uuid4()
            assert (
                sum(
                    await asyncio.gather(
                        *[
                            PostgresTransportNonces(pool)(nonce, int(time.time()) + 30)
                            for _ in range(4)
                        ]
                    )
                )
                == 1
            )
            # A new pool/consumer still sees the committed replay marker.
            await pool.close()
            pool = await asyncpg.create_pool(
                host=state_pg,
                port=int(PORT),
                database="postgres",
                user="ctt_nonce_test",
                min_size=1,
                max_size=2,
            )
            assert not await PostgresTransportNonces(pool)(nonce, int(time.time()) + 30)
            assert not await PostgresTransportNonces(pool)(
                uuid4(), int(time.time()) - 120
            )
            # A borrowed connection in an outer transaction would only
            # release a savepoint, not durably commit a channel nonce.
            async with pool.acquire() as borrowed:
                async with borrowed.transaction():

                    class DirtyPool:
                        @asynccontextmanager
                        async def acquire(self):
                            yield borrowed

                    refused = uuid4()
                    assert not await PostgresTransportNonces(DirtyPool())(
                        refused, int(time.time()) + 30
                    )
                    assert not await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM "
                        "ctt_staff_private.transport_nonces WHERE nonce=$1)",
                        refused,
                    )
            async with pool.acquire() as restricted:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await restricted.fetchval(
                        "SELECT count(*) FROM " "ctt_staff_private.transport_nonces"
                    )
            import os
            from devnous.copa_telmex.staff_capture_executor import (
                CaptureResult,
            )
            from devnous.copa_telmex.staff_capture_transport import (
                CaptureUnixClient,
                CaptureUnixTransport,
                CaptureTransportUncertain,
            )

            calls = []

            async def handler(attempt, decision):
                calls.append(attempt)
                return CaptureResult(attempt, uuid4())

            channel_key = b"isolated-channel-key-with-at-least-32-bytes"
            server = CaptureUnixTransport(
                key=channel_key,
                allowed_uids=frozenset({os.getuid()}),
                consume_nonce=PostgresTransportNonces(pool),
                handler=handler,
            )
            path = tmp_path / "writer.sock"
            async with server.serve(path):
                client = CaptureUnixClient(
                    path=path,
                    key=channel_key,
                    server_uid=os.getuid(),
                )
                request_nonce, attempt = uuid4(), uuid4()
                await client.execute(
                    attempt, b"synthetic-decision", nonce=request_nonce
                )
                with pytest.raises(CaptureTransportUncertain):
                    await client.execute(
                        attempt, b"synthetic-decision", nonce=request_nonce
                    )
            assert calls == [attempt]
            app = await connect(state_pg, "devnous_user")
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.fetchval(
                        "SELECT ctt_staff_private." "claim_transport_nonce($1,$2)",
                        uuid4(),
                        int(time.time()) + 30,
                    )
            finally:
                await app.close()
        finally:
            if pool is not None:
                await pool.close()
            await conn.execute(
                "ALTER TABLE ctt_staff_private.transport_nonces "
                "DISABLE TRIGGER ctt_transport_nonce_immutable"
            )
            await conn.execute("DELETE FROM ctt_staff_private.transport_nonces")
            await conn.execute(
                "ALTER TABLE ctt_staff_private.transport_nonces "
                "ENABLE TRIGGER ctt_transport_nonce_immutable"
            )
            await conn.execute("DROP OWNED BY ctt_nonce_test")
            await conn.execute("DROP ROLE ctt_nonce_test")
            await conn.execute(
                (
                    ROOT
                    / "database/migrations"
                    / "20261030_ctt_transport_nonces.rollback.sql"
                ).read_text()
            )
            await conn.close()

    asyncio.run(run())
