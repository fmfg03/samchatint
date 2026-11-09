"""Cryptographic proof format tests; no actual authority is conferred."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_signed_assertion import (
    AssertionProof,
    AssertionProofDenied,
    PLATFORM_POLICY_HASH,
    PlatformAssertionProof,
    SignedAssertionVerifier,
    sign_assertion,
    verify_assertion,
)


def sample():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    proof = AssertionProof(
        uuid4(),
        uuid4(),
        uuid4(),
        b"synthetic",
        "sha256:" + "a" * 64,
        4,
        5,
        1900000000,
    )
    return key, public, proof


def test_roundtrip_signature_and_binding():
    key, public, proof = sample()
    raw = sign_assertion(proof, kid="test", private_key=key)
    assert verify_assertion(raw, public_keys={"test": public}) == proof
    assert "synthetic" not in repr(proof)


@pytest.mark.parametrize(
    "field",
    [
        "attempt_id",
        "challenge_id",
        "principal_id",
        "credential_id",
        "scope_hash",
        "previous_counter",
        "new_counter",
        "expires_at",
        "kid",
    ],
)
def test_changed_signed_fields_fail(field):
    key, public, proof = sample()
    value = json.loads(sign_assertion(proof, kid="test", private_key=key))
    changed = {
        "attempt_id": str(uuid4()),
        "challenge_id": str(uuid4()),
        "principal_id": str(uuid4()),
        "credential_id": "YQ==",
        "scope_hash": "sha256:" + "b" * 64,
        "previous_counter": 3,
        "new_counter": 6,
        "expires_at": 1900000001,
        "kid": "other",
    }
    value["body"][field] = changed[field]
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(AssertionProofDenied):
        verify_assertion(raw, public_keys={"test": public, "other": public})


def test_wrong_key_duplicate_json_and_noncanonical_are_rejected():
    key, public, proof = sample()
    raw = sign_assertion(proof, kid="test", private_key=key)
    _, other, _ = sample()
    for malformed in (raw + b" ", b'{"body":{},"body":{},"signature":""}'):
        with pytest.raises(AssertionProofDenied):
            verify_assertion(malformed, public_keys={"test": public})
    with pytest.raises(AssertionProofDenied):
        verify_assertion(raw, public_keys={"test": other})
    with pytest.raises(AssertionProofDenied):
        sign_assertion(
            replace(proof, new_counter=proof.previous_counter),
            kid="test",
            private_key=key,
        )


def test_verified_proof_maps_all_fields_to_executor():
    from devnous.copa_telmex.staff_signed_assertion import (
        SignedAssertionVerifier,
    )

    key, public, proof = sample()
    raw = sign_assertion(proof, kid="test", private_key=key)
    decision = SignedAssertionVerifier({"test": public}).verify(raw)
    assert decision.proof_bytes == raw
    assert "proof_bytes" not in repr(decision)
    assert decision.attempt_id == proof.attempt_id
    assert decision.challenge_id == proof.challenge_id
    assert decision.principal_id == proof.principal_id
    assert decision.credential_id == proof.credential_id
    assert decision.scope_hash == proof.scope_hash
    assert decision.prior_counter == proof.previous_counter
    assert decision.new_counter == proof.new_counter
    assert decision.expires_at == proof.expires_at


def platform_sample(**changes):
    key, public, previous = sample()
    values = vars(previous) | dict(
        previous_counter=0,
        new_counter=0,
        backup_eligible=True,
        backup_state=True,
    )
    return key, public, PlatformAssertionProof(**(values | changes))


def test_platform_zero_counter_roundtrip_and_protected_metadata():
    key, public, proof = platform_sample()
    raw = sign_assertion(proof, kid="test", private_key=key)
    assert json.loads(raw)["body"]["kind"] == "ctt.review-assertion.v2"
    assert verify_assertion(raw, public_keys={"test": public}) == proof
    decision = SignedAssertionVerifier({"test": public}).verify(raw)
    assert decision.auth_profile == "platform-passkey-v1"
    assert decision.auth_policy_hash == PLATFORM_POLICY_HASH
    assert decision.backup_eligible is True
    assert decision.backup_state is True
    assert decision.prior_counter == decision.new_counter == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"previous_counter": 3, "new_counter": 0},
        {"previous_counter": 3, "new_counter": 3},
        {"previous_counter": 3, "new_counter": 2},
        {"previous_counter": False},
        {"new_counter": True},
        {"new_counter": 0x100000000},
        {"auth_profile": "device-counter-v1"},
        {"auth_profile": "browser-profile"},
        {"auth_policy_hash": "sha256:" + "a" * 64},
        {"backup_eligible": False},
        {"backup_eligible": 1},
        {"backup_state": 1},
    ],
)
def test_platform_rejects_counter_and_policy_anomalies(changes):
    with pytest.raises(AssertionProofDenied):
        platform_sample(**changes)


@pytest.mark.parametrize("previous,current", [(0, 1), (1, 2), (0, 0)])
def test_platform_allows_counterless_and_increasing_proofs(previous, current):
    key, public, proof = platform_sample(previous_counter=previous, new_counter=current)
    assert (
        verify_assertion(
            sign_assertion(proof, kid="test", private_key=key),
            public_keys={"test": public},
        )
        == proof
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("auth_profile", "device-counter-v1"),
        ("auth_policy_hash", "sha256:" + "b" * 64),
        ("backup_eligible", False),
        ("backup_state", False),
        ("kind", "ctt.review-assertion.v1"),
    ],
)
def test_platform_signature_binds_profile_backup_state_and_version(field, value):
    key, public, proof = platform_sample()
    envelope = json.loads(sign_assertion(proof, kid="test", private_key=key))
    envelope["body"][field] = value
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(AssertionProofDenied):
        verify_assertion(raw, public_keys={"test": public})


def test_platform_and_device_signature_domains_cannot_be_substituted():
    key, public, proof = platform_sample(previous_counter=0, new_counter=1)
    envelope = json.loads(sign_assertion(proof, kid="test", private_key=key))
    for name in ("auth_profile", "auth_policy_hash", "backup_eligible", "backup_state"):
        del envelope["body"][name]
    envelope["body"]["kind"] = "ctt.review-assertion.v1"
    # Even a valid signature for the same JSON under the v2 domain must not
    # be usable as a device assertion under the existing v1 domain.
    import base64

    body = json.dumps(
        envelope["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    envelope["signature"] = base64.b64encode(
        key.sign(b"samchat.ctt.review-assertion.v2\x00" + body)
    ).decode("ascii")
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(AssertionProofDenied):
        verify_assertion(raw, public_keys={"test": public})


def test_platform_policy_hash_matches_canonical_approved_definition():
    import hashlib

    definition = {
        "backup_eligibility": "pinned",
        "backup_state": "signed-change-audited",
        "challenge": "32-byte-single-use-300s-max",
        "counter": "zero-pair-or-strict-increase",
        "id": "platform-passkey-v1",
        "user_verification": "required",
    }
    raw = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    assert (
        PLATFORM_POLICY_HASH
        == "sha256:"
        + hashlib.sha256(b"samchat.ctt.auth-policy.v1\x00" + raw).hexdigest()
    )
