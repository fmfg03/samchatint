import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_governance_receipt import (
    verify_staff_evidence_receipt,
)


@pytest.fixture
def signed_receipt():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    event = {
        "event_type": "samchat_registration_roster_decision_v2",
        "tenant_id": "test-tenant",
        "scope": "draft-1",
    }

    def canonical(value):
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()

    def digest(value):
        return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()

    receipt = {
        "receipt_type": "EvidenceReceipt.v1",
        "event_hash": digest(event),
        "event_type": event["event_type"],
        "tenant_id": event["tenant_id"],
        "accepted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "verified": True,
        "issuer": "zaubern-evidence-bus",
        "kid": "key-1",
        "alg": "Ed25519",
    }
    receipt["signature"] = base64.b64encode(private.sign(canonical(receipt))).decode(
        "ascii"
    )
    receipt["receipt_id"] = digest(receipt)
    return event, receipt, {"key-1": public}


def test_signed_exact_event(signed_receipt):
    event, receipt, keys = signed_receipt
    assert verify_staff_evidence_receipt(receipt, event, public_keys=keys)


@pytest.mark.parametrize(
    "field,value",
    [
        ("signature", "invalid"),
        ("receipt_id", "sha256:" + "0" * 64),
        ("kid", "unknown"),
        ("alg", "HS256"),
        ("tenant_id", "other"),
        ("verified", False),
        ("issuer", "other"),
        ("event_type", "other"),
        ("accepted_at", "2020-01-01T00:00:00Z"),
    ],
)
def test_rejects_modified_or_expired_receipt(signed_receipt, field, value):
    event, receipt, keys = signed_receipt
    assert not verify_staff_evidence_receipt(
        {**receipt, field: value}, event, public_keys=keys
    )


def test_rejects_changed_event_and_missing_key(signed_receipt):
    event, receipt, keys = signed_receipt
    assert not verify_staff_evidence_receipt(
        receipt, {**event, "scope": "draft-2"}, public_keys=keys
    )
    assert not verify_staff_evidence_receipt(receipt, event, public_keys={})


def test_rejects_future_receipt_and_extra_fields(signed_receipt):
    event, receipt, keys = signed_receipt
    earlier = datetime.now(timezone.utc) - timedelta(minutes=2)
    assert not verify_staff_evidence_receipt(
        receipt, event, public_keys=keys, now=earlier
    )
    assert not verify_staff_evidence_receipt(
        {**receipt, "extra": 1}, event, public_keys=keys
    )


@pytest.mark.parametrize(
    "receipt,event", [(None, {}), ({}, None), ([], []), ("bad", {})]
)
def test_malformed_input_fails_closed(receipt, event):
    assert not verify_staff_evidence_receipt(receipt, event, public_keys={})
