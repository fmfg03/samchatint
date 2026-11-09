"""Synthetic signature, protected anchor, guard and effect in one transaction.

This is a test adapter with synthetic identity, authority, source and effect.
It is not an operational writer or evidence of review or provenance.
"""

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_capture_anchor_reader import load_locked_anchor
from devnous.copa_telmex.staff_capture_executor import (
    CaptureDenied,
    CaptureResult,
    StaffCaptureExecutor,
)
from devnous.copa_telmex.staff_review_challenge import scope_hash
from devnous.copa_telmex.staff_signed_assertion import (
    AssertionProof,
    SignedAssertionVerifier,
    sign_assertion,
)
from test_ctt_capture_anchors_postgres import (
    insert_attempt,
    remove_synthetic,
    sample,
)
from test_ctt_staff_capture_executor_postgres import Transaction
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_staff_review_state_postgres",)
ROOT = Path(__file__).resolve().parents[2]
ANCHOR = ROOT / "database/migrations/20261030_ctt_capture_anchors.sql"
ANCHOR_UNDO = ROOT / "database/migrations/20261030_ctt_capture_anchors.rollback.sql"
GUARD = ROOT / "database/migrations/20261030_ctt_signed_assertion_guard.sql"
GUARD_UNDO = (
    ROOT / "database/migrations/20261030_ctt_signed_assertion_guard.rollback.sql"
)


@pytest.fixture(scope="module")
def writer_pg(state_pg):
    async def run():
        conn = await connect(state_pg)
        try:
            await conn.execute(ANCHOR.read_text())
            await conn.execute(GUARD.read_text())
        finally:
            await conn.close()

    asyncio.run(run())
    yield state_pg

    async def undo():
        conn = await connect(state_pg)
        try:
            await conn.execute(GUARD_UNDO.read_text())
            await conn.execute(ANCHOR_UNDO.read_text())
        finally:
            await conn.close()

    asyncio.run(undo())


class TestTransaction(Transaction):
    __test__ = False

    async def lock_attempt(self, attempt_id):
        return await load_locked_anchor(self.conn, attempt_id)

    async def validate_decision(self, decision, attempt):
        if decision.expires_at is None or decision.proof_sha256 is None:
            raise CaptureDenied("capture denied")
        # Human faculty, receipts and source provenance remain synthetic.

    async def consume(self, decision):
        await super().consume(decision)
        await self.conn.execute(
            "SELECT ctt_staff_private.record_signed_assertion_guard($1,$2,$3)",
            decision.challenge_id,
            decision.proof_sha256,
            decision.expires_at,
        )


class Store:
    def __init__(self, socket, fail=False):
        self.socket = socket
        self.fail = fail

    @asynccontextmanager
    async def transaction(self):
        conn = await connect(self.socket, "ctt_signed_test_writer")
        try:
            async with conn.transaction():
                failure = "write" if self.fail else None
                yield TestTransaction(conn, None, failure)
        finally:
            await conn.close()


@pytest.mark.parametrize("fail", [False, True])
def test_signed_decision_consumption_and_effect_in_one_transaction(writer_pg, fail):
    async def run():
        admin = await connect(writer_pg)
        try:
            scope, challenge, attempt = await sample(admin)
            await insert_attempt(admin, scope, challenge, attempt)
            row = await admin.fetchrow(
                "SELECT credential_id, "
                "floor(extract(epoch FROM expires_at))::bigint AS expiry "
                "FROM ctt_staff_private.review_challenges "
                "WHERE challenge_id=$1",
                challenge,
            )
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            proof = AssertionProof(
                attempt,
                challenge,
                scope.actor_id,
                row["credential_id"],
                scope_hash(scope),
                0,
                1,
                row["expiry"],
            )
            signed = sign_assertion(proof, kid="test", private_key=key)
            await admin.execute("CREATE ROLE ctt_signed_test_writer LOGIN")
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private " "TO ctt_signed_test_writer"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.lock_capture_anchor(uuid), "
                "ctt_staff_private.consume_review_challenge("
                "uuid,uuid,bytea,text,bigint,bigint), "
                "ctt_staff_private.record_signed_assertion_guard("
                "uuid,text,bigint) "
                "TO ctt_signed_test_writer"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_effect (attempt_id uuid PRIMARY KEY, "
                "result_id uuid NOT NULL)"
            )
            await admin.execute(
                "GRANT SELECT, INSERT ON ctt_test_effect " "TO ctt_signed_test_writer"
            )
            executor = StaffCaptureExecutor(
                Store(writer_pg, fail=fail),
                SignedAssertionVerifier({"test": public}),
            )
            with pytest.raises(CaptureDenied):
                await executor.execute(attempt, signed + b" ")
            if fail:
                with pytest.raises(CaptureDenied):
                    await executor.execute(attempt, signed)
                assert await admin.fetchval("SELECT count(*) FROM ctt_test_effect") == 0
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM ctt_staff_private."
                        "signed_assertion_guards"
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
                return
            first, second = await asyncio.gather(
                executor.execute(attempt, signed),
                executor.execute(attempt, signed),
            )
            assert first == second
            assert await admin.fetchval("SELECT count(*) FROM ctt_test_effect") == 1
            assert (
                await admin.fetchval(
                    "SELECT proof_sha256 FROM ctt_staff_private."
                    "signed_assertion_guards "
                    "WHERE challenge_id=$1",
                    challenge,
                )
                == "sha256:" + sha256(signed).hexdigest()
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private."
                    "review_challenge_consumptions"
                )
                == 1
            )
        finally:
            await admin.execute("DROP TABLE IF EXISTS ctt_test_effect")
            # Remove synthetic guard and anchors before the review state.
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "DISABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.signed_assertion_guards")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "ENABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await remove_synthetic(admin)
            await admin.execute("DROP OWNED BY ctt_signed_test_writer")
            await admin.execute("DROP ROLE ctt_signed_test_writer")
            await admin.close()

    asyncio.run(run())


@pytest.mark.parametrize("fail", [False, "write", "mutate"])
def test_real_source_snapshot_with_signed_postgres_transaction(
    writer_pg, monkeypatch, fail
):
    from dataclasses import replace
    import secrets
    from uuid import uuid4

    from devnous.copa_telmex.staff_capture_source_adapter import (
        SourceCheckAdapter,
    )

    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    from test_ctt_staff_capture_source_adapter import Provider, candidate

    async def run():
        admin = await connect(writer_pg)
        retained = []
        try:
            base_scope, original, _ = await sample(admin)
            locked, scope, manifest, pdf, images = candidate()
            scope = replace(
                scope,
                actor_id=base_scope.actor_id,
                edition_id=base_scope.edition_id,
            )
            edition = replace(locked.edition_binding, edition_id=scope.edition_id)
            challenge, attempt = uuid4(), uuid4()
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_challenges "
                "SELECT $1,principal_id,credential_id,tenant_id,edition_id,"
                "faculty,principal_authority_version,"
                "faculty_authority_version,"
                "credential_authority_version,$2,$3,issued_at,expires_at "
                "FROM ctt_staff_private.review_challenges "
                "WHERE challenge_id=$4",
                challenge,
                scope_hash(scope),
                secrets.token_bytes(32),
                original,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.manifest_anchors "
                "(manifest_sha256,tenant_id,edition_id,tournament_id,"
                "edition_year,roster_slug) VALUES($1,$2,$3,$4,$5,$6)",
                scope.manifest_sha256,
                edition.tenant_id,
                edition.edition_id,
                edition.tournament_id,
                edition.edition_year,
                edition.roster_slug,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.source_anchors " "VALUES($1,$2,$3)",
                scope.manifest_sha256,
                scope.document_id,
                scope.pdf_sha256,
            )
            await insert_attempt(admin, scope, challenge, attempt)
            provider = Provider(manifest, pdf, images)
            adapter = SourceCheckAdapter(
                provider,
                max_source_bytes=1_000_000,
                max_asset_bytes=1_000_000,
                max_manifest_bytes=1_000_000,
            )

            class SourceTransaction(TestTransaction):
                def open_source(self, item):
                    return adapter.open_verified_snapshot(item)

                async def capture(self, item, snapshot):
                    retained.append(snapshot)
                    assert provider.closed
                    assert snapshot.pdf.read() == pdf
                    assert list(snapshot.page_assets.values()) == images
                    result = CaptureResult(item.attempt_id, uuid4())
                    await self.conn.execute(
                        "INSERT INTO ctt_test_effect VALUES($1,$2)",
                        item.attempt_id,
                        result.result_id,
                    )
                    if fail == "write":
                        raise RuntimeError("synthetic effect failure")
                    if fail == "mutate":
                        import os

                        fd = os.open(
                            f"/proc/self/fd/{snapshot.pdf.fileno()}",
                            os.O_WRONLY,
                        )
                        try:
                            os.pwrite(fd, b"X", 0)
                        finally:
                            os.close(fd)
                    return result

            class SourceStore:
                @asynccontextmanager
                async def transaction(self):
                    conn = await connect(writer_pg, "ctt_signed_test_writer")
                    try:
                        async with conn.transaction():
                            yield SourceTransaction(conn, None, None)
                    finally:
                        await conn.close()

            row = await admin.fetchrow(
                "SELECT credential_id, "
                "floor(extract(epoch FROM expires_at))::bigint AS expiry "
                "FROM ctt_staff_private.review_challenges "
                "WHERE challenge_id=$1",
                challenge,
            )
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            signed = sign_assertion(
                AssertionProof(
                    attempt,
                    challenge,
                    scope.actor_id,
                    row["credential_id"],
                    scope_hash(scope),
                    0,
                    1,
                    row["expiry"],
                ),
                kid="test",
                private_key=key,
            )
            await admin.execute("CREATE ROLE ctt_signed_test_writer LOGIN")
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private " "TO ctt_signed_test_writer"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION "
                "ctt_staff_private.lock_capture_anchor(uuid), "
                "ctt_staff_private.consume_review_challenge("
                "uuid,uuid,bytea,text,bigint,bigint), "
                "ctt_staff_private.record_signed_assertion_guard("
                "uuid,text,bigint) TO ctt_signed_test_writer"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_effect (attempt_id uuid PRIMARY KEY, "
                "result_id uuid NOT NULL)"
            )
            await admin.execute(
                "GRANT SELECT, INSERT ON ctt_test_effect " "TO ctt_signed_test_writer"
            )
            executor = StaffCaptureExecutor(
                SourceStore(), SignedAssertionVerifier({"test": public})
            )
            if fail:
                with pytest.raises(CaptureDenied):
                    await executor.execute(attempt, signed)
            else:
                await executor.execute(attempt, signed)
            assert await admin.fetchval(
                "SELECT sign_count FROM ctt_staff_private.review_credentials "
                "WHERE credential_id=$1",
                row["credential_id"],
            ) == int(not fail)
            assert len(retained) == 1
            assert retained[0].pdf.closed
            for table in (
                "ctt_test_effect",
                "ctt_staff_private.review_challenge_consumptions",
                "ctt_staff_private.signed_assertion_guards",
            ):
                assert await admin.fetchval(f"SELECT count(*) FROM {table}") == int(
                    not fail
                )
        finally:
            await admin.execute("DROP TABLE IF EXISTS ctt_test_effect")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "DISABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.signed_assertion_guards")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_guards "
                "ENABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await remove_synthetic(admin)
            await admin.execute("DROP OWNED BY ctt_signed_test_writer")
            await admin.execute("DROP ROLE ctt_signed_test_writer")
            await admin.close()

    asyncio.run(run())
