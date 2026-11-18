"""Protected batch consent issuer. No enrollment, source admission or app pool.

WebAuthn is verified once for a frozen parent. Signed child authorizations are
another proof kind, with no invented WebAuthn counters. Protected SQL retains
proofs, consumes the genuine assertion, activates qualification and fences
workers. No proof leaves this component before its transaction commits.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from uuid import UUID, uuid4

from devnous.copa_telmex.staff_batch_authorization import (
    CONSENT_SCHEMA,
    MEMBER_SCHEMA,
    VERIFICATION_SCHEMA,
    BatchAuthorizationDenied,
    batch_scope_hash,
    parse_batch_scope,
    sign_batch_proof,
    verify_batch_proof,
)
from devnous.copa_telmex.staff_batch_contracts import canonical, strict_loads
from staff_review_issuer_v2 import _check_connection, _response
from staff_review_webauthn_v2 import ReviewChallenge, _validate_review_assertion


def deny():
    raise BatchAuthorizationDenied("batch issuance denied") from None


def _context(raw):
    value = json.loads(raw) if type(raw) is str else raw
    if type(value) is not dict:
        deny()
    value = dict(value)
    if "authority_deadline" in value:
        value["authority_deadline_seconds"] = value["authority_deadline"]
    for name in (
        "root_bytes",
        "credential_id",
        "attested_credential",
        "user_handle",
        "challenge_bytes",
        "proof_bytes",
        "scope_bytes",
        "parent_proof_bytes",
        "verification_proof_bytes",
    ):
        data = value.get(name)
        if type(data) is str:
            try:
                value[name] = base64.b64decode(data, validate=True)
            except Exception:
                deny()
    return value


class PostgresBatchAssertionIssuer:
    def __init__(self, pool, *, signing_kid, signing_key, verifier_public_keys, clock=None):
        import re

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        if (
            type(signing_kid) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", signing_kid)
            or not isinstance(signing_key, Ed25519PrivateKey)
        ):
            deny()
        if not verifier_public_keys or any(
            type(k) is not str or type(v) is not bytes or len(v) != 32
            for k, v in verifier_public_keys.items()
        ):
            deny()
        self._pool = pool
        self._kid, self._key = signing_kid, signing_key
        self._verifier_keys = dict(verifier_public_keys)
        self._issuer_keys = {signing_kid: signing_key.public_key().public_bytes_raw()}
        self._clock = clock or time.time

    async def issue_consent(self, challenge_id: UUID, response_bytes: bytes) -> bytes:
        try:
            if type(challenge_id) is not UUID:
                deny()
            response = _response(response_bytes)
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    deny()
                async with conn.transaction():
                    await _check_connection(conn)
                    context = _context(
                        await conn.fetchval(
                            "SELECT ctt_staff_private.batch_load_consent_context($1)", challenge_id
                        )
                    )
                    now = int(self._clock())
                    if context.get("proof_bytes"):
                        signed = context["proof_bytes"]
                        body = verify_batch_proof(
                            signed, schema=CONSENT_SCHEMA, public_keys=self._issuer_keys, now=now
                        )
                        if (
                            body["challenge_id"] != str(challenge_id)
                            or body["root_hash"] != context["root_hash"]
                        ):
                            deny()
                    else:
                        root = strict_loads(context["root_bytes"])
                        if (
                            canonical(root) != context["root_bytes"]
                            or hashlib.sha256(context["root_bytes"]).hexdigest()
                            != context["root_hash"]
                        ):
                            deny()
                        state = ReviewChallenge(
                            challenge_id=str(challenge_id),
                            principal_id=str(context["principal_id"]),
                            scope_hash="sha256:" + context["root_hash"],
                            challenge=context["challenge_bytes"],
                            issued_at=context["issued_at_seconds"],
                            expires_at=context["expires_at_seconds"],
                            credential=context["attested_credential"],
                            user_handle=context["user_handle"],
                            counter=context["sign_count"],
                            auth_profile=context["auth_profile"],
                            backup_eligible=context["backup_eligible"],
                        )
                        verified = _validate_review_assertion(
                            state=state,
                            expected_principal_id=state.principal_id,
                            expected_scope_hash=state.scope_hash,
                            response=response,
                            now=now,
                        )
                        if verified.credential_id != context["credential_id"]:
                            deny()
                        expires = min(now + 3600, int(context["authority_deadline_seconds"]))
                        body = {
                            "schema": CONSENT_SCHEMA,
                            "kid": self._kid,
                            "consent_id": str(context["consent_id"]),
                            "challenge_id": str(challenge_id),
                            "principal_id": str(context["principal_id"]),
                            "credential_id": base64.b64encode(verified.credential_id).decode(),
                            "root_hash": context["root_hash"],
                            "qualification_hash": context["qualification_hash"],
                            **{
                                name: context[name]
                                for name in (
                                    "capability_version",
                                    "credential_version",
                                    "principal_version",
                                    "account_version",
                                    "service_version",
                                )
                            },
                            "counter_before": verified.previous_counter,
                            "counter_after": verified.new_counter,
                            "auth_profile": verified.auth_profile,
                            "backup_eligible": verified.backup_eligible,
                            "backup_state": verified.backup_state,
                            "auth_policy_hash": verified.auth_policy_hash,
                            "issued_at": now,
                            "expires_at": expires,
                        }
                        signed = sign_batch_proof(body, signing_key=self._key)
                        result = _context(
                            await conn.fetchval(
                                "SELECT ctt_staff_private.batch_register_consent($1,$2)",
                                challenge_id,
                                signed,
                            )
                        )
                        if (
                            result.get("consent_id") != body["consent_id"]
                            or result.get("proof_hash") != hashlib.sha256(signed).hexdigest()
                        ):
                            deny()
            return signed
        except Exception:
            deny()

    async def issue_child(
        self, consent_id: UUID, member_id: UUID, worker_id: UUID, lease_epoch: int
    ) -> bytes:
        try:
            if (
                any(type(v) is not UUID for v in (consent_id, member_id, worker_id))
                or type(lease_epoch) is not int
                or lease_epoch < 1
            ):
                deny()
            async with self._pool.acquire() as conn:
                if conn.is_in_transaction():
                    deny()
                async with conn.transaction():
                    await _check_connection(conn)
                    context = _context(
                        await conn.fetchval(
                            "SELECT ctt_staff_private.batch_load_child_context($1,$2,$3,$4)",
                            consent_id,
                            member_id,
                            worker_id,
                            lease_epoch,
                        )
                    )
                    now = int(self._clock())
                    parent = verify_batch_proof(
                        context["parent_proof_bytes"],
                        schema=CONSENT_SCHEMA,
                        public_keys=self._issuer_keys,
                        now=now,
                    )
                    verification = verify_batch_proof(
                        context["verification_proof_bytes"],
                        schema=VERIFICATION_SCHEMA,
                        public_keys=self._verifier_keys,
                        now=now,
                    )
                    scope = parse_batch_scope(context["scope_bytes"])
                    if (
                        parent["consent_id"] != str(consent_id)
                        or parent["principal_id"] != str(scope.actor_id)
                        or scope.member_id != member_id
                        or batch_scope_hash(scope) != context["scope_hash"]
                        or verification["scope_hash"] != context["scope_hash"]
                        or verification["qualification_hash"] != parent["qualification_hash"]
                        or verification["document_id"] != scope.document_id
                        or verification["manifest_sha256"] != scope.manifest_sha256
                        or verification["policy_hash"] != scope.policy_hash
                        or hashlib.sha256(context["verification_proof_bytes"]).hexdigest()
                        != context["verification_hash"]
                        or context.get("qualification_active") is not True
                    ):
                        deny()
                    if context.get("proof_bytes"):
                        signed = context["proof_bytes"]
                        body = verify_batch_proof(
                            signed, schema=MEMBER_SCHEMA, public_keys=self._issuer_keys, now=now
                        )
                        if (
                            body["consent_id"] != str(consent_id)
                            or body["member_id"] != str(member_id)
                            or body["worker_id"] != str(worker_id)
                            or body["lease_epoch"] != lease_epoch
                            or body["authorization_generation"]
                            != context["authorization_generation"]
                            or body["dispatch_generation"] != context["dispatch_generation"]
                        ):
                            deny()
                    else:
                        body = {
                            "schema": MEMBER_SCHEMA,
                            "kid": self._kid,
                            "authorization_id": str(uuid4()),
                            "consent_id": str(consent_id),
                            "member_id": str(member_id),
                            "attempt_id": str(context["attempt_id"]),
                            "operation_id": str(scope.operation_id),
                            "principal_id": str(scope.actor_id),
                            "scope_hash": context["scope_hash"],
                            "root_hash": parent["root_hash"],
                            "qualification_hash": parent["qualification_hash"],
                            "verification_hash": context["verification_hash"],
                            "authorization_generation": context["authorization_generation"],
                            "worker_id": str(worker_id),
                            "lease_epoch": lease_epoch,
                            "dispatch_generation": context["dispatch_generation"],
                            "nonce": str(uuid4()),
                            "issued_at": now,
                            "expires_at": min(
                                now + 300,
                                parent["expires_at"],
                                verification["expires_at"],
                                int(context["authority_deadline_seconds"]),
                            ),
                        }
                        signed = sign_batch_proof(body, signing_key=self._key)
                        result = _context(
                            await conn.fetchval(
                                "SELECT ctt_staff_private.batch_issue_child($1,$2,$3,$4,$5)",
                                consent_id,
                                member_id,
                                worker_id,
                                lease_epoch,
                                signed,
                            )
                        )
                        if (
                            result.get("authorization_id") != body["authorization_id"]
                            or result.get("proof_hash") != hashlib.sha256(signed).hexdigest()
                        ):
                            deny()
            return signed
        except Exception:
            deny()
