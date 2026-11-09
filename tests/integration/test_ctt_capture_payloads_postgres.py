"""Protected payload migration on PostgreSQL and concrete store contracts."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from devnous.copa_telmex.staff_capture_executor import (
    CaptureDenied,
    LockedAttempt,
    StaffCaptureExecutor,
    VerifiedDecision,
)
from devnous.copa_telmex.staff_capture_postgres import PostgresCaptureStore
from devnous.copa_telmex.staff_review_challenge import (
    canonical_scope_bytes,
    scope_hash,
)
from test_ctt_capture_anchors_postgres import anchor_pg as _anchor_fixture
from test_ctt_capture_anchors_postgres import (
    insert_attempt,
    remove_synthetic,
    sample,
)
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_capture_anchors_postgres",)
anchor_pg = _anchor_fixture
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261030_ctt_capture_payloads.sql"
ROLLBACK = ROOT / "database/migrations/20261030_ctt_capture_payloads.rollback.sql"
GUARD = ROOT / "database/migrations/20261030_ctt_signed_assertion_guard.sql"
GUARD_UNDO = (
    ROOT / "database/migrations/20261030_ctt_signed_assertion_guard.rollback.sql"
)


def test_private_payload_result_acl_immutable_and_rollback(anchor_pg):
    async def run():
        admin = await connect(anchor_pg)
        app = await connect(anchor_pg, "devnous_user")
        team = None
        try:
            await admin.execute(GUARD.read_text())
            await admin.execute(MIGRATION.read_text())
            scope, challenge, attempt = await sample(admin)
            team = scope.team_id
            await admin.execute(
                "INSERT INTO copa_telmex_teams VALUES($1,$2)",
                team,
                "synthetic",
            )
            await insert_attempt(admin, scope, challenge, attempt)
            await admin.execute(
                "INSERT INTO ctt_staff_private.capture_payloads "
                "VALUES($1,$2,$3,$4,$5)",
                attempt,
                b"{}",
                b"{}",
                b"{}",
                uuid4(),
            )
            payload = await admin.fetchrow(
                "SELECT * FROM ctt_staff_private.load_capture_payload($1)",
                attempt,
            )
            assert payload["effect_bytes"] == b"{}"
            for statement in (
                "SELECT * FROM ctt_staff_private.capture_payloads",
                "SELECT * FROM ctt_staff_private.capture_results",
                "SELECT ctt_staff_private.get_capture_result(NULL::uuid)",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app.execute(statement)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.execute(
                    "UPDATE ctt_staff_private.capture_payloads " "SET effect_bytes=$1",
                    b"changed",
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.execute(
                    "SELECT ctt_staff_private.record_capture_result($1,$2,$3)",
                    attempt,
                    uuid4(),
                    "sha256:" + sha256(b"{}").hexdigest(),
                )
            credential = await admin.fetchval(
                "SELECT credential_id FROM "
                "ctt_staff_private.review_challenges "
                "WHERE challenge_id=$1",
                challenge,
            )
            async with admin.transaction():
                await admin.fetchval(
                    "SELECT ctt_staff_private.consume_review_challenge("
                    "$1,$2,$3,$4,0,1)",
                    challenge,
                    scope.actor_id,
                    credential,
                    scope_hash(scope),
                )
                await admin.execute(
                    "SELECT ctt_staff_private.record_signed_assertion_guard("
                    "$1,$2,$3)",
                    challenge,
                    "sha256:" + "b" * 64,
                    payload["expires_at_seconds"],
                )
                result_id = uuid4()
                await admin.execute(
                    "SELECT ctt_staff_private.record_capture_result($1,$2,$3)",
                    attempt,
                    result_id,
                    "sha256:" + sha256(b"{}").hexdigest(),
                )
            assert (
                await admin.fetchval(
                    "SELECT ctt_staff_private.get_capture_result($1)", attempt
                )
                == result_id
            )
            with pytest.raises(asyncpg.RaiseError):
                await admin.execute(ROLLBACK.read_text())
            await admin.execute("ROLLBACK")
        finally:
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_results "
                "DISABLE TRIGGER ctt_capture_result_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.capture_results")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_results "
                "ENABLE TRIGGER ctt_capture_result_immutable"
            )
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "DISABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.signed_assertion_guards")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "ENABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_payloads "
                "DISABLE TRIGGER ctt_capture_payload_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.capture_payloads")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_payloads "
                "ENABLE TRIGGER ctt_capture_payload_immutable"
            )
            await admin.execute(ROLLBACK.read_text())
            await remove_synthetic(admin)
            if team is not None:
                await admin.execute("DELETE FROM copa_telmex_teams WHERE id=$1", team)
            await admin.execute(GUARD_UNDO.read_text())
            await app.close()
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "write_failure",
        "commit_failure",
        "gate_shorter_expiry",
        "gate_later_expiry",
    ],
)
def test_concrete_store_commit_idempotency_and_rollback(monkeypatch, outcome):
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    from test_ctt_staff_capture_authorization import authorization
    import devnous.copa_telmex.staff_capture_postgres as module

    values, body, sign = authorization()
    if outcome in ("gate_shorter_expiry", "gate_later_expiry"):
        body["expires_at"] = 1090 if outcome == "gate_shorter_expiry" else 1110
        values["envelope"] = sign(body)
    scope = values["scope"]
    attempt = LockedAttempt(
        values["attempt_id"],
        uuid4(),
        scope.actor_id,
        b"credential",
        scope_hash(scope),
        canonical_scope_bytes(scope),
        None,
    )
    decision = VerifiedDecision(
        attempt.attempt_id,
        attempt.challenge_id,
        attempt.principal_id,
        attempt.credential_id,
        attempt.scope_hash,
        0,
        1,
        1100,
        "sha256:" + "a" * 64,
    )
    payload = dict(
        review_bytes=values["review_bytes"],
        effect_bytes=values["effect_bytes"],
        gate_envelope=values["envelope"],
        nonce=values["nonce"],
        expires_at_seconds=1100,
    )

    class Connection:
        result = None
        consumed = False
        committed = False

        @asynccontextmanager
        async def transaction(self):
            old = self.result, self.consumed
            try:
                yield
                if outcome == "commit_failure":
                    raise RuntimeError("synthetic commit failure")
                self.committed = True
            except BaseException:
                self.result, self.consumed = old
                raise

        async def fetchrow(self, query, *args):
            return payload

        async def fetchval(self, query, *args):
            if query.startswith("SHOW"):
                return "0"
            if "get_capture_result" in query:
                return self.result
            if "consume_review_challenge" in query:
                self.consumed = True
                return True
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "record_capture_result" in query:
                self.result = args[1]

    conn = Connection()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    class Source:
        calls = 0

        @contextmanager
        def open_verified_snapshot(self, value):
            self.calls += 1
            yield SimpleNamespace(verify_unchanged=lambda: None)

    source = Source()

    async def load(connection, identifier):
        assert identifier == attempt.attempt_id
        return attempt

    monkeypatch.setattr(module, "load_locked_anchor", load)
    monkeypatch.setattr(module.time, "time", lambda: 1050)
    store = PostgresCaptureStore(
        Pool(),
        lambda item: source,
        public_keys=values["public_keys"],
        review_key=values["review_key"],
        effect_key=values["effect_key"],
    )

    async def persist(connection, **kwargs):
        assert kwargs["effect_bytes"] == payload["effect_bytes"]
        assert kwargs["nonce"] == payload["nonce"]
        assert conn.consumed
        if outcome == "write_failure":
            raise RuntimeError("synthetic write failure")
        return "sha256:" + sha256(payload["gate_envelope"]).hexdigest()

    monkeypatch.setattr(store._writer, "persist", persist)
    verifier = SimpleNamespace(verify=lambda raw: decision)

    async def run():
        executor = StaffCaptureExecutor(store, verifier)
        if outcome == "success":
            result = await executor.execute(attempt.attempt_id, b"signed")
            assert conn.committed
            assert result.result_id == conn.result
            assert await executor.execute(attempt.attempt_id, b"signed") == result
            assert source.calls == 1
        else:
            with pytest.raises(CaptureDenied):
                await executor.execute(attempt.attempt_id, b"signed")
            assert conn.result is None and not conn.consumed
            if outcome in ("gate_shorter_expiry", "gate_later_expiry"):
                assert source.calls == 0

    asyncio.run(run())


def test_payload_preflight_rejects_disabled_anchor_protection(anchor_pg):
    async def run():
        admin = await connect(anchor_pg)
        try:
            await admin.execute(GUARD.read_text())
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_attempt_anchors "
                "DISABLE TRIGGER ctt_capture_anchor_immutable"
            )
            with pytest.raises(
                asyncpg.RaiseError, match="trigger dependencies invalid"
            ):
                await admin.execute(MIGRATION.read_text())
            await admin.execute("ROLLBACK")
            assert (
                await admin.fetchval(
                    "SELECT to_regclass('ctt_staff_private.capture_payloads')"
                )
                is None
            )
        finally:
            await admin.execute(
                "ALTER TABLE ctt_staff_private.capture_attempt_anchors "
                "ENABLE TRIGGER ctt_capture_anchor_immutable"
            )
            await admin.execute(GUARD_UNDO.read_text())
            await admin.close()

    asyncio.run(run())
