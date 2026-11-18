"""Verify private gate authorization of both capture commitments.

Only a separately accredited gate may hold the signing key. This module has
no signer or endpoint and cannot accredit a reviewer, source or producer.
The writer must still revalidate current state and consume the protected nonce
in its own transaction; this check does not persist or activate any rows.
"""

import base64
import hashlib
import hmac
import json
from typing import Mapping
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .staff_capture_effect import (
    bind_capture_effect,
    bind_review_plan,
    check_effect_follows_review,
)
from .staff_capture_executor import CaptureDenied
from .staff_review_challenge import ReviewScope, scope_hash

DOMAIN = b"ctt.capture.authorization.v1\x00"


def canonical_authorization(value: Mapping) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def verify_capture_authorization(
    *,
    envelope: bytes,
    public_keys: Mapping[str, bytes],
    scope: ReviewScope,
    attempt_id: UUID,
    nonce: UUID,
    review_bytes: bytes,
    effect_bytes: bytes,
    review_key: bytes,
    effect_key: bytes,
    now_seconds: int,
    member_proof_bytes: bytes | None = None,
) -> str:
    """Check pinned gate signature, expiry, complete scope and exact effect.

    attempt_id and nonce must come from locked protected state, not the caller
    browser. Return only the signed authorization hash for private audit.
    """

    from .staff_batch_authorization import (
        CAPTURE_SCHEMA,
        BatchReviewScope,
        batch_scope_hash,
        verify_batch_proof,
    )

    if type(scope) is BatchReviewScope:
        from .staff_capture_effect import check_batch_effect_follows_review

        try:
            body = verify_batch_proof(
                envelope,
                schema=CAPTURE_SCHEMA,
                public_keys=public_keys,
                now=now_seconds,
            )
            if (
                type(member_proof_bytes) is not bytes
                or not member_proof_bytes
                or body["member_proof_hash"]
                != hashlib.sha256(member_proof_bytes).hexdigest()
                or body["attempt_id"] != str(attempt_id)
                or body["nonce"] != str(nonce)
                or body["scope_hash"] != batch_scope_hash(scope)
                or body["review_plan_binding"]
                != bind_review_plan(review_bytes, review_key)
                or body["effect_binding"]
                != bind_capture_effect(effect_bytes, effect_key)
            ):
                raise ValueError("batch binding mismatch")
            check_batch_effect_follows_review(
                review_bytes=review_bytes,
                effect_bytes=effect_bytes,
                scope=scope,
                review_key=review_key,
                authority_metadata=body["authority_metadata"],
            )
            return "sha256:" + hashlib.sha256(envelope).hexdigest()
        except Exception:
            raise CaptureDenied("capture denied") from None
    if member_proof_bytes is not None:
        raise CaptureDenied("capture denied")

    def pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = child
        return value

    try:
        if type(envelope) is not bytes or not 0 < len(envelope) <= 262144:
            raise ValueError("invalid envelope")
        obj = json.loads(envelope, object_pairs_hook=pairs)
        if type(obj) is not dict or set(obj) != {"body", "signature"}:
            raise ValueError("invalid envelope")
        body = obj["body"]
        expected_keys = {
            "schema",
            "kid",
            "scope_hash",
            "attempt_id",
            "nonce",
            "review_plan_binding",
            "effect_binding",
            "issued_at",
            "expires_at",
            "authority_metadata",
        }
        if type(body) is not dict or set(body) != expected_keys:
            raise ValueError("invalid body")
        if (
            type(scope) is not ReviewScope
            or not isinstance(attempt_id, UUID)
            or not isinstance(nonce, UUID)
            or type(now_seconds) is not int
            or body["schema"] != "ctt.capture.authorization.v1"
            or body["scope_hash"] != scope_hash(scope)
            or body["attempt_id"] != str(attempt_id)
            or body["nonce"] != str(nonce)
            or type(body["issued_at"]) is not int
            or type(body["expires_at"]) is not int
            or not 0 < body["expires_at"] - body["issued_at"] <= 300
            or not body["issued_at"] <= now_seconds < body["expires_at"]
            or type(body["kid"]) is not str
            or not body["kid"]
            or type(body["authority_metadata"]) is not dict
        ):
            raise ValueError("scope or expiry mismatch")
        if canonical_authorization(obj) != envelope:
            raise ValueError("noncanonical envelope")
        signature = base64.b64decode(obj["signature"], validate=True)
        if (
            len(signature) != 64
            or base64.b64encode(signature).decode() != obj["signature"]
        ):
            raise ValueError("invalid signature")
        Ed25519PublicKey.from_public_bytes(public_keys[body["kid"]]).verify(
            signature, DOMAIN + canonical_authorization(body)
        )
        if not hmac.compare_digest(
            body["review_plan_binding"],
            bind_review_plan(review_bytes, review_key),
        ):
            raise ValueError("plan mismatch")
        if not hmac.compare_digest(
            body["effect_binding"],
            bind_capture_effect(effect_bytes, effect_key),
        ):
            raise ValueError("effect mismatch")
        check_effect_follows_review(
            review_bytes=review_bytes,
            effect_bytes=effect_bytes,
            scope=scope,
            review_key=review_key,
            authority_metadata=body["authority_metadata"],
        )
        return "sha256:" + hashlib.sha256(envelope).hexdigest()
    except Exception:
        raise CaptureDenied("capture denied") from None
