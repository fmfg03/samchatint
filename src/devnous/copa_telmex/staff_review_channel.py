"""Bounded authenticated identity-proof channel; never business authority."""

import base64
import hashlib
import hmac
import json
import re
import struct
from typing import NoReturn, cast
from uuid import UUID

PATH = "/v2/staff-review/verify"
MAX_ASSERTION_BYTES = 65536
MAX_REQUEST_BYTES = 100000
MAX_PROOF_BYTES = 8192
MAX_RESPONSE_BYTES = 12000
TIME_HEADER = "X-CTT-Review-Time"
NONCE_HEADER = "X-CTT-Review-Nonce"
MAC_HEADER = "X-CTT-Review-MAC"
RESPONSE_HEADER = "X-CTT-Review-Response-MAC"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class ReviewChannelDenied(ValueError):
    """A private channel message could not be authenticated or decoded."""


def _deny() -> NoReturn:
    raise ReviewChannelDenied("review channel denied") from None


def _key(key: bytes) -> None:
    if type(key) is not bytes or len(key) < 32:
        _deny()


def _canonical(value: dict[str, str]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _frame(*parts: bytes) -> bytes:
    return b"".join(struct.pack(">I", len(part)) + part for part in parts)


def _json(raw: bytes, maximum: int) -> dict[str, str]:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        _deny()
    # Channel envelopes have only string-valued members. Reject nesting
    # before invoking the JSON decoder, including inside oversized attacks.
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 1:
                _deny()
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _deny()
    if quoted or depth != 0:
        _deny()

    def pairs(items):
        result = {}
        for name, value in items:
            if name in result or type(value) is not str:
                _deny()
            result[name] = value
        return result

    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=pairs)
        if type(value) is not dict or _canonical(value) != raw:
            _deny()
        return cast(dict[str, str], value)
    except Exception:
        _deny()


def _base64(value: str, maximum: int) -> bytes:
    try:
        if type(value) is not str:
            _deny()
        raw = base64.b64decode(value, validate=True)
        if (
            not 0 < len(raw) <= maximum
            or base64.b64encode(raw).decode("ascii") != value
        ):
            _deny()
        return raw
    except Exception:
        _deny()


def encode_review_request(attempt_id: UUID, assertion: bytes) -> bytes:
    if (
        not isinstance(attempt_id, UUID)
        or type(assertion) is not bytes
        or not 0 < len(assertion) <= MAX_ASSERTION_BYTES
    ):
        _deny()
    return _canonical(
        {
            "attempt_id": str(attempt_id),
            "assertion": base64.b64encode(assertion).decode("ascii"),
        }
    )


def decode_review_request(raw: bytes) -> tuple[UUID, bytes]:
    try:
        value = _json(raw, MAX_REQUEST_BYTES)
        if set(value) != {"attempt_id", "assertion"}:
            _deny()
        attempt_id = UUID(value["attempt_id"])
        if str(attempt_id) != value["attempt_id"]:
            _deny()
        return attempt_id, _base64(value["assertion"], MAX_ASSERTION_BYTES)
    except Exception:
        _deny()


def request_mac(key: bytes, nonce: UUID, timestamp: int, body: bytes) -> str:
    _key(key)
    if (
        not isinstance(nonce, UUID)
        or type(timestamp) is not int
        or not 0 < timestamp <= 253402300799
        or type(body) is not bytes
        or not 0 < len(body) <= MAX_REQUEST_BYTES
    ):
        _deny()
    message = b"samchat.ctt.review-http.request.v1\x00" + _frame(
        b"POST",
        PATH.encode("ascii"),
        str(timestamp).encode("ascii"),
        str(nonce).encode("ascii"),
        body,
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_request(key, nonce_text, timestamp_text, mac, body, *, now, skew):
    try:
        if (
            type(nonce_text) is not str
            or type(timestamp_text) is not str
            or len(timestamp_text) > 12
            or not timestamp_text.isascii()
            or not timestamp_text.isdecimal()
            or type(mac) is not str
            or not _HEX.fullmatch(mac)
            or type(now) is not int
            or type(skew) is not int
            or not 1 <= skew <= 60
        ):
            _deny()
        nonce, timestamp = UUID(nonce_text), int(timestamp_text)
        if (
            str(nonce) != nonce_text
            or str(timestamp) != timestamp_text
            or abs(now - timestamp) > skew
            or not hmac.compare_digest(request_mac(key, nonce, timestamp, body), mac)
        ):
            _deny()
        return nonce, timestamp + skew
    except Exception:
        _deny()


def encode_review_response(proof: bytes) -> bytes:
    if type(proof) is not bytes or not 0 < len(proof) <= MAX_PROOF_BYTES:
        _deny()
    return _canonical({"proof": base64.b64encode(proof).decode("ascii")})


def response_mac(
    key: bytes, nonce: UUID, request_body: bytes, response_body: bytes
) -> str:
    _key(key)
    if (
        not isinstance(nonce, UUID)
        or type(request_body) is not bytes
        or not 0 < len(request_body) <= MAX_REQUEST_BYTES
        or type(response_body) is not bytes
        or not 0 < len(response_body) <= MAX_RESPONSE_BYTES
    ):
        _deny()
    message = b"samchat.ctt.review-http.response.v1\x00" + _frame(
        str(nonce).encode("ascii"),
        hashlib.sha256(request_body).digest(),
        b"200",
        response_body,
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def decode_review_response(key, nonce, request_body, body, mac) -> bytes:
    if type(mac) is not str or not _HEX.fullmatch(mac):
        _deny()
    if not hmac.compare_digest(response_mac(key, nonce, request_body, body), mac):
        _deny()
    value = _json(body, MAX_RESPONSE_BYTES)
    if set(value) != {"proof"}:
        _deny()
    return _base64(value["proof"], MAX_PROOF_BYTES)
