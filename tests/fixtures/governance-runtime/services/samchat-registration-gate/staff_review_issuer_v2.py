"""Issue an identity assertion from protected PostgreSQL and WebAuthn state.

This internal gate component does not consume the challenge, enroll people,
verify business receipts, or authorize materialization. Its caller must use an
authenticated channel. The writer still revalidates and consumes the exact
assertion together with the independently authorized capture effect.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, NoReturn
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_review_challenge import (
    parse_canonical_scope,
    scope_hash,
)
from devnous.copa_telmex.staff_signed_assertion import (
    AssertionProof,
    PLATFORM_POLICY_HASH,
    PLATFORM_PROFILE,
    PlatformAssertionProof,
    sign_assertion,
)
from staff_review_webauthn_v2 import (
    MAX_RESPONSE_BYTES,
    ReviewChallenge,
    _bounded_json,
    _limit_json_text_depth,
    _validate_review_assertion,
)


class ReviewIssuanceDenied(ValueError):
    """The protected identity assertion could not be issued."""


def _deny() -> NoReturn:
    raise ReviewIssuanceDenied("staff review issuance denied") from None


def _response(raw: bytes):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
        _deny()
    _limit_json_text_depth(raw)

    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                _deny()
            result[name] = value
        return result

    def constant(_value):
        _deny()

    response = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=constant,
    )
    if type(response) is not dict or set(response) - {
        "id",
        "rawId",
        "response",
        "type",
        "clientExtensionResults",
        "authenticatorAttachment",
    }:
        _deny()
    _bounded_json(response, max_bytes=MAX_RESPONSE_BYTES)
    return response


async def _check_connection(connection):
    if not connection.is_in_transaction():
        _deny()
    if await connection.fetchval("SHOW max_prepared_transactions") != "0":
        _deny()
    role = await connection.fetchrow(
        "SELECT current_user AS name,session_user AS session_name,"
        "rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls,"
        "pg_catalog.pg_has_role(current_user,"
        "'samchat_ctt_staff_owner','MEMBER') AS private_owner_member,"
        "(pg_catalog.pg_has_role('devnous_user',current_user,'MEMBER') OR "
        "pg_catalog.pg_has_role(current_user,'devnous_user','MEMBER')) "
        "AS app_role_related,"
        "EXISTS(SELECT 1 FROM pg_catalog.pg_roles broad_role "
        "WHERE broad_role.rolname IN ('pg_read_all_data','pg_write_all_data',"
        "'pg_read_server_files','pg_write_server_files','pg_execute_server_program') "
        "AND pg_catalog.pg_has_role(current_user,broad_role.oid,'MEMBER')) "
        "AS broad_privileges "
        "FROM pg_catalog.pg_roles WHERE rolname=current_user"
    )
    if (
        role is None
        or role["name"] == "devnous_user"
        or role["name"] != role["session_name"]
        or any(
            role[key] is not False
            for key in (
                "rolsuper",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "rolbypassrls",
                "private_owner_member",
                "app_role_related",
                "broad_privileges",
            )
        )
    ):
        _deny()


def _state(context, attempt_id):
    if context is None or context["attempt_id"] != attempt_id:
        _deny()
    scope = parse_canonical_scope(context["scope_bytes"])
    if (
        scope_hash(scope) != context["scope_hash"]
        or scope.actor_id != context["principal_id"]
        or type(context["credential_id"]) is not bytes
        or not 1 <= len(context["credential_id"]) <= 1024
        or type(context["backup_eligible"]) is not bool
        or type(context["backup_state"]) is not bool
        or (context["backup_state"] and not context["backup_eligible"])
    ):
        _deny()
    profile = context["auth_profile"]
    if profile == PLATFORM_PROFILE:
        if context["auth_policy_hash"] != PLATFORM_POLICY_HASH:
            _deny()
    elif profile == "device-counter-v1":
        if (
            context["auth_policy_hash"] is not None
            or context["backup_eligible"]
            or context["backup_state"]
        ):
            _deny()
    else:
        _deny()
    return ReviewChallenge(
        challenge_id=str(context["challenge_id"]),
        principal_id=str(context["principal_id"]),
        scope_hash=context["scope_hash"],
        challenge=context["challenge_bytes"],
        issued_at=context["issued_at_seconds"],
        expires_at=context["expires_at_seconds"],
        credential=context["attested_credential"],
        user_handle=context["user_handle"],
        counter=context["sign_count"],
        auth_profile=profile,
        backup_eligible=context["backup_eligible"],
    )


class PostgresReviewAssertionIssuer:
    """A configured gate signer; never construct it from request values."""

    def __init__(
        self,
        pool,
        *,
        signing_kid: str,
        signing_key: Ed25519PrivateKey,
        retain_proof: bool = False,
    ):
        if (
            type(signing_kid) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", signing_kid) is None
            or not isinstance(signing_key, Ed25519PrivateKey)
            or type(retain_proof) is not bool
        ):
            _deny()
        self._pool = pool
        self._kid, self._key = signing_kid, signing_key
        self._retain_proof = retain_proof

    def require_proof_retention(self) -> None:
        """Fail closed when a proposal workflow lacks atomic proof retention."""
        if self._retain_proof is not True:
            _deny()

    def validate_channel_key(self, channel_key: bytes) -> None:
        """Validate trusted channel configuration without exposing a secret."""
        if type(channel_key) is not bytes or len(channel_key) < 32:
            _deny()
        if hmac.compare_digest(channel_key, self._key.private_bytes_raw()):
            _deny()

    async def issue(self, attempt_id: UUID, response_bytes: bytes) -> bytes:
        """Return a signed identity proof only after its audit commits.

        The loader checks current faculty, versions, revocation and expiry
        under locks. Issuance does not consume the challenge; the independently
        configured writer performs that consumption with the business effect.
        """
        try:
            if not isinstance(attempt_id, UUID):
                _deny()
            response = _response(response_bytes)
            async with self._pool.acquire() as connection:
                # A nested transaction would release only a savepoint and
                # could expose the proof before its audit actually commits.
                if connection.is_in_transaction() is not False:
                    _deny()
                async with connection.transaction():
                    await _check_connection(connection)
                    context = await connection.fetchrow(
                        "SELECT * FROM "
                        "ctt_staff_private.load_review_assertion_context($1)",
                        attempt_id,
                    )
                    state = _state(context, attempt_id)
                    verified = _validate_review_assertion(
                        state=state,
                        expected_principal_id=state.principal_id,
                        expected_scope_hash=state.scope_hash,
                        response=response,
                        now=int(time.time()),
                    )
                    if not hmac.compare_digest(
                        verified.credential_id, context["credential_id"]
                    ):
                        _deny()
                    common: dict[str, Any] = dict(
                        attempt_id=UUID(str(attempt_id)),
                        challenge_id=UUID(verified.challenge_id),
                        principal_id=UUID(verified.principal_id),
                        credential_id=verified.credential_id,
                        scope_hash=verified.scope_hash,
                        previous_counter=verified.previous_counter,
                        new_counter=verified.new_counter,
                        expires_at=verified.expires_at,
                    )
                    proof: AssertionProof
                    if verified.auth_profile == PLATFORM_PROFILE:
                        if verified.auth_policy_hash != PLATFORM_POLICY_HASH:
                            _deny()
                        proof = PlatformAssertionProof(
                            **common,
                            auth_profile=verified.auth_profile,
                            auth_policy_hash=PLATFORM_POLICY_HASH,
                            backup_eligible=verified.backup_eligible,
                            backup_state=verified.backup_state,
                        )
                    elif verified.auth_profile == "device-counter-v1":
                        if (
                            verified.auth_policy_hash is not None
                            or verified.backup_eligible is not False
                            or verified.backup_state is not False
                        ):
                            _deny()
                        proof = AssertionProof(**common)
                    else:
                        _deny()
                    signed = sign_assertion(proof, kid=self._kid, private_key=self._key)
                    recorded = await connection.fetchval(
                        "SELECT ctt_staff_private.record_review_assertion_issuance("
                        "$1,$2,$3,$4,$5,$6,$7,$8,$9)",
                        attempt_id,
                        "sha256:" + hashlib.sha256(signed).hexdigest(),
                        verified.previous_counter,
                        verified.new_counter,
                        verified.auth_profile,
                        verified.backup_eligible,
                        verified.backup_state,
                        verified.auth_policy_hash,
                        verified.expires_at,
                    )
                    if recorded is not True:
                        _deny()
                    if self._retain_proof:
                        retained = await connection.fetchval(
                            "SELECT ctt_staff_private.record_assertion_proof($1,$2)",
                            attempt_id,
                            signed,
                        )
                        if retained is not True:
                            _deny()
            return signed
        except Exception:
            _deny()
