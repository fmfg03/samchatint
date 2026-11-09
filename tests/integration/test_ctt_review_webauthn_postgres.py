"""Synthetic cross-repository assertion and transaction; no capture authority.

Set CTT_GATE_SOURCE and CTT_FIDO2_PATH to the isolated gate and dependency
folders. This test starts its own disposable PostgreSQL instance. WebAuthn
signs the challenge; the scope hash is bound to that challenge by the trusted
server state and the atomic database consume, not signed as a separate field.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from devnous.copa_telmex.staff_review_challenge import (
    ReviewScope,
    SourcePage,
    create_challenge_candidate,
    scope_hash,
)
from test_ctt_staff_review_state_postgres import clean, connect

pytest_plugins = ("test_ctt_staff_review_state_postgres",)


@pytest.fixture
def webauthn(monkeypatch):
    gate_dir = os.environ.get("CTT_GATE_SOURCE")
    deps_dir = os.environ.get("CTT_FIDO2_PATH")
    if not gate_dir or not deps_dir:
        pytest.skip("Set CTT_GATE_SOURCE and CTT_FIDO2_PATH to isolated paths")
    for value in (gate_dir, deps_dir):
        path = Path(value).resolve(strict=True)
        if not path.is_relative_to(Path("/tmp")):
            pytest.fail("Cross-repository dependencies must be isolated under /tmp")
        monkeypatch.syspath_prepend(str(path))
    monkeypatch.setattr(logging.getLogger("fido2.server"), "level", logging.WARNING)
    pytest.importorskip("fido2")
    return importlib.import_module("staff_review_webauthn_v2")


@pytest.mark.parametrize(
    "scenario", ["success", "scope", "signature", "replay", "rollback"]
)
def test_synthetic_challenge_with_atomic_consumption(state_pg, webauthn, scenario):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.cose import ES256
    from fido2.utils import websafe_decode
    from fido2.webauthn import (
        AttestedCredentialData,
        AuthenticationResponse,
        AuthenticatorAssertionResponse,
        AuthenticatorData,
        CollectedClientData,
    )

    async def run():
        conn = await connect(state_pg)
        sha = "sha256:" + "a" * 64
        mac = "hmac-sha256:" + "b" * 64
        principal, edition, challenge_id = uuid4(), uuid4(), uuid4()
        scope = ReviewScope(
            tenant_id="sample-tenant",
            tournament_slug="sample-edition",
            edition_id=edition,
            session_id=uuid4(),
            draft_id=uuid4(),
            draft_version=3,
            draft_content_hash=sha,
            team_id=uuid4(),
            manifest_sha256="a" * 64,
            document_id="b" * 64,
            pdf_sha256="c" * 64,
            source_pages=(SourcePage(1, uuid4(), sha, 13),),
            actor_id=principal,
            actor_role="coordinador",
            authority_binding=mac,
            staff_and_changes_binding=mac,
            decision_hash=sha,
            review_receipt_id=sha,
            policy_hash=sha,
            operation_id=sha,
        )
        candidate = create_challenge_candidate(
            scope, issued_at=datetime.now(timezone.utc).replace(microsecond=0)
        )
        digest = scope_hash(scope)
        private = ec.generate_private_key(ec.SECP256R1())
        credential_id, handle = b"synthetic-credential", b"synthetic-handle"
        credential = AttestedCredentialData.create(
            b"\x00" * 16,
            credential_id,
            ES256.from_cryptography_key(private.public_key()),
        )
        try:
            await conn.execute(
                "INSERT INTO "
                "ctt_staff_private.review_principals(principal_id,active) "
                "VALUES($1,true)",
                principal,
            )
            await conn.execute(
                "INSERT INTO "
                "ctt_staff_private.review_credentials(credential_id,princip"
                "al_id,attested_credential,user_handle,sign_count) "
                "VALUES($1,$2,$3,$4,4)",
                credential_id,
                principal,
                bytes(credential),
                handle,
            )
            await conn.execute(
                "INSERT INTO "
                "ctt_staff_private.review_faculties(principal_id,tenant_id,"
                "edition_id,faculty,valid_from,valid_until) "
                "VALUES($1,$2,$3,'capture_staff_v2',$4,$5)",
                principal,
                scope.tenant_id,
                edition,
                candidate.issued_at - timedelta(minutes=1),
                candidate.expires_at + timedelta(minutes=1),
            )
            await conn.execute(
                "INSERT INTO "
                "ctt_staff_private.review_challenges(challenge_id,principal"
                "_id,credential_id,tenant_id,edition_id,principal_authority"
                "_version,faculty_authority_version,credential_authority_ve"
                "rsion,scope_hash,challenge_bytes,issued_at,expires_at) "
                "VALUES($1,$2,$3,$4,$5,1,1,1,$6,$7,$8,$9)",
                challenge_id,
                principal,
                credential_id,
                scope.tenant_id,
                edition,
                digest,
                candidate.challenge,
                candidate.issued_at,
                candidate.expires_at,
            )
            row = await conn.fetchrow(
                "SELECT challenge_bytes,scope_hash FROM "
                "ctt_staff_private.review_challenges WHERE challenge_id=$1",
                challenge_id,
            )
            assert row["challenge_bytes"] == candidate.challenge
            assert row["scope_hash"] == digest
            state, options = webauthn.prepare_challenge(
                challenge_id=str(challenge_id),
                principal_id=str(principal),
                scope_hash=digest,
                challenge=candidate.challenge,
                credential=bytes(credential),
                user_handle=handle,
                counter=4,
                issued_at=int(candidate.issued_at.timestamp()),
                expires_at=int(candidate.expires_at.timestamp()),
            )
            assert (
                websafe_decode(options["publicKey"]["challenge"]) == candidate.challenge
            )
            client = CollectedClientData.create(
                "webauthn.get",
                candidate.challenge,
                webauthn.ORIGIN,
                cross_origin=False,
            )
            auth = AuthenticatorData.create(
                hashlib.sha256(webauthn.RP_ID.encode()).digest(),
                AuthenticatorData.FLAG.UP | AuthenticatorData.FLAG.UV,
                5,
            )
            signature = private.sign(
                bytes(auth) + client.hash, ec.ECDSA(hashes.SHA256())
            )
            if scenario == "signature":
                signature = bytes([signature[0] ^ 1]) + signature[1:]
            response = json.loads(
                json.dumps(
                    dict(
                        AuthenticationResponse(
                            raw_id=credential_id,
                            response=AuthenticatorAssertionResponse(
                                client_data=client,
                                authenticator_data=auth,
                                signature=signature,
                                user_handle=handle,
                            ),
                        )
                    )
                )
            )
            changed_digest = scope_hash(replace(scope, pdf_sha256="d" * 64))
            assert changed_digest != digest
            calls = []
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
            from devnous.copa_telmex.staff_signed_assertion import (
                AssertionProof,
                sign_assertion,
                verify_assertion,
            )

            signing_key = Ed25519PrivateKey.generate()
            public_key = signing_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            attempt_id = uuid4()

            async def consume(value):
                assert conn.is_in_transaction()
                assert value.challenge_id == str(challenge_id)
                assert value.principal_id == str(principal)
                assert value.scope_hash == digest
                assert value.credential_id == credential_id
                proof = AssertionProof(
                    attempt_id,
                    UUID(value.challenge_id),
                    UUID(value.principal_id),
                    value.credential_id,
                    value.scope_hash,
                    value.previous_counter,
                    value.new_counter,
                    value.expires_at,
                )
                wire = sign_assertion(proof, kid="synthetic", private_key=signing_key)
                assert (
                    verify_assertion(wire, public_keys={"synthetic": public_key})
                    == proof
                )
                calls.append(value)
                return await conn.fetchval(
                    "SELECT "
                    "ctt_staff_private.consume_review_challenge($1,$2,$3,$4"
                    ",$5,$6)",
                    UUID(value.challenge_id),
                    UUID(value.principal_id),
                    value.credential_id,
                    value.scope_hash,
                    value.previous_counter,
                    value.new_counter,
                )

            async def verify(expected=digest):
                return await webauthn.verify_review_assertion_async(
                    state=state,
                    expected_principal_id=str(principal),
                    expected_scope_hash=expected,
                    response=response,
                    now=int(datetime.now(timezone.utc).timestamp()),
                    consume_once=consume,
                )

            tx = conn.transaction()
            await tx.start()
            if scenario in ("scope", "signature"):
                with pytest.raises(webauthn.ReviewAssertionDenied):
                    await verify(changed_digest if scenario == "scope" else digest)
                assert not calls
                await tx.rollback()
            else:
                result = await verify()
                assert result["assertion_verified"] is True
                assert result["materialization_authorized"] is False
                assert result["durable_consumption_unproven"] is True
                assert result["actor_revalidation_required"] is True
                assert len(calls) == 1
                if scenario == "rollback":
                    await tx.rollback()
                else:
                    await tx.commit()
                if scenario == "replay":
                    tx2 = conn.transaction()
                    await tx2.start()
                    with pytest.raises(webauthn.ReviewAssertionDenied):
                        await verify()
                    await tx2.rollback()
                    assert len(calls) == 2
            consumed = await conn.fetchval(
                "SELECT count(*) FROM "
                "ctt_staff_private.review_challenge_consumptions WHERE "
                "challenge_id=$1",
                challenge_id,
            )
            count = await conn.fetchval(
                "SELECT sign_count FROM "
                "ctt_staff_private.review_credentials WHERE "
                "credential_id=$1",
                credential_id,
            )
            committed = scenario in ("success", "replay")
            assert consumed == int(committed)
            assert count == (5 if committed else 4)
        finally:
            await clean(conn)
            await conn.close()

    asyncio.run(run())
