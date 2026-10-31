"""Both commitments must be signed by the trusted gate key together."""

import base64
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_review_challenge import scope_hash
from test_ctt_staff_capture_effect import lifecycle


def authorization():
    args, plan, review_key, metadata = lifecycle()
    effect = freeze_capture_effect(**args)
    attempt, nonce = uuid4(), uuid4()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    effect_key = b"independent-effect-key-at-least-32-bytes"
    body = {
        "schema": "ctt.capture.authorization.v1",
        "kid": "synthetic-gate",
        "scope_hash": scope_hash(args["scope"]),
        "attempt_id": str(attempt),
        "nonce": str(nonce),
        "review_plan_binding": bind_review_plan(plan, review_key),
        "effect_binding": bind_capture_effect(effect, effect_key),
        "issued_at": 1000,
        "expires_at": 1100,
        "authority_metadata": metadata,
    }

    def sign(value):
        return canonical_authorization(
            {
                "body": value,
                "signature": base64.b64encode(
                    key.sign(DOMAIN + canonical_authorization(value))
                ).decode(),
            }
        )

    kwargs = dict(
        envelope=sign(body),
        public_keys={"synthetic-gate": public},
        scope=args["scope"],
        attempt_id=attempt,
        nonce=nonce,
        review_bytes=plan,
        effect_bytes=effect,
        review_key=review_key,
        effect_key=effect_key,
        now_seconds=1050,
    )
    return kwargs, body, sign


def test_pinned_gate_signature_binds_plan_effect_and_receipts():
    kwargs, _, _ = authorization()
    assert verify_capture_authorization(**kwargs).startswith("sha256:")


@pytest.mark.parametrize(
    "field",
    [
        "effect_binding",
        "review_plan_binding",
        "scope_hash",
        "nonce",
        "attempt_id",
        "authority_metadata",
    ],
)
def test_unsigned_changes_fail(field):
    kwargs, _, _ = authorization()
    envelope = json.loads(kwargs["envelope"])
    envelope["body"][field] = {} if field == "authority_metadata" else "changed"
    kwargs["envelope"] = canonical_authorization(envelope)
    with pytest.raises(CaptureDenied):
        verify_capture_authorization(**kwargs)


@pytest.mark.parametrize(
    "change", ["expired", "future", "nonce", "attempt", "key", "effect"]
)
def test_current_protected_context_and_bytes_must_match(change):
    kwargs, _, _ = authorization()
    if change == "expired":
        kwargs["now_seconds"] = 1100
    elif change == "future":
        kwargs["now_seconds"] = 999
    elif change == "nonce":
        kwargs["nonce"] = uuid4()
    elif change == "attempt":
        kwargs["attempt_id"] = uuid4()
    elif change == "key":
        kwargs["public_keys"] = {}
    else:
        kwargs["effect_bytes"] += b" "
    with pytest.raises(CaptureDenied):
        verify_capture_authorization(**kwargs)


def test_even_signed_wrong_effect_commitment_is_denied():
    kwargs, body, sign = authorization()
    body["effect_binding"] = "hmac-sha256:" + "0" * 64
    kwargs["envelope"] = sign(body)
    with pytest.raises(CaptureDenied):
        verify_capture_authorization(**kwargs)
