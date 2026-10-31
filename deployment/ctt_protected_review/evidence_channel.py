"""Authenticated, bounded publication into a separate v2 durable Bus journal."""

import asyncio
import hashlib
import hmac
import ipaddress
import time
import httpx
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response

from devnous.copa_telmex.staff_review_channel import (
    MAC_HEADER,
    NONCE_HEADER,
    RESPONSE_HEADER,
    TIME_HEADER,
    _frame,
)
from devnous.copa_telmex.staff_review_operations_channel import _canonical, _json

PATH = "/v2/ctt/evidence"
MAX_BYTES = 262144


class EvidenceTransportUnavailable(ValueError):
    """No publication receipt was confirmed; never means no durable event."""


class EvidenceTransportUncertain(ConnectionError):
    """A transport failure can recover the identical durable event on retry."""


def _key(key):
    if type(key) is not bytes or not 32 <= len(key) <= 64:
        raise EvidenceTransportUnavailable("invalid evidence transport configuration")


def request_mac(key, nonce, timestamp, body):
    _key(key)
    if type(nonce) is not UUID or type(timestamp) is not int or timestamp < 1:
        raise EvidenceTransportUnavailable("invalid evidence request")
    return hmac.new(
        key,
        b"ctt.evidence.request.v1\0"
        + _frame(
            b"POST",
            PATH.encode(),
            str(nonce).encode(),
            str(timestamp).encode(),
            body,
        ),
        hashlib.sha256,
    ).hexdigest()


def response_mac(key, nonce, request, status, body):
    _key(key)
    return hmac.new(
        key,
        b"ctt.evidence.response.v1\0"
        + _frame(
            PATH.encode(),
            str(nonce).encode(),
            hashlib.sha256(request).digest(),
            str(status).encode(),
            body,
        ),
        hashlib.sha256,
    ).hexdigest()


def create_evidence_app(
    *, journal, signing_key, signing_kid, channel_key, consume_nonce, timeout_seconds=60
):
    """Only a validated v2 event can be signed and fsynced in this process."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from evidence_receipt import issue_evidence_receipt
    from staff_v2_event_validation import validate_staff_v2_event

    _key(channel_key)
    if (
        not isinstance(signing_key, Ed25519PrivateKey)
        or type(signing_kid) is not str
        or not signing_kid
        or not callable(consume_nonce)
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 90
        or hmac.compare_digest(
            channel_key,
            signing_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
        )
    ):
        raise EvidenceTransportUnavailable("invalid evidence service configuration")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    semaphore = asyncio.Semaphore(8)

    @app.get("/healthz", include_in_schema=False)
    async def health():
        return {"status": "ok", "role": "ctt-evidence-v2"}

    @app.post(PATH, include_in_schema=False)
    async def publish(request: Request):
        try:
            async with asyncio.timeout(timeout_seconds), semaphore:
                if (
                    len(request.headers.getlist("content-type")) != 1
                    or len(request.headers.getlist("content-encoding")) > 1
                ):
                    raise ValueError
                if request.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError
                if request.headers.get("content-type") != "application/json":
                    raise ValueError
                for header in (MAC_HEADER, TIME_HEADER, NONCE_HEADER):
                    if len(request.headers.getlist(header)) != 1:
                        raise ValueError
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError
                raw = bytes(body)
                event = _json(raw, MAX_BYTES)
                if _canonical(event) != raw or not validate_staff_v2_event(event):
                    raise ValueError
                timestamp = int(request.headers[TIME_HEADER])
                if (
                    str(timestamp) != request.headers[TIME_HEADER]
                    or abs(int(time.time()) - timestamp) > 30
                ):
                    raise ValueError
                nonce = UUID(request.headers[NONCE_HEADER])
                if str(nonce) != request.headers[NONCE_HEADER]:
                    raise ValueError
                if not hmac.compare_digest(
                    request_mac(channel_key, nonce, timestamp, raw),
                    request.headers[MAC_HEADER],
                ):
                    raise ValueError
                if await consume_nonce(nonce, timestamp + 30) is not True:
                    raise ValueError
                wrapped = {
                    **event,
                    "type": event["event_type"],
                    "execution_receipt_ref": "zaubern:registration-gate:precommit",
                }
                try:
                    receipt = await asyncio.to_thread(
                        journal.append_idempotent_receipt,
                        wrapped,
                        lambda: issue_evidence_receipt(
                            wrapped,
                            None,
                            kid=signing_kid,
                            alg="Ed25519",
                            ed25519_private_key=signing_key,
                        ),
                    )
                    output, status = _canonical({"receipt": receipt}), 200
                except Exception:
                    output, status = _canonical({"error": "EVIDENCE_UNAVAILABLE"}), 503
                return Response(
                    output,
                    status_code=status,
                    media_type="application/json",
                    headers={
                        "Cache-Control": "no-store",
                        RESPONSE_HEADER: response_mac(
                            channel_key, nonce, raw, status, output
                        ),
                    },
                )
        except Exception:
            return Response(status_code=403, headers={"Cache-Control": "no-store"})

    return app


class EvidencePublisher:
    """Synchronous Gate publisher; an ambiguous response is retryable by event ID."""

    def __init__(self, *, base_url, channel_key, http_client, timeout_seconds=60):
        _key(channel_key)
        parsed = urlsplit(base_url)
        try:
            valid = (
                parsed.scheme == "http"
                and ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            valid = False
        if (
            not valid
            or parsed.port is None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or getattr(http_client, "trust_env", None) is not False
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 90
        ):
            raise EvidenceTransportUnavailable(
                "invalid evidence publisher configuration"
            )
        self._url, self._key, self._client = (
            base_url.rstrip("/") + PATH,
            channel_key,
            http_client,
        )
        self._timeout = timeout_seconds

    def __call__(self, event):
        raw = _canonical(event)
        if not 0 < len(raw) <= MAX_BYTES:
            raise EvidenceTransportUnavailable("evidence request too large")
        nonce, timestamp = uuid4(), int(time.time())
        try:
            with self._client.stream(
                "POST",
                self._url,
                content=raw,
                follow_redirects=False,
                timeout=self._timeout,
                headers={
                    "Content-Type": "application/json",
                    NONCE_HEADER: str(nonce),
                    TIME_HEADER: str(timestamp),
                    MAC_HEADER: request_mac(self._key, nonce, timestamp, raw),
                },
            ) as response:
                if (
                    response.headers.get("content-encoding", "identity") != "identity"
                    or len(response.headers.get_list(RESPONSE_HEADER)) != 1
                ):
                    raise ValueError
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError
                    parts.append(chunk)
                output = b"".join(parts)
                if not hmac.compare_digest(
                    response_mac(self._key, nonce, raw, response.status_code, output),
                    response.headers[RESPONSE_HEADER],
                ):
                    raise ValueError
                value = _json(output, MAX_BYTES)
                if (
                    response.status_code != 200
                    or set(value) != {"receipt"}
                    or type(value["receipt"]) is not dict
                ):
                    raise ValueError
                return value["receipt"]
        except (httpx.TransportError, OSError):
            raise EvidenceTransportUncertain("evidence response lost") from None
        except Exception:
            raise EvidenceTransportUnavailable(
                "evidence receipt not confirmed"
            ) from None
