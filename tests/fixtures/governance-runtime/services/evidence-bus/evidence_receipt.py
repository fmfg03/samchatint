"""EvidenceReceipt.v1 issuance for the repository Evidence Bus."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def issue_evidence_receipt(
    event: Mapping[str, Any],
    key: Optional[bytes],
    *,
    kid: str,
    issuer: str = "zaubern-evidence-bus",
    alg: str = "HS256",
    ed25519_private_key: Optional[Ed25519PrivateKey] = None,
) -> Dict[str, Any]:
    semantic_event = {
        field: value
        for field, value in event.items()
        if field not in {"eis_sig", "execution_receipt_ref", "type"}
    }
    receipt: Dict[str, Any] = {
        "receipt_type": "EvidenceReceipt.v1",
        "event_hash": _sha256(semantic_event),
        "event_type": str(event.get("event_type") or event.get("type") or "unknown"),
        "tenant_id": str(event.get("tenant_id") or event.get("tenant") or "default"),
        "accepted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "verified": True,
        "issuer": issuer,
        "kid": kid,
        "alg": alg,
    }
    if alg == "HS256":
        if key is None or len(key) < 32:
            raise ValueError("HS256 receipt key is missing or too short")
        signature = hmac.new(key, _canonical(receipt), hashlib.sha256).digest()
    elif alg == "Ed25519":
        if ed25519_private_key is None:
            raise ValueError("Ed25519 receipt private key is missing")
        signature = ed25519_private_key.sign(_canonical(receipt))
    else:
        raise ValueError(f"unsupported EvidenceReceipt algorithm: {alg}")
    receipt["signature"] = base64.b64encode(signature).decode("ascii")
    receipt["receipt_id"] = _sha256(receipt)
    return receipt
