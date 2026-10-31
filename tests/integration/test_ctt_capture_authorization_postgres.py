"""Joint gate commitments and retained sources in disposable PostgreSQL.

Identity, authority, documents and keys are synthetic. The effect is stored in
an isolated test table, never in Team/Player/TeamStaff or an operational gate.
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_capture_anchor_reader import load_locked_anchor
from devnous.copa_telmex.staff_capture_authorization import (
    DOMAIN,
    canonical_authorization,
    verify_capture_authorization,
)
from devnous.copa_telmex.staff_capture_effect import (
    bind_capture_effect,
    bind_review_plan,
    freeze_capture_effect,
)
from devnous.copa_telmex.staff_capture_executor import (
    CaptureDenied,
    CaptureResult,
    StaffCaptureExecutor,
)
from devnous.copa_telmex.staff_capture_source_adapter import SourceCheckAdapter
from devnous.copa_telmex.staff_review_challenge import (
    parse_canonical_scope,
    scope_hash,
)
from devnous.copa_telmex.staff_signed_assertion import (
    AssertionProof,
    SignedAssertionVerifier,
    sign_assertion,
)
from test_ctt_capture_anchors_postgres import insert_attempt, remove_synthetic
from test_ctt_staff_review_state_postgres import connect

pytest_plugins = ("test_ctt_signed_writer_postgres",)
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "scenario", ["success", "effect", "scope", "nonce", "rollback"]
)
def test_joint_gate_authorization_and_source_transaction(
    writer_pg, monkeypatch, scenario
):
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    import test_ctt_staff_capture_effect as effects
    from test_ctt_staff_capture_source_adapter import Provider, candidate

    source_attempt, source_scope, manifest, pdf, images = candidate()
    monkeypatch.setattr(
        effects,
        "sample",
        lambda: (
            manifest,
            source_scope,
            pdf,
            source_attempt.edition_binding,
        ),
    )
    args, plan, review_key, metadata = effects.lifecycle()
    scope = args["scope"]
    effect = freeze_capture_effect(**args)
    effect_key = b"isolated-complete-effect-key-32-bytes"
    attempt, challenge, nonce = uuid4(), uuid4(), uuid4()
    credential = secrets.token_bytes(32)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expiry = now + timedelta(minutes=3)
    signing = Ed25519PrivateKey.generate()
    public = signing.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    body = {
        "schema": "ctt.capture.authorization.v1",
        "kid": "test-gate",
        "scope_hash": scope_hash(scope),
        "attempt_id": str(attempt),
        "nonce": str(nonce),
        "review_plan_binding": bind_review_plan(plan, review_key),
        "effect_binding": bind_capture_effect(effect, effect_key),
        "issued_at": int(now.timestamp()),
        "expires_at": int(expiry.timestamp()),
        "authority_metadata": metadata,
    }
    envelope = canonical_authorization(
        {
            "body": body,
            "signature": base64.b64encode(
                signing.sign(DOMAIN + canonical_authorization(body))
            ).decode(),
        }
    )
    assertion = sign_assertion(
        AssertionProof(
            attempt,
            challenge,
            scope.actor_id,
            credential,
            scope_hash(scope),
            0,
            1,
            int(expiry.timestamp()),
        ),
        kid="test-gate",
        private_key=signing,
    )
    adapter = SourceCheckAdapter(
        Provider(manifest, pdf, images),
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    observed = []

    class Transaction:
        def __init__(self, conn):
            self.conn = conn

        async def assert_prepared_transactions_disabled(self):
            assert await self.conn.fetchval("SHOW max_prepared_transactions") == "0"

        async def lock_attempt(self, value):
            return await load_locked_anchor(self.conn, value)

        async def completed_result(self, value):
            result = await self.conn.fetchval(
                "SELECT result_id FROM ctt_test_authorized_effect W"
                "HERE attempt_id=$1",
                value,
            )
            return CaptureResult(value, result) if result else None

        async def validate_decision(self, decision, locked):
            assert self.conn.is_in_transaction()
            protected_nonce = await self.conn.fetchval(
                "SELECT nonce FROM ctt_test_authorization_state WHE"
                "RE attempt_id=$1 FOR UPDATE",
                locked.attempt_id,
            )
            current = parse_canonical_scope(locked.scope_bytes)
            if scenario == "scope":
                current = replace(current, policy_hash="sha256:" + "f" * 64)
            verify_capture_authorization(
                envelope=envelope,
                public_keys={"test-gate": public},
                scope=current,
                attempt_id=locked.attempt_id,
                nonce=protected_nonce,
                review_bytes=plan,
                effect_bytes=effect + (b" " if scenario == "effect" else b""),
                review_key=review_key,
                effect_key=effect_key,
                now_seconds=int(datetime.now(timezone.utc).timestamp()),
            )

        def open_source(self, locked):
            return adapter.open_verified_snapshot(locked)

        async def consume(self, decision):
            await self.conn.fetchval(
                "SELECT ctt_staff_private.consume_review_challenge("
                "$1,$2,$3,$4,$5,$6)",
                decision.challenge_id,
                decision.principal_id,
                decision.credential_id,
                decision.scope_hash,
                decision.prior_counter,
                decision.new_counter,
            )
            await self.conn.execute(
                "SELECT ctt_staff_private.record_signed_assertion_g" "uard($1,$2,$3)",
                decision.challenge_id,
                decision.proof_sha256,
                decision.expires_at,
            )

        async def capture(self, locked, snapshot):
            observed.append(snapshot)
            assert snapshot.pdf.read() == pdf
            assert list(snapshot.page_assets.values()) == images
            result = CaptureResult(locked.attempt_id, uuid4())
            await self.conn.execute(
                "INSERT INTO ctt_test_authorized_effect VALUES($1,$2,$3)",
                locked.attempt_id,
                result.result_id,
                effect,
            )
            if scenario == "rollback":
                raise RuntimeError("synthetic effect failure")
            return result

    class Store:
        @asynccontextmanager
        async def transaction(self):
            conn = await connect(writer_pg, "ctt_authorization_test_writer")
            try:
                async with conn.transaction():
                    yield Transaction(conn)
            finally:
                await conn.close()

    async def run():
        admin = await connect(writer_pg)
        try:
            await admin.execute("CREATE ROLE ctt_authorization_test_writer LOGIN")
            await admin.execute(
                "GRANT USAGE ON SCHEMA ctt_staff_private TO ctt_aut"
                "horization_test_writer"
            )
            await admin.execute(
                "GRANT EXECUTE ON FUNCTION ctt_staff_private.lock_c"
                "apture_anchor(uuid), "
                "ctt_staff_private.consume_review_challenge(uuid,uu"
                "id,bytea,text,bigint,bigint), "
                "ctt_staff_private.record_signed_assertion_guard(uu"
                "id,text,bigint) "
                "TO ctt_authorization_test_writer"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_authorized_effect (attempt_i"
                "d uuid PRIMARY KEY, "
                "result_id uuid NOT NULL, private_effect bytea NOT NULL)"
            )
            await admin.execute(
                "CREATE TABLE ctt_test_authorization_state (attempt"
                "_id uuid PRIMARY KEY, nonce uuid NOT NULL)"
            )
            await admin.execute(
                "REVOKE ALL ON ctt_test_authorized_effect, ctt_test"
                "_authorization_state FROM PUBLIC, devnous_user"
            )
            await admin.execute(
                "GRANT SELECT, INSERT ON ctt_test_authorized_effect"
                " TO ctt_authorization_test_writer"
            )
            await admin.execute(
                "GRANT SELECT, UPDATE ON ctt_test_authorization_sta"
                "te TO ctt_authorization_test_writer"
            )
            await admin.execute(
                "INSERT INTO ctt_test_authorization_state VALUES($1,$2)",
                attempt,
                uuid4() if scenario == "nonce" else nonce,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_principals(pr"
                "incipal_id,active) VALUES($1,true)",
                scope.actor_id,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_credentials(c"
                "redential_id,principal_id,attested_credential,user"
                "_handle) VALUES($1,$2,$3,$4)",
                credential,
                scope.actor_id,
                b"synthetic-credential",
                b"synthetic-handle",
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_faculties(pri"
                "ncipal_id,tenant_id,edition_id,faculty,valid_from,"
                "valid_until) "
                "VALUES($1,$2,$3,'capture_staff_v2',$4,$5)",
                scope.actor_id,
                scope.tenant_id,
                scope.edition_id,
                now - timedelta(minutes=1),
                expiry + timedelta(minutes=1),
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_challenges(ch"
                "allenge_id,principal_id,credential_id,tenant_id,ed"
                "ition_id,"
                "principal_authority_version,faculty_authority_vers"
                "ion,credential_authority_version,scope_hash,challe"
                "nge_bytes,issued_at,expires_at) "
                "VALUES($1,$2,$3,$4,$5,1,1,1,$6,$7,$8,$9)",
                challenge,
                scope.actor_id,
                credential,
                scope.tenant_id,
                scope.edition_id,
                scope_hash(scope),
                secrets.token_bytes(32),
                now,
                expiry,
            )
            edition = source_attempt.edition_binding
            await admin.execute(
                "INSERT INTO ctt_staff_private.manifest_anchors(man"
                "ifest_sha256,tenant_id,edition_id,tournament_id,ed"
                "ition_year,roster_slug) "
                "VALUES($1,$2,$3,$4,$5,$6)",
                scope.manifest_sha256,
                edition.tenant_id,
                edition.edition_id,
                edition.tournament_id,
                edition.edition_year,
                edition.roster_slug,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.source_anchors VALUE" "S($1,$2,$3)",
                scope.manifest_sha256,
                scope.document_id,
                scope.pdf_sha256,
            )
            await insert_attempt(admin, scope, challenge, attempt)
            executor = StaffCaptureExecutor(
                Store(), SignedAssertionVerifier({"test-gate": public})
            )
            if scenario == "success":
                result = await executor.execute(attempt, assertion)
                assert (await executor.execute(attempt, assertion)) == result
            else:
                with pytest.raises(CaptureDenied):
                    await executor.execute(attempt, assertion)
            expected = int(scenario == "success")
            for table in (
                "ctt_test_authorized_effect",
                "ctt_staff_private.review_challenge_consumptions",
                "ctt_staff_private.signed_assertion_guards",
            ):
                assert await admin.fetchval(f"SELECT count(*) FROM {table}") == expected
            assert (
                await admin.fetchval(
                    "SELECT sign_count FROM ctt_staff_private.review_cr"
                    "edentials WHERE credential_id=$1",
                    credential,
                )
                == expected
            )
            if expected:
                assert (
                    await admin.fetchval(
                        "SELECT private_effect FROM ctt_test_authorized_effect"
                    )
                    == effect
                )
            if scenario in ("success", "rollback"):
                assert len(observed) == 1 and observed[0].pdf.closed
            else:
                assert not observed
        finally:
            await admin.execute(
                "DROP TABLE IF EXISTS ctt_test_authorized_effect, c"
                "tt_test_authorization_state"
            )
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_gua"
                "rds DISABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await admin.execute("DELETE FROM ctt_staff_private.signed_assertion_guards")
            await admin.execute(
                "ALTER TABLE ctt_staff_private.signed_assertion_gua"
                "rds ENABLE TRIGGER ctt_assertion_guard_immutable"
            )
            await remove_synthetic(admin)
            await admin.execute("DROP OWNED BY ctt_authorization_test_writer")
            await admin.execute("DROP ROLE ctt_authorization_test_writer")
            await admin.close()

    asyncio.run(run())
