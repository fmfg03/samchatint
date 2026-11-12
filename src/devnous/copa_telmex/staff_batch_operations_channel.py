"""Explicit authenticated private workflow transport; never business authority.

Configure only from trusted process settings. Browser values never select URLs,
keys, database roles or workflow implementations. No automatic configuration.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import struct
import time
from typing import Any, NoReturn
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .staff_capture_transport_store import PostgresTransportNonces
from .staff_review_channel import MAC_HEADER, NONCE_HEADER, RESPONSE_HEADER, TIME_HEADER

PREFIX = "/v3/staff-review/batches/"
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 24_000_000
MAX_BINARY_BYTES = 16_000_000
OPERATIONS = {
    "catalog": ("account_id",),
    "create_run": ("account_id", "payload"),
    "view": ("account_id", "run_id", "offset", "limit"),
    "sample": ("account_id", "run_id", "payload"),
    "prepare": ("account_id", "run_id", "payload"),
    "challenge": ("account_id", "block_id", "payload"),
    "assertion": ("account_id", "block_id", "payload"),
    "pause": ("account_id", "block_id", "payload"),
    "resume": ("account_id", "block_id", "payload"),
    "revoke": ("account_id", "block_id", "payload"),
    "attempt": ("account_id", "block_id", "attempt_id"),
    "source_page": ("account_id", "run_id", "document_id", "page_index"),
    "source_asset": ("account_id", "session_id", "asset_id"),
    "member": ("account_id", "run_id", "member_id"),
    "propose_corrections": ("account_id", "run_id", "member_id", "payload"),
    "report_quality_issue": ("account_id", "run_id", "member_id", "payload"),
}

SAFE_DOMAIN_CODES = frozenset(
    {
        "STALE_PROPOSAL",
        "REVIEW_STALE",
        "ENROLLMENT_REQUIRED",
        "PASSKEY_PENDING_ACTIVATION",
        "SOURCE_UNAVAILABLE",
        "REVIEW_BLOCKED",
        "SCOPE_DENIED",
        "ACCOUNT_NOT_BOUND",
        "QUALITY_FALSE_ADMISSION_REPORT_REQUIRED",
        "REVIEW_EXPIRED",
    }
)


class BatchOperationsUnavailable(ValueError):
    """Authenticated result unavailable; an operation may already have committed."""


class BatchOperationDenied(BatchOperationsUnavailable):
    """Authenticated bounded domain denial without source or key details."""

    def __init__(self, code: str):
        if code not in SAFE_DOMAIN_CODES:
            _deny()
        self.code = code
        super().__init__(code)


def _deny() -> NoReturn:
    raise BatchOperationsUnavailable("review operations unavailable") from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except Exception:
        _deny()


def _json(raw: bytes, limit: int):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        _deny()
    depth, quoted, escaped = 0, False, False
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
            if depth > 24:
                _deny()
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _deny()
    if quoted or depth:
        _deny()

    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                _deny()
            result[name] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        if type(value) is not dict or _canonical(value) != raw:
            _deny()
        return value
    except Exception:
        _deny()


def _uuid(value):
    try:
        result = UUID(value)
        if str(result) != value:
            _deny()
        return result
    except Exception:
        _deny()


def _binary(value, maximum):
    try:
        result = base64.b64decode(value, validate=True)
        if not 0 < len(result) <= maximum or base64.b64encode(result).decode() != value:
            _deny()
        return result
    except Exception:
        _deny()


def encode_operation(operation, **params):
    def convert(value):
        if isinstance(value, UUID):
            return str(value)
        if type(value) is dict:
            return {k: convert(v) for k, v in value.items()}
        if type(value) is list:
            return [convert(v) for v in value]
        return value

    raw = _canonical(convert(params))
    decode_operation(operation, raw)
    return raw


def decode_operation(operation, raw):
    if operation not in OPERATIONS:
        _deny()
    value = _json(raw, MAX_REQUEST_BYTES)
    if set(value) != set(OPERATIONS[operation]):
        _deny()
    for key, item in value.items():
        if key.endswith("_id") and key != "document_id":
            value[key] = _uuid(item)
        elif key == "document_id":
            if (
                type(item) is not str
                or len(item) != 64
                or any(c not in "0123456789abcdef" for c in item)
            ):
                _deny()
        elif key == "payload" and type(item) is not dict:
            _deny()
        elif key in {"offset", "limit", "page_index"}:
            if type(item) is not int or item < 0 or item > 100000:
                _deny()
    return value


def _mac(key: bytes, domain: bytes, *parts: bytes):
    if type(key) is not bytes or not 32 <= len(key) <= 64:
        _deny()
    framed = b"".join(struct.pack(">I", len(part)) + part for part in parts)
    return hmac.new(key, domain + framed, hashlib.sha256).hexdigest()


def operation_request_mac(key, operation, nonce, timestamp, body):
    if (
        operation not in OPERATIONS
        or not isinstance(nonce, UUID)
        or (type(timestamp) is not int or not 0 < timestamp <= 253402300799)
        or type(body) is not bytes
        or not 0 < len(body) <= MAX_REQUEST_BYTES
    ):
        _deny()
    return _mac(
        key,
        b"samchat.ctt.batch-operations.request.v1\x00",
        b"POST",
        (PREFIX + operation).encode(),
        str(nonce).encode(),
        str(timestamp).encode(),
        body,
    )


def operation_response_mac(key, operation, nonce, request, status, body):
    if (
        operation not in OPERATIONS
        or not isinstance(nonce, UUID)
        or (type(status) is not int or status not in (200, 403, 503))
        or type(body) is not bytes
        or not 0 < len(body) <= MAX_RESPONSE_BYTES
    ):
        _deny()
    return _mac(
        key,
        b"samchat.ctt.batch-operations.response.v1\x00",
        (PREFIX + operation).encode(),
        str(nonce).encode(),
        hashlib.sha256(request).digest(),
        str(status).encode(),
        body,
    )


def create_batch_operations_app(
    *,
    service,
    channel_key: bytes,
    consume_nonce: PostgresTransportNonces,
    max_clock_skew=30,
    timeout_seconds=60,
    max_inflight=8,
):
    """Assemble a private listener with configured concrete protected services."""
    from fastapi import FastAPI, HTTPException, Request, Response

    from .staff_batch_store import BatchDenied
    from .staff_batch_workflow import BatchReviewWorkflowService

    if (
        not isinstance(service, BatchReviewWorkflowService)
        or not isinstance(consume_nonce, PostgresTransportNonces)
        or type(max_clock_skew) is not int
        or not 1 <= max_clock_skew <= 60
        or (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        )
        or type(max_inflight) is not int
        or not 1 <= max_inflight <= 128
    ):
        _deny()
    _mac(channel_key, b"configuration")
    service.validate_channel_key(channel_key)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    semaphore = asyncio.Semaphore(max_inflight)

    @app.post(PREFIX + "{operation}")
    async def execute(operation: str, request: Request):
        if operation not in OPERATIONS or semaphore.locked():
            raise HTTPException(503, "review operations unavailable")
        async with semaphore:
            try:
                async with asyncio.timeout(timeout_seconds):
                    for header in (
                        TIME_HEADER,
                        NONCE_HEADER,
                        MAC_HEADER,
                        "content-type",
                    ):
                        if len(request.headers.getlist(header)) != 1:
                            _deny()
                    if request.headers["content-type"] != "application/json" or (
                        request.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        _deny()
                    size, chunks = 0, []
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_REQUEST_BYTES:
                            _deny()
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    nonce = _uuid(request.headers[NONCE_HEADER])
                    text = request.headers[TIME_HEADER]
                    if not text.isascii() or not text.isdecimal() or len(text) > 12:
                        _deny()
                    timestamp = int(text)
                    if (
                        str(timestamp) != text
                        or abs(int(time.time()) - timestamp) > max_clock_skew
                    ):
                        _deny()
                    expected = operation_request_mac(
                        channel_key, operation, nonce, timestamp, body
                    )
                    if not hmac.compare_digest(expected, request.headers[MAC_HEADER]):
                        _deny()
                    params = decode_operation(operation, body)
                    if (
                        await consume_nonce(nonce, timestamp + max_clock_skew)
                        is not True
                    ):
                        _deny()
                    try:
                        result = await getattr(service, operation)(**params)
                        if operation in {"source_page", "source_asset"}:
                            if (
                                type(result) is not bytes
                                or not 0 < len(result) <= MAX_BINARY_BYTES
                            ):
                                _deny()
                            result = {"png": base64.b64encode(result).decode()}
                        output, status = _canonical({"result": result}), 200
                    except Exception as error:
                        code = getattr(error, "code", None)
                        if (
                            isinstance(
                                error,
                                (BatchDenied,),
                            )
                            and code in SAFE_DOMAIN_CODES
                        ):
                            output, status = (
                                _canonical({"ok": False, "error": code}),
                                200,
                            )
                        else:
                            output, status = (
                                _canonical({"error": "REVIEW_UNAVAILABLE"}),
                                503,
                            )
                    if len(output) > MAX_RESPONSE_BYTES:
                        _deny()
                    return Response(
                        output,
                        status_code=status,
                        media_type="application/json",
                        headers={
                            RESPONSE_HEADER: operation_response_mac(
                                channel_key, operation, nonce, body, status, output
                            ),
                            "Cache-Control": "no-store",
                        },
                    )
            except Exception:
                raise HTTPException(503, "review operations unavailable") from None

    return app


def _valid_timeout(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 120


class BatchOperationsClient:
    """Private configured client; no automatic retries after uncertain outcomes."""

    _url: str
    _key: bytes
    _client: Any
    _timeout: float

    def __init__(
        self,
        *,
        base_url: str,
        channel_key: bytes,
        http_client: Any,
        timeout_seconds: float = 60,
    ):
        try:
            parsed = urlsplit(base_url)
            valid_url: bool = not (
                parsed.scheme != "http"
                or parsed.hostname is None
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or parsed.port is None
                or parsed.path not in ("", "/")
                or (
                    parsed.query
                    or parsed.fragment
                    or parsed.username
                    or parsed.password
                )
                or getattr(http_client, "trust_env", None) is not False
                or not _valid_timeout(timeout_seconds)
            )
            if not valid_url:
                _deny()
            _mac(channel_key, b"configuration")
            self._url, self._key = base_url.rstrip("/"), channel_key
            self._client, self._timeout = http_client, timeout_seconds
        except Exception:
            _deny()

    async def _call(self, operation, **params):
        try:
            body = encode_operation(operation, **params)
            nonce, timestamp = uuid4(), int(time.time())
            headers = {
                "Content-Type": "application/json",
                NONCE_HEADER: str(nonce),
                TIME_HEADER: str(timestamp),
                MAC_HEADER: operation_request_mac(
                    self._key, operation, nonce, timestamp, body
                ),
            }
            async with self._client.stream(
                "POST",
                self._url + PREFIX + operation,
                content=body,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code not in (200, 503) or (
                    response.headers.get("content-encoding", "identity") != "identity"
                    or response.headers.get("content-type") != "application/json"
                    or len(response.headers.get_list(RESPONSE_HEADER)) != 1
                ):
                    _deny()
                parts, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        _deny()
                    parts.append(chunk)
                raw = b"".join(parts)
                expected = operation_response_mac(
                    self._key, operation, nonce, body, response.status_code, raw
                )
                if not hmac.compare_digest(expected, response.headers[RESPONSE_HEADER]):
                    _deny()
                value = _json(raw, MAX_RESPONSE_BYTES)
                if response.status_code == 200 and set(value) == {"ok", "error"}:
                    if (
                        value["ok"] is not False
                        or value["error"] not in SAFE_DOMAIN_CODES
                    ):
                        _deny()
                    raise BatchOperationDenied(value["error"])
                if response.status_code != 200 or set(value) != {"result"}:
                    _deny()
                if operation in {"source_page", "source_asset"}:
                    if type(value["result"]) is not dict or set(value["result"]) != {
                        "png"
                    }:
                        _deny()
                    return _binary(value["result"]["png"], MAX_BINARY_BYTES)
                if type(value["result"]) is not dict:
                    _deny()
                return value["result"]
        except BatchOperationDenied:
            raise
        except Exception:
            _deny()

    async def catalog(self, account_id):
        return await self._call("catalog", account_id=account_id)

    async def create_run(self, account_id, payload):
        return await self._call("create_run", account_id=account_id, payload=payload)

    async def view(self, account_id, run_id, offset=0, limit=50):
        return await self._call(
            "view", account_id=account_id, run_id=run_id, offset=offset, limit=limit
        )

    async def sample(self, account_id, run_id, payload):
        return await self._call(
            "sample", account_id=account_id, run_id=run_id, payload=payload
        )

    async def prepare(self, account_id, run_id, payload):
        return await self._call(
            "prepare", account_id=account_id, run_id=run_id, payload=payload
        )

    async def challenge(self, account_id, block_id, payload):
        return await self._call(
            "challenge", account_id=account_id, block_id=block_id, payload=payload
        )

    async def assertion(self, account_id, block_id, payload):
        return await self._call(
            "assertion", account_id=account_id, block_id=block_id, payload=payload
        )

    async def pause(self, account_id, block_id, payload):
        return await self._call(
            "pause", account_id=account_id, block_id=block_id, payload=payload
        )

    async def resume(self, account_id, block_id, payload):
        return await self._call(
            "resume", account_id=account_id, block_id=block_id, payload=payload
        )

    async def revoke(self, account_id, block_id, payload):
        return await self._call(
            "revoke", account_id=account_id, block_id=block_id, payload=payload
        )

    async def attempt(self, account_id, block_id, attempt_id):
        return await self._call(
            "attempt", account_id=account_id, block_id=block_id, attempt_id=attempt_id
        )

    async def source_page(self, account_id, run_id, document_id, page_index):
        return await self._call(
            "source_page",
            account_id=account_id,
            run_id=run_id,
            document_id=document_id,
            page_index=page_index,
        )

    async def source_asset(self, account_id, session_id, asset_id):
        return await self._call(
            "source_asset",
            account_id=account_id,
            session_id=session_id,
            asset_id=asset_id,
        )

    async def member(self, account_id, run_id, member_id):
        return await self._call(
            "member", account_id=account_id, run_id=run_id, member_id=member_id
        )

    async def propose_corrections(self, account_id, run_id, member_id, payload):
        return await self._call(
            "propose_corrections",
            account_id=account_id,
            run_id=run_id,
            member_id=member_id,
            payload=payload,
        )

    async def report_quality_issue(self, account_id, run_id, member_id, payload):
        return await self._call(
            "report_quality_issue",
            account_id=account_id,
            run_id=run_id,
            member_id=member_id,
            payload=payload,
        )
