"""Typed private batch issuer RPC. The workflow never receives issuer secrets.

Channel authentication is not reviewer authority. Only the independent issuer
loads its signing seed/SQL pool and verifies genuine parent UV and current SQL
state. Individual V2 request/proof formats remain unchanged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException, Request, Response

from .staff_batch_authorization import CONSENT_SCHEMA, MEMBER_SCHEMA, verify_batch_proof
from .staff_batch_contracts import canonical, canonical_uuid, strict_loads
from .staff_capture_transport_store import PostgresTransportNonces

PREFIX = "/v3/staff-review/batch-issuer/"
OPERATIONS = frozenset({"consent", "child"})
LIMIT = 262144
TIME = "X-CTT-Batch-Issuer-Time"
NONCE = "X-CTT-Batch-Issuer-Nonce"
MAC = "X-CTT-Batch-Issuer-MAC"
RESPONSE_MAC = "X-CTT-Batch-Issuer-Response-MAC"


class BatchIssuerUnavailable(ValueError):
    """No authenticated proof received; recover the same protected attempt."""


def deny():
    raise BatchIssuerUnavailable("batch issuer unavailable") from None


def _binary(value):
    try:
        raw = base64.b64decode(value, validate=True)
        if (
            type(value) is not str
            or not 0 < len(raw) <= LIMIT
            or base64.b64encode(raw).decode() != value
        ):
            deny()
        return raw
    except Exception:
        deny()


def _json(raw):
    value = strict_loads(raw, LIMIT)
    if type(value) is not dict or canonical(value) != raw:
        deny()
    return value


def _body(operation, value):
    fields = (
        {"challenge_id", "assertion"}
        if operation == "consent"
        else {"consent_id", "member_id", "worker_id", "lease_epoch"}
    )
    if (
        operation not in OPERATIONS
        or type(value) is not dict
        or set(value) != {"schema", "operation", *fields}
        or value["schema"] != "ctt.batch.issuer-request.v1"
        or value["operation"] != operation
    ):
        deny()
    for name in fields - {"assertion", "lease_epoch"}:
        canonical_uuid(value[name])
    if operation == "consent":
        _binary(value["assertion"])
    elif type(value["lease_epoch"]) is not int or not 1 <= value["lease_epoch"] < 2**63:
        deny()
    return value


def request_mac(key, operation, nonce, stamp, raw):
    if (
        type(key) is not bytes
        or not 32 <= len(key) <= 64
        or operation not in OPERATIONS
        or type(nonce) is not UUID
        or type(stamp) is not int
        or not 0 < stamp < 2**63
        or type(raw) is not bytes
        or not 0 < len(raw) <= LIMIT
    ):
        deny()
    frame = (
        b"ctt.batch.issuer-rpc.request.v1\0"
        + (PREFIX + operation).encode()
        + b"\0"
        + str(nonce).encode()
        + b"\0"
        + str(stamp).encode()
        + b"\0"
        + raw
    )
    return hmac.new(key, frame, hashlib.sha256).hexdigest()


def response_mac(key, operation, nonce, request, raw):
    return hmac.new(
        key,
        b"ctt.batch.issuer-rpc.response.v1\0"
        + (PREFIX + operation).encode()
        + b"\0"
        + str(nonce).encode()
        + hashlib.sha256(request).digest()
        + raw,
        hashlib.sha256,
    ).hexdigest()


def mount_batch_issuer_rpc(
    app, *, issuer, channel_key, consume_nonce, timeout_seconds=60, max_clock_skew=30
):
    """Mount only inside the independently configured issuer process."""
    from cryptography.hazmat.primitives import serialization
    from staff_batch_issuer_v3 import PostgresBatchAssertionIssuer

    if (
        type(issuer) is not PostgresBatchAssertionIssuer
        or not isinstance(consume_nonce, PostgresTransportNonces)
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 60
        or type(max_clock_skew) is not int
        or not 1 <= max_clock_skew <= 60
    ):
        deny()
    seed = issuer._key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    if (
        type(channel_key) is not bytes
        or not 32 <= len(channel_key) <= 64
        or any(
            hmac.compare_digest(value, channel_key)
            for value in (
                seed,
                *issuer._issuer_keys.values(),
                *issuer._verifier_keys.values(),
            )
        )
    ):
        deny()
    semaphore = asyncio.Semaphore(8)

    @app.post(PREFIX + "{operation}")
    async def invoke(operation: str, request: Request):
        if operation not in OPERATIONS or semaphore.locked():
            raise HTTPException(503, "batch issuer unavailable")
        try:
            async with semaphore, asyncio.timeout(timeout_seconds):
                if (
                    any(
                        len(request.headers.getlist(k)) != 1
                        for k in (TIME, NONCE, MAC, "content-type")
                    )
                    or request.headers["content-type"] != "application/json"
                    or request.headers.get("content-encoding", "identity") != "identity"
                ):
                    deny()
                parts, size = [], 0
                async for part in request.stream():
                    size += len(part)
                    if size > LIMIT:
                        deny()
                    parts.append(part)
                raw = b"".join(parts)
                nonce = UUID(canonical_uuid(request.headers[NONCE]))
                stamp = int(request.headers[TIME])
                if (
                    str(stamp) != request.headers[TIME]
                    or abs(int(time.time()) - stamp) > max_clock_skew
                    or not hmac.compare_digest(
                        request_mac(channel_key, operation, nonce, stamp, raw),
                        request.headers[MAC],
                    )
                ):
                    deny()
                value = _body(operation, _json(raw))
                if await consume_nonce(nonce, stamp + max_clock_skew) is not True:
                    deny()
                if operation == "consent":
                    proof = await issuer.issue_consent(
                        UUID(value["challenge_id"]), _binary(value["assertion"])
                    )
                else:
                    proof = await issuer.issue_child(
                        UUID(value["consent_id"]),
                        UUID(value["member_id"]),
                        UUID(value["worker_id"]),
                        value["lease_epoch"],
                    )
                body = canonical(
                    {
                        "schema": "ctt.batch.issuer-response.v1",
                        "proof": base64.b64encode(proof).decode(),
                    }
                )
                return Response(
                    body,
                    media_type="application/json",
                    headers={
                        RESPONSE_MAC: response_mac(
                            channel_key, operation, nonce, raw, body
                        ),
                        "Cache-Control": "no-store",
                    },
                )
        except Exception:
            raise HTTPException(503, "batch issuer unavailable") from None

    return app


class BatchIssuerClient:
    """Remote issuer reference with public verification pins only."""

    def __init__(
        self, *, url, channel_key, http_client, issuer_public_keys, timeout_seconds=60
    ):
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "http"
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or parsed.port is None
                or parsed.path not in ("", "/")
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or getattr(http_client, "trust_env", None) is not False
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 60
                or not issuer_public_keys
                or any(
                    type(k) is not str or not k or type(v) is not bytes or len(v) != 32
                    for k, v in issuer_public_keys.items()
                )
            ):
                deny()
            request_mac(channel_key, "child", uuid4(), int(time.time()), b"{}")
            if channel_key in issuer_public_keys.values():
                deny()
            self._url, self._key, self._http = url.rstrip("/"), channel_key, http_client
            self._public_keys = dict(issuer_public_keys)
            self._timeout = timeout_seconds
        except Exception:
            deny()

    async def _call(self, operation, values):
        try:
            body = canonical(
                _body(
                    operation,
                    {
                        "schema": "ctt.batch.issuer-request.v1",
                        "operation": operation,
                        **values,
                    },
                )
            )
            nonce, stamp = uuid4(), int(time.time())
            headers = {
                "content-type": "application/json",
                NONCE: str(nonce),
                TIME: str(stamp),
                MAC: request_mac(self._key, operation, nonce, stamp, body),
            }
            async with self._http.stream(
                "POST",
                self._url + PREFIX + operation,
                content=body,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-type") != "application/json"
                    or response.headers.get("content-encoding", "identity")
                    != "identity"
                    or len(response.headers.get_list(RESPONSE_MAC)) != 1
                ):
                    deny()
                parts, size = [], 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > LIMIT:
                        deny()
                    parts.append(part)
                raw = b"".join(parts)
                if not hmac.compare_digest(
                    response_mac(self._key, operation, nonce, body, raw),
                    response.headers[RESPONSE_MAC],
                ):
                    deny()
                result = _json(raw)
                if (
                    set(result) != {"schema", "proof"}
                    or result["schema"] != "ctt.batch.issuer-response.v1"
                ):
                    deny()
                proof = _binary(result["proof"])
                claims = verify_batch_proof(
                    proof,
                    schema=CONSENT_SCHEMA if operation == "consent" else MEMBER_SCHEMA,
                    public_keys=self._public_keys,
                    now=int(time.time()),
                )
                for key, value in values.items():
                    if key != "assertion" and claims.get(key) != value:
                        deny()
                return proof
        except Exception:
            deny()

    async def issue_consent(self, challenge_id, assertion):
        return await self._call(
            "consent",
            {
                "challenge_id": str(challenge_id),
                "assertion": base64.b64encode(assertion).decode(),
            },
        )

    async def issue_child(self, consent_id, member_id, worker_id, lease_epoch):
        return await self._call(
            "child",
            {
                "consent_id": str(consent_id),
                "member_id": str(member_id),
                "worker_id": str(worker_id),
                "lease_epoch": lease_epoch,
            },
        )
