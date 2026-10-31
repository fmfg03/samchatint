"""Verify signed EvidenceReceipt.v1 for proposed staff governance events.

This helper does not issue authority or alter the existing registration flow.
Callers must additionally verify the event's complete business scope.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def verify_staff_evidence_receipt(
    receipt: Any,
    event: Any,
    *,
    public_keys: Mapping[str, bytes],
    now: Optional[datetime] = None,
    max_age_seconds: int = 60,
) -> bool:
    """Verify a receipt against the exact event and trusted Ed25519 keys."""
    if not isinstance(receipt, Mapping) or not isinstance(event, Mapping):
        return False
    required = {
        "receipt_type",
        "receipt_id",
        "event_hash",
        "event_type",
        "tenant_id",
        "accepted_at",
        "verified",
        "issuer",
        "kid",
        "alg",
        "signature",
    }
    if (
        set(receipt) != required
        or receipt.get("receipt_type") != "EvidenceReceipt.v1"
        or receipt.get("verified") is not True
        or receipt.get("issuer") != "zaubern-evidence-bus"
        or receipt.get("alg") != "Ed25519"
        or not isinstance(receipt.get("kid"), str)
        or not receipt["kid"]
        or receipt.get("event_type") != event.get("event_type")
        or receipt.get("tenant_id") != event.get("tenant_id")
        or not isinstance(event.get("event_type"), str)
        or not isinstance(event.get("tenant_id"), str)
        or not event["event_type"]
        or not event["tenant_id"]
        or type(max_age_seconds) is not int
        or max_age_seconds < 0
    ):
        return False
    try:
        current = now or datetime.now(timezone.utc)
        accepted = datetime.fromisoformat(
            str(receipt["accepted_at"]).replace("Z", "+00:00")
        )
        if current.tzinfo is None or accepted.tzinfo is None:
            return False
        if (
            abs(
                (
                    current.astimezone(timezone.utc) - accepted.astimezone(timezone.utc)
                ).total_seconds()
            )
            > max_age_seconds
        ):
            return False
        if not hmac.compare_digest(str(receipt["event_hash"]), _digest(event)):
            return False
        signed = {
            k: v for k, v in receipt.items() if k not in {"receipt_id", "signature"}
        }
        receipt_without_id = {k: v for k, v in receipt.items() if k != "receipt_id"}
        if not hmac.compare_digest(
            str(receipt["receipt_id"]), _digest(receipt_without_id)
        ):
            return False
        signature = base64.b64decode(str(receipt["signature"]), validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_keys[receipt["kid"]])
        public_key.verify(signature, _canonical(signed))
        return True
    except (KeyError, TypeError, ValueError, InvalidSignature, OverflowError):
        return False
