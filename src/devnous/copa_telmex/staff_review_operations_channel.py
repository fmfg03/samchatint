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
import os
import stat
import struct
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .staff_capture_transport_store import PostgresTransportNonces
from .staff_review_channel import MAC_HEADER, NONCE_HEADER, RESPONSE_HEADER, TIME_HEADER

PREFIX = "/v2/staff-review/operations/"
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 24_000_000
MAX_BINARY_BYTES = 16_000_000
OPERATIONS = {
    "view": (),
    "prepare": ("expected_proposal_id", "changes"),
    "challenge": ("proposal_id", "confirmed"),
    "assert_and_capture": ("attempt_id", "assertion"),
    "status": ("attempt_id",),
    "source_page": ("proposal_id", "page_index"),
    "enrollment_begin": (),
    "enrollment_complete": ("enrollment_id", "registration"),
    "enrollment_status": ("enrollment_id",),
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
        "REVIEW_EXPIRED",
    }
)


class StaffReviewOperationsUnavailable(ValueError):
    """Authenticated result unavailable; an operation may already have committed."""


class StaffReviewOperationDenied(StaffReviewOperationsUnavailable):
    """Authenticated bounded domain denial without source or key details."""

    def __init__(self, code: str):
        if code not in SAFE_DOMAIN_CODES:
            _deny()
        self.code = code
        super().__init__(code)


def _deny() -> NoReturn:
    raise StaffReviewOperationsUnavailable("review operations unavailable") from None


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


def encode_operation(operation: str, account_id: UUID, session_id: UUID, **params):
    if (
        operation not in OPERATIONS
        or any(not isinstance(value, UUID) for value in (account_id, session_id))
        or set(params) != set(OPERATIONS[operation])
    ):
        _deny()
    body: dict[str, Any] = {
        "account_id": str(account_id),
        "session_id": str(session_id),
    }
    for key, value in params.items():
        if key.endswith("_id"):
            if value is None and key == "expected_proposal_id":
                body[key] = None
            elif isinstance(value, UUID):
                body[key] = str(value)
            else:
                _deny()
        elif key in {"assertion", "registration"}:
            if (
                key == "assertion"
                and operation == "assert_and_capture"
                and value is None
            ):
                body[key] = None
                continue
            if type(value) is dict:
                value = _canonical(value)
            if type(value) is not bytes or not 0 < len(value) <= 65_536:
                _deny()
            body[key] = base64.b64encode(value).decode()
        else:
            body[key] = value
    raw = _canonical(body)
    decode_operation(operation, raw)
    return raw


def decode_operation(operation: str, raw: bytes):
    if operation not in OPERATIONS:
        _deny()
    value = _json(raw, MAX_REQUEST_BYTES)
    if set(value) != {"account_id", "session_id", *OPERATIONS[operation]}:
        _deny()
    for key in value:
        if key.endswith("_id"):
            if key == "expected_proposal_id" and value[key] is None:
                continue
            value[key] = _uuid(value[key])
        elif key in {"assertion", "registration"}:
            if (
                key == "assertion"
                and operation == "assert_and_capture"
                and value[key] is None
            ):
                continue
            value[key] = _binary(value[key], 65_536)
        elif key == "page_index":
            if type(value[key]) is not int or not 0 <= value[key] <= 100_000:
                _deny()
        elif key == "confirmed":
            if value[key] is not True:
                _deny()
        elif key == "changes" and type(value[key]) is not dict:
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
        b"samchat.ctt.review-operations.request.v1\x00",
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
        b"samchat.ctt.review-operations.response.v1\x00",
        (PREFIX + operation).encode(),
        str(nonce).encode(),
        hashlib.sha256(request).digest(),
        str(status).encode(),
        body,
    )


def create_staff_review_operations_app(
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
    from .staff_review_workflow import ReviewWorkflowDenied, ReviewWorkflowService
    from .staff_review_proposal_store import (
        ProposalAuthorityUnavailable,
        ProposalConflict,
    )

    if (
        not isinstance(service, ReviewWorkflowService)
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
                        if operation == "source_page":
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
                                (
                                    ReviewWorkflowDenied,
                                    ProposalAuthorityUnavailable,
                                    ProposalConflict,
                                ),
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


class StaffReviewOperationsClient:
    """Private configured client; no automatic retries after uncertain outcomes."""

    def __init__(self, *, base_url, channel_key, http_client, timeout_seconds=60):
        try:
            parsed = urlsplit(base_url)
            if (
                parsed.scheme != "http"
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
                or (
                    type(timeout_seconds) not in (int, float)
                    or not math.isfinite(timeout_seconds)
                    or not 0 < timeout_seconds <= 120
                )
            ):
                _deny()
            _mac(channel_key, b"configuration")
            self._url, self._key = base_url.rstrip("/"), channel_key
            self._client, self._timeout = http_client, timeout_seconds
        except Exception:
            _deny()

    async def _call(self, operation, account_id, session_id, **params):
        try:
            body = encode_operation(operation, account_id, session_id, **params)
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
                    raise StaffReviewOperationDenied(value["error"])
                if response.status_code != 200 or set(value) != {"result"}:
                    _deny()
                if operation == "source_page":
                    if type(value["result"]) is not dict or set(value["result"]) != {
                        "png"
                    }:
                        _deny()
                    return _binary(value["result"]["png"], MAX_BINARY_BYTES)
                if type(value["result"]) is not dict:
                    _deny()
                return value["result"]
        except StaffReviewOperationDenied:
            raise
        except Exception:
            _deny()

    async def view(self, account_id, session_id):
        return await self._call("view", account_id, session_id)

    async def prepare(self, account_id, session_id, expected_proposal_id, changes):
        return await self._call(
            "prepare",
            account_id,
            session_id,
            expected_proposal_id=expected_proposal_id,
            changes=changes,
        )

    async def challenge(self, account_id, session_id, proposal_id, confirmed):
        return await self._call(
            "challenge",
            account_id,
            session_id,
            proposal_id=proposal_id,
            confirmed=confirmed,
        )

    async def assert_and_capture(self, account_id, session_id, attempt_id, assertion):
        return await self._call(
            "assert_and_capture",
            account_id,
            session_id,
            attempt_id=attempt_id,
            assertion=assertion,
        )

    async def status(self, account_id, session_id, attempt_id):
        return await self._call("status", account_id, session_id, attempt_id=attempt_id)

    async def source_page(self, account_id, session_id, proposal_id, page_index):
        return await self._call(
            "source_page",
            account_id,
            session_id,
            proposal_id=proposal_id,
            page_index=page_index,
        )

    async def enrollment_begin(self, account_id, session_id):
        return await self._call("enrollment_begin", account_id, session_id)

    async def enrollment_complete(
        self, account_id, session_id, enrollment_id, registration
    ):
        return await self._call(
            "enrollment_complete",
            account_id,
            session_id,
            enrollment_id=enrollment_id,
            registration=registration,
        )

    async def enrollment_status(self, account_id, session_id, enrollment_id):
        return await self._call(
            "enrollment_status", account_id, session_id, enrollment_id=enrollment_id
        )

    async def aclose(self):
        """Release configured HTTP resources during application shutdown."""
        await self._client.aclose()


@dataclass(frozen=True, repr=False)
class StaffReviewClientSettings:
    """Trusted startup settings; disabled is the default and creates no client."""

    enabled: bool = False
    base_url: str = ""
    key_path: Path | None = field(default=None, repr=False)
    timeout_seconds: float = 60


def _read_key(path: Path):
    try:
        path = Path(path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.is_relative_to(Path(__file__).absolute().parents[3])
            or path.is_relative_to(Path("/root/samchat"))
        ):
            _deny()
        with ExitStack() as stack:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            stack.callback(os.close, fd)
            for part in path.parts[1:-1]:
                fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                stack.callback(os.close, fd)
            parent = os.fstat(fd)
            if (
                parent.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(parent.st_mode) != 0o700
            ):
                _deny()
            keyfd = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            stack.callback(os.close, keyfd)
            info = os.fstat(keyfd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in (0, os.geteuid())
                or (
                    stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                    or info.st_nlink != 1
                    or not 32 <= info.st_size <= 64
                )
            ):
                _deny()
            key = os.read(keyfd, 65)
            if len(key) != info.st_size:
                _deny()
            return key
    except Exception:
        _deny()


def configure_staff_review_client(app, settings: StaffReviewClientSettings):
    """Configure once from explicit settings; never read environment or request."""
    import httpx

    if (
        not isinstance(settings, StaffReviewClientSettings)
        or type(settings.enabled) is not bool
    ):
        _deny()
    if getattr(app.state, "ctt_staff_review_client", None) is not None:
        _deny()
    app.state.ctt_staff_review_client = None
    if not settings.enabled:
        return None
    if settings.key_path is None:
        _deny()
    key = _read_key(settings.key_path)
    transport = httpx.AsyncClient(trust_env=False, follow_redirects=False)
    try:
        client = StaffReviewOperationsClient(
            base_url=settings.base_url,
            channel_key=key,
            http_client=transport,
            timeout_seconds=settings.timeout_seconds,
        )
    except Exception:
        # Validate configuration before allocating resources used by requests.
        # AsyncClient has not opened any connection at this point.
        _deny()
    app.state.ctt_staff_review_client = client
    return client


async def close_staff_review_client(app):
    """Call from lifespan cleanup; no live default configuration is installed."""
    client = getattr(app.state, "ctt_staff_review_client", None)
    app.state.ctt_staff_review_client = None
    if client is not None:
        await client.aclose()
