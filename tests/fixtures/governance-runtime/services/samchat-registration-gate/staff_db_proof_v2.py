"""Non-authoritative encoding and verification primitives for staff proofs.

No endpoint or operational signer is exposed here. A matching MAC only proves
possession of the test/issuer key; it does not establish human authority,
source provenance, current policy, receipt authenticity, or a transaction.
The future caller must establish those facts separately and atomically.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
from typing import Mapping, NoReturn, Optional, Sequence, cast
from uuid import UUID

_DOMAIN = b"ctt-staff-sql-proof-v2"
FIELDS = (
    "kid",
    "operation",
    "tenant_id",
    "tournament_edition_id",
    "tournament_slug",
    "session_id",
    "draft_id",
    "draft_version",
    "draft_content_hash",
    "team_id",
    "staff_id",
    "staff_slot",
    "staff_role",
    "actor_id",
    "actor_role",
    "actor_updated_at_us",
    "authorization_epoch",
    "source_pages_sha256",
    "payload_sha256",
    "review_decision_hash",
    "review_receipt_id",
    "policy_hash",
    "roster_decision_id",
    "roster_draft_binding",
    "preauthorization_receipt_id",
    "finality_receipt_id",
    "attempt_id",
    "nonce",
    "issued_at_us",
    "expires_at_us",
    "transaction_id",
    "staff_row_json_text",
)
_HEX = re.compile(r"[0-9a-f]{64}")
_SHA = re.compile(r"sha256:[0-9a-f]{64}")
_HMAC = re.compile(r"hmac-sha256:[0-9a-f]{64}")
_INT = re.compile(r"[1-9][0-9]*")
_TX = re.compile(r"samchat-tx-[0-9a-f]{24}")


class StaffProofFormatError(ValueError):
    """Invalid proof data; never includes its potentially sensitive values."""


def _invalid() -> NoReturn:
    raise StaffProofFormatError("invalid staff proof fields")


def _json_without_duplicates(text: str) -> None:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _invalid()
            result[key] = value
        return result

    def constant(_value):
        _invalid()

    try:
        parsed = json.loads(
            text, object_pairs_hook=pairs, parse_constant=constant
        )
        if not isinstance(parsed, dict):
            _invalid()
    except (TypeError, ValueError, RecursionError):
        _invalid()


def _fields(values: Mapping[str, Optional[str]]) -> dict[str, Optional[str]]:
    if not isinstance(values, Mapping) or set(values) != set(FIELDS):
        _invalid()
    result = dict(values)
    for name in FIELDS:
        value = result[name]
        if name == "finality_receipt_id" and value is None:
            continue
        if not isinstance(value, str) or not value:
            _invalid()
    if result["operation"] not in ("CREATE_PENDING", "ACTIVATE"):
        _invalid()
    if (result["operation"] == "CREATE_PENDING") != (
        result["finality_receipt_id"] is None
    ):
        _invalid()
    if result["staff_slot"] not in ("1", "2") or result["staff_role"] != (
        "director_tecnico" if result["staff_slot"] == "1" else "auxiliar"
    ):
        _invalid()
    for name in (
        "tournament_edition_id",
        "session_id",
        "draft_id",
        "team_id",
        "staff_id",
        "actor_id",
        "nonce",
    ):
        try:
            if str(UUID(result[name])) != result[name]:
                _invalid()
        except (ValueError, AttributeError, TypeError):
            _invalid()
    for name in (
        "draft_version",
        "actor_updated_at_us",
        "issued_at_us",
        "expires_at_us",
    ):
        value = cast(str, result[name])
        if (
            not _INT.fullmatch(value)
            or len(value) > 19
            or int(value) > 9223372036854775807
        ):
            _invalid()
    if int(cast(str, result["issued_at_us"])) >= int(
        cast(str, result["expires_at_us"])
    ):
        _invalid()
    for name in (
        "draft_content_hash",
        "authorization_epoch",
        "review_decision_hash",
        "review_receipt_id",
        "policy_hash",
        "roster_decision_id",
        "preauthorization_receipt_id",
        "attempt_id",
    ):
        if not _SHA.fullmatch(cast(str, result[name])):
            _invalid()
    if result["finality_receipt_id"] is not None and not _SHA.fullmatch(
        cast(str, result["finality_receipt_id"])
    ):
        _invalid()
    for name in ("source_pages_sha256", "payload_sha256"):
        if not _HEX.fullmatch(cast(str, result[name])):
            _invalid()
    if not _HMAC.fullmatch(cast(str, result["roster_draft_binding"])):
        _invalid()
    if not _TX.fullmatch(cast(str, result["transaction_id"])):
        _invalid()
    _json_without_duplicates(cast(str, result["staff_row_json_text"]))
    return result


def frame_staff_proof(values: Mapping[str, Optional[str]]) -> bytes:
    """Encode exact field bytes, including sensitive text.

    Prefix is literal ASCII. Each UTF-8 value uses a signed big-endian int32
    byte length; SQL NULL uses -1. Never reserialize or Unicode-normalize the
    values. Do not log or persist this buffer as public evidence.
    """
    validated = _fields(values)
    return frame_values([validated[name] for name in FIELDS])


def frame_values(values: Sequence[Optional[str]]) -> bytes:
    """Encode the wire fields without treating them as verified claims."""
    if not isinstance(values, (list, tuple)) or len(values) != len(FIELDS):
        _invalid()
    parts = [_DOMAIN]
    size = len(_DOMAIN)
    try:
        for index, value in enumerate(values):
            if value is None:
                if FIELDS[index] != "finality_receipt_id":
                    _invalid()
                parts.append(struct.pack(">i", -1))
                size += 4
                continue
            if not isinstance(value, str):
                _invalid()
            encoded = value.encode("utf-8", "strict")
            if len(encoded) > 1048576:
                _invalid()
            parts.extend((struct.pack(">i", len(encoded)), encoded))
            size += 4 + len(encoded)
        if size > 1048576:
            _invalid()
    except (UnicodeError, OverflowError, struct.error):
        _invalid()
    return b"".join(parts)


def verify_staff_proof_mac(
    values: Mapping[str, Optional[str]],
    signature: str,
    *,
    key: bytes,
    now_us: int,
    max_ttl_us: int,
) -> bool:
    """Compare MAC and server-supplied TTL; this never authorizes an effect."""
    if (
        not isinstance(key, bytes)
        or len(key) < 32
        or not isinstance(signature, str)
        or not _HMAC.fullmatch(signature)
        or type(now_us) is not int
        or now_us < 0
        or type(max_ttl_us) is not int
        or max_ttl_us < 1
    ):
        return False
    try:
        validated = _fields(values)
        if (
            int(cast(str, validated["expires_at_us"]))
            - int(cast(str, validated["issued_at_us"]))
            > max_ttl_us
        ):
            return False
        if (
            not int(cast(str, validated["issued_at_us"]))
            <= now_us
            < int(cast(str, validated["expires_at_us"]))
        ):
            return False
        encoded = frame_staff_proof(validated)
        expected = (
            "hmac-sha256:" + hmac.new(key, encoded, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(signature, expected)
    except (StaffProofFormatError, KeyError, TypeError, ValueError):
        return False
