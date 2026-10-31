"""Cryptographic wire primitive for a candidate reviewer assertion.

A valid signature is not proof of human identity, faculty, source approval,
receipt verification, current expiry, single use, or authority to capture.
This primitive is not connected to an endpoint or an operational signer.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
import json
import re
from typing import Mapping, NoReturn
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_DOMAIN = b"samchat.ctt.review-assertion.v1\x00"
_DOMAIN_V2 = b"samchat.ctt.review-assertion.v2\x00"
PLATFORM_PROFILE = "platform-passkey-v1"
# Hash of the approved canonical policy definition, with a separate domain.
PLATFORM_POLICY_HASH = (
    "sha256:e0a2e1ce21182519a8739ffa11aba21164d565871bf62909cf9746861dfa48d3"
)
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


class AssertionProofDenied(ValueError):
    """Malformed or cryptographically invalid candidate assertion."""


def _deny() -> NoReturn:
    raise AssertionProofDenied("assertion proof denied") from None


@dataclass(frozen=True, repr=False)
class AssertionProof:
    attempt_id: UUID
    challenge_id: UUID
    principal_id: UUID
    credential_id: bytes = field(repr=False)
    scope_hash: str
    previous_counter: int
    new_counter: int
    expires_at: int

    def __post_init__(self) -> None:
        _validate_proof(self, allow_zero_pair=False)


def _validate_proof(proof, *, allow_zero_pair: bool) -> None:
    self = proof
    counters_valid = (
        type(self.previous_counter) is int
        and type(self.new_counter) is int
        and (
            0 <= self.previous_counter < self.new_counter <= 0xFFFFFFFF
            or (
                allow_zero_pair and self.previous_counter == 0 and self.new_counter == 0
            )
        )
    )
    if (
        any(
            type(v) is not UUID
            for v in (
                self.attempt_id,
                self.challenge_id,
                self.principal_id,
            )
        )
        or type(self.credential_id) is not bytes
        or not 1 <= len(self.credential_id) <= 1024
        or not isinstance(self.scope_hash, str)
        or not _SHA.fullmatch(self.scope_hash)
        or not counters_valid
        or type(self.expires_at) is not int
        or not 0 < self.expires_at <= 253402300799
    ):
        _deny()


@dataclass(frozen=True, repr=False)
class PlatformAssertionProof(AssertionProof):
    """Versioned platform proof; profile is pinned again in protected SQL."""

    auth_profile: str = PLATFORM_PROFILE
    backup_eligible: bool = False
    backup_state: bool = False
    auth_policy_hash: str = PLATFORM_POLICY_HASH

    def __post_init__(self) -> None:
        _validate_proof(self, allow_zero_pair=True)
        if (
            self.auth_profile != PLATFORM_PROFILE
            or self.auth_policy_hash != PLATFORM_POLICY_HASH
            or type(self.backup_eligible) is not bool
            or type(self.backup_state) is not bool
            or (self.backup_state and not self.backup_eligible)
        ):
            _deny()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _body(proof: AssertionProof, kid: str) -> dict:
    if (
        type(proof) not in (AssertionProof, PlatformAssertionProof)
        or not isinstance(kid, str)
        or not _KID.fullmatch(kid)
    ):
        _deny()
    body = {
        "kind": (
            "ctt.review-assertion.v2"
            if type(proof) is PlatformAssertionProof
            else "ctt.review-assertion.v1"
        ),
        "kid": kid,
        "attempt_id": str(proof.attempt_id),
        "challenge_id": str(proof.challenge_id),
        "principal_id": str(proof.principal_id),
        "credential_id": base64.b64encode(proof.credential_id).decode("ascii"),
        "scope_hash": proof.scope_hash,
        "previous_counter": proof.previous_counter,
        "new_counter": proof.new_counter,
        "expires_at": proof.expires_at,
    }
    if type(proof) is PlatformAssertionProof:
        body.update(
            auth_profile=proof.auth_profile,
            auth_policy_hash=proof.auth_policy_hash,
            backup_eligible=proof.backup_eligible,
            backup_state=proof.backup_state,
        )
    return body


def _domain(body):
    return _DOMAIN_V2 if body["kind"] == "ctt.review-assertion.v2" else _DOMAIN


def sign_assertion(
    proof: AssertionProof, *, kid: str, private_key: Ed25519PrivateKey
) -> bytes:
    """Sign a candidate; the caller establishes authority separately."""
    try:
        body = _body(proof, kid)
        signature = private_key.sign(_domain(body) + _canonical(body))
        return _canonical(
            {
                "body": body,
                "signature": base64.b64encode(signature).decode("ascii"),
            }
        )
    except Exception:
        _deny()


def verify_assertion(raw: bytes, *, public_keys: Mapping[str, bytes]) -> AssertionProof:
    """Check signature and format; caller verifies current authority."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _deny()
            result[key] = value
        return result

    def constant(_value):
        _deny()

    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= 8192
            or not isinstance(public_keys, Mapping)
        ):
            _deny()
        envelope = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
        )
        if type(envelope) is not dict or set(envelope) != {
            "body",
            "signature",
        }:
            _deny()
        body = envelope["body"]
        if type(body) is not dict or body.get("kind") not in (
            "ctt.review-assertion.v1",
            "ctt.review-assertion.v2",
        ):
            _deny()
        platform = body["kind"] == "ctt.review-assertion.v2"
        extra_fields = (
            {"auth_profile", "auth_policy_hash", "backup_eligible", "backup_state"}
            if platform
            else set()
        )
        if (
            set(body)
            != {
                "kind",
                "kid",
                "attempt_id",
                "challenge_id",
                "principal_id",
                "credential_id",
                "scope_hash",
                "previous_counter",
                "new_counter",
                "expires_at",
            }
            | extra_fields
        ):
            _deny()
        kid = body["kid"]
        if not isinstance(kid, str) or not _KID.fullmatch(kid):
            _deny()
        signature = base64.b64decode(envelope["signature"], validate=True)
        credential = base64.b64decode(body["credential_id"], validate=True)
        if (
            len(signature) != 64
            or base64.b64encode(signature).decode("ascii") != envelope["signature"]
        ):
            _deny()
        identifiers = [
            UUID(body[k]) for k in ("attempt_id", "challenge_id", "principal_id")
        ]
        if any(
            str(value) != body[key]
            for value, key in zip(
                identifiers, ("attempt_id", "challenge_id", "principal_id")
            )
        ):
            _deny()
        common = (
            identifiers[0],
            identifiers[1],
            identifiers[2],
            credential,
            body["scope_hash"],
            body["previous_counter"],
            body["new_counter"],
            body["expires_at"],
        )
        proof: AssertionProof
        if platform:
            proof = PlatformAssertionProof(
                *common,
                auth_profile=body["auth_profile"],
                auth_policy_hash=body["auth_policy_hash"],
                backup_eligible=body["backup_eligible"],
                backup_state=body["backup_state"],
            )
        else:
            proof = AssertionProof(*common)
        if _body(proof, kid) != body or _canonical(envelope) != raw:
            _deny()
        Ed25519PublicKey.from_public_bytes(public_keys[kid]).verify(
            signature, _domain(body) + _canonical(body)
        )
        return proof
    except Exception:
        _deny()


class SignedAssertionVerifier:
    """Cryptographic adapter; a trusted store must revalidate authority."""

    def __init__(self, public_keys: Mapping[str, bytes]):
        self._public_keys = dict(public_keys)

    def verify(self, signed_decision: bytes):
        from .staff_capture_executor import VerifiedDecision

        proof = verify_assertion(signed_decision, public_keys=self._public_keys)
        return VerifiedDecision(
            attempt_id=proof.attempt_id,
            challenge_id=proof.challenge_id,
            principal_id=proof.principal_id,
            credential_id=proof.credential_id,
            scope_hash=proof.scope_hash,
            prior_counter=proof.previous_counter,
            new_counter=proof.new_counter,
            expires_at=proof.expires_at,
            proof_sha256="sha256:" + hashlib.sha256(signed_decision).hexdigest(),
            auth_profile=getattr(proof, "auth_profile", "device-counter-v1"),
            auth_policy_hash=getattr(proof, "auth_policy_hash", None),
            backup_eligible=getattr(proof, "backup_eligible", False),
            backup_state=getattr(proof, "backup_state", False),
            proof_bytes=signed_decision,
        )
