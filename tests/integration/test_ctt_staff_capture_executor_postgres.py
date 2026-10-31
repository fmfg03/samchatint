"""Transaction coordination against PostgreSQL with synthetic effects only."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_capture_executor import (
    CaptureDenied,
    CaptureResult,
    LockedAttempt,
    StaffCaptureExecutor,
    VerifiedDecision,
)
from test_ctt_staff_review_state_postgres import (
    SCOPE,
    clean,
    connect,
    synthetic_state,
)

pytest_plugins = ("test_ctt_staff_review_state_postgres",)


class Verifier:
    def __init__(self, decision):
        self.decision = decision

    def verify(self, data):
        assert data == b"synthetic"
        return self.decision


class Transaction:
    def __init__(self, connection, attempt, fail):
        self.conn, self.attempt, self.fail = connection, attempt, fail

    async def assert_prepared_transactions_disabled(self):
        assert await self.conn.fetchval("SHOW max_prepared_transactions") == "0"

    async def lock_attempt(self, attempt_id):
        value = await self.conn.fetchval(
            "SELECT attempt_id FROM ctt_test_attempt WHERE attempt_id=$1 " "FOR UPDATE",
            attempt_id,
        )
        if value is None:
            raise CaptureDenied("capture denied")
        return self.attempt

    async def completed_result(self, attempt_id):
        value = await self.conn.fetchval(
            "SELECT result_id FROM ctt_test_effect WHERE attempt_id=$1",
            attempt_id,
        )
        return CaptureResult(attempt_id, value) if value else None

    async def validate_decision(self, decision, attempt):
        # Synthetic adapter: no real identity or faculty is established.
        return None

    @contextmanager
    def open_source(self, attempt):
        # Synthetic adapter: no source authority is claimed.
        yield SimpleNamespace(verify_unchanged=lambda: None)

    async def consume(self, d):
        await self.conn.fetchval(
            "SELECT ctt_staff_private.consume_review_challenge(" "$1,$2,$3,$4,$5,$6)",
            d.challenge_id,
            d.principal_id,
            d.credential_id,
            d.scope_hash,
            d.prior_counter,
            d.new_counter,
        )

    async def capture(self, attempt, source):
        assert callable(source.verify_unchanged)
        result = CaptureResult(attempt.attempt_id, uuid4())
        await self.conn.execute(
            "INSERT INTO ctt_test_effect VALUES($1,$2)",
            attempt.attempt_id,
            result.result_id,
        )
        if self.fail == "write":
            raise RuntimeError("synthetic write failure")
        if self.fail == "expiry":
            await asyncio.sleep(1.2)
        return result


class Store:
    def __init__(self, socket, attempt, fail=False):
        self.socket, self.attempt, self.fail = socket, attempt, fail
        self.ready_commit = False
        self.commit_error = None

    @asynccontextmanager
    async def transaction(self):
        conn = await connect(self.socket, "ctt_test_writer")
        try:
            try:
                async with conn.transaction():
                    yield Transaction(conn, self.attempt, self.fail)
                    self.ready_commit = True
            except Exception as exc:
                if self.ready_commit:
                    self.commit_error = str(exc)
                raise
        finally:
            await conn.close()


@pytest.mark.parametrize("failure", [None, "write", "expiry"])
def test_consumption_and_effect_share_transaction(state_pg, failure):
    async def run():
        admin = await connect(state_pg)
        try:
            await admin.execute("CREATE ROLE ctt_test_writer LOGIN")
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_test_writer"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.consume_review_challenge("
                "uuid,uuid,bytea,text,bigint,bigint) TO ctt_test_writer"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_attempt (attempt_id uuid PRIMARY KEY)"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_effect (attempt_id uuid PRIMARY KEY, "
                "result_id uuid NOT NULL)"
            )
            await admin.execute(
                "GRANT SELECT, UPDATE ON ctt_test_attempt TO ctt_test_writer"
            )
            await admin.execute(
                "GRANT SELECT, INSERT ON ctt_test_effect TO ctt_test_writer"
            )
            lifetime = (
                timedelta(seconds=1) if failure == "expiry" else timedelta(minutes=3)
            )
            principal, _, challenge, credential = await synthetic_state(
                admin, lifetime=lifetime
            )
            attempt = LockedAttempt(uuid4(), challenge, principal, credential, SCOPE)
            await admin.execute(
                "INSERT INTO ctt_test_attempt VALUES ($1)", attempt.attempt_id
            )
            decision = VerifiedDecision(
                attempt.attempt_id,
                challenge,
                principal,
                credential,
                SCOPE,
                0,
                1,
            )
            store = Store(state_pg, attempt, failure)
            executor = StaffCaptureExecutor(store, Verifier(decision))
            if failure:
                with pytest.raises(CaptureDenied):
                    await executor.execute(attempt.attempt_id, b"synthetic")
                if failure == "expiry":
                    assert store.ready_commit
                    assert store.commit_error == (
                        "CTT review challenge expired or authority "
                        "changed before commit"
                    )
            else:
                result, again = await asyncio.gather(
                    executor.execute(attempt.attempt_id, b"synthetic"),
                    executor.execute(attempt.attempt_id, b"synthetic"),
                )
                assert result == again
            count = 0 if failure else 1
            assert await admin.fetchval("SELECT count(*) FROM ctt_test_effect") == count
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_challenge_consumptions"
                )
                == count
            )
        finally:
            await clean(admin)
            await admin.execute("DROP TABLE IF EXISTS ctt_test_effect")
            await admin.execute("DROP TABLE IF EXISTS ctt_test_attempt")
            await admin.execute("DROP OWNED BY ctt_test_writer")
            await admin.execute("DROP ROLE ctt_test_writer")
            await admin.close()

    asyncio.run(run())
