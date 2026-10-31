"""Private Unix transport for the trusted capture writer; no HTTP endpoint.

Peer UID and channel MAC identify a caller process, never a human reviewer.
The handler must verify authority and own a cancellation-safe transaction.
The nonce consumer must be durable and atomic; no in-memory fallback exists.
A transport nonce is consumed before invocation and is not capture approval.
Retries use a fresh transport nonce and the same idempotent attempt ID.
No socket, role, key or service is installed by importing this module.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import hmac
import json
import os
import socket
import stat
import struct
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

from .staff_capture_executor import CaptureResult

DOMAIN = b"ctt.capture.unix-request.v1\x00"
RESPONSE_DOMAIN = b"ctt.capture.unix-response.v1\x00"
MAX_RESPONSE_BYTES = 2048
MAX_FRAME_BYTES = 100000
MAX_DECISION_BYTES = 65536


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _uuid(value):
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("invalid UUID")
    return parsed


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def encode_request(
    *,
    attempt_id: UUID,
    nonce: UUID,
    signed_decision: bytes,
    timestamp: int,
    key: bytes,
) -> bytes:
    """Create one framed internal request from trusted client configuration."""
    if (
        not isinstance(attempt_id, UUID)
        or not isinstance(nonce, UUID)
        or type(signed_decision) is not bytes
        or not 0 < len(signed_decision) <= MAX_DECISION_BYTES
        or type(timestamp) is not int
        or timestamp < 0
        or type(key) is not bytes
        or len(key) < 32
    ):
        raise ValueError("invalid transport request")
    body = dict(
        schema="ctt.capture.unix-request.v1",
        method="execute",
        attempt_id=str(attempt_id),
        nonce=str(nonce),
        timestamp=timestamp,
        signed_decision=base64.b64encode(signed_decision).decode("ascii"),
    )
    tag = hmac.new(key, DOMAIN + _canonical(body), hashlib.sha256).hexdigest()
    raw = _canonical({"body": body, "mac": tag})
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("invalid transport request")
    return struct.pack("!I", len(raw)) + raw


class CaptureUnixTransport:
    """Configured only by the trusted service bootstrap, never a request."""

    def __init__(
        self,
        *,
        key: bytes,
        allowed_uids: frozenset[int],
        consume_nonce: Callable[[UUID, int], Awaitable[bool]],
        handler: Callable[[UUID, bytes], Awaitable[CaptureResult]],
        request_timeout: float = 5.0,
        max_connections: int = 16,
        client_gid: int | None = None,
        max_clock_skew: int = 30,
        clock: Callable[[], float] = time.time,
    ):
        if (
            type(key) is not bytes
            or len(key) < 32
            or type(allowed_uids) is not frozenset
            or not allowed_uids
            or any(type(uid) is not int or uid < 0 for uid in allowed_uids)
            or not callable(consume_nonce)
            or not callable(handler)
            or type(request_timeout) not in (int, float)
            or not 0 < request_timeout <= 60
            or type(max_clock_skew) is not int
            or not 0 < max_clock_skew <= 60
            or type(max_connections) is not int
            or not 1 <= max_connections <= 128
            or (
                client_gid is not None
                and (type(client_gid) is not int or client_gid < 0)
            )
            or not callable(clock)
        ):
            raise ValueError("invalid transport configuration")
        self._key, self._uids = key, allowed_uids
        self._consume, self._handler = consume_nonce, handler
        self._timeout, self._skew, self._clock = (
            request_timeout,
            max_clock_skew,
            clock,
        )
        self._tasks: set[asyncio.Task[Any]] = set()
        self._max_connections = max_connections
        self._client_gid = client_gid
        self._serving = False

    async def _request(self, reader, writer, context):
        peer = writer.get_extra_info("socket")
        if peer is None or not hasattr(socket, "SO_PEERCRED"):
            raise ValueError("peer unavailable")
        _, uid, _ = struct.unpack(
            "3i",
            peer.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            ),
        )
        if uid not in self._uids:
            raise ValueError("peer denied")
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        if not 0 < size <= MAX_FRAME_BYTES:
            raise ValueError("frame denied")
        raw = await reader.readexactly(size)
        obj = json.loads(raw, object_pairs_hook=_pairs)
        if type(obj) is not dict or set(obj) != {"body", "mac"}:
            raise ValueError("envelope denied")
        body = obj["body"]
        if type(body) is not dict or set(body) != {
            "schema",
            "method",
            "attempt_id",
            "nonce",
            "timestamp",
            "signed_decision",
        }:
            raise ValueError("body denied")
        if (
            body["schema"] != "ctt.capture.unix-request.v1"
            or body["method"] != "execute"
            or type(obj["mac"]) is not str
            or type(body["timestamp"]) is not int
            or abs(int(self._clock()) - body["timestamp"]) > self._skew
            or _canonical(obj) != raw
        ):
            raise ValueError("request denied")
        expected = hmac.new(
            self._key, DOMAIN + _canonical(body), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(obj["mac"], expected):
            raise ValueError("MAC denied")
        attempt, nonce = _uuid(body["attempt_id"]), _uuid(body["nonce"])
        decision = base64.b64decode(body["signed_decision"], validate=True)
        if (
            not 0 < len(decision) <= MAX_DECISION_BYTES
            or base64.b64encode(decision).decode("ascii") != body["signed_decision"]
        ):
            raise ValueError("decision denied")
        context.update(
            attempt_id=str(attempt),
            nonce=str(nonce),
            request_hash="sha256:"
            + hashlib.sha256(struct.pack("!I", len(raw)) + raw).hexdigest(),
        )
        if await self._consume(nonce, body["timestamp"] + self._skew) is not True:
            raise ValueError("nonce denied")
        result = await self._handler(attempt, decision)
        if not isinstance(result, CaptureResult) or result.attempt_id != attempt:
            raise ValueError("handler denied")

    async def _connection(self, reader, writer):
        success = False
        context = dict(attempt_id=None, nonce=None, request_hash=None)
        try:
            async with asyncio.timeout(self._timeout):
                await self._request(reader, writer, context)
                success = True
        except Exception:
            pass
        finally:
            try:
                body = dict(
                    schema="ctt.capture.unix-response.v1",
                    ok=success,
                    **context,
                )
                raw = _canonical(
                    {
                        "body": body,
                        "mac": hmac.new(
                            self._key,
                            RESPONSE_DOMAIN + _canonical(body),
                            hashlib.sha256,
                        ).hexdigest(),
                    }
                )
                writer.write(struct.pack("!I", len(raw)) + raw)
                async with asyncio.timeout(self._timeout):
                    await writer.drain()
            except Exception:
                pass
            writer.close()
            try:
                async with asyncio.timeout(self._timeout):
                    await writer.wait_closed()
            except Exception:
                pass

    @asynccontextmanager
    async def serve(self, path: Path):
        """Bind an absent socket in a current-user-owned restricted directory.

        Never unlink an existing file/socket. The parent must be stable and
        owned by the service account; host or same-UID compromise is outside
        this boundary. A configured GID requires directory 0710/socket 0660;
        otherwise 0700/0600. Extended ACLs are rejected. Caller manages
        process limits and durable nonce storage.
        """
        if self._serving:
            raise ValueError("transport already serving")
        path = Path(path)
        if not path.is_absolute() or path.parent.resolve() != path.parent:
            raise ValueError("unsafe socket path")
        parent = path.parent.lstat()
        expected_mode = 0o700 if self._client_gid is None else 0o710
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != expected_mode
            or (self._client_gid is not None and parent.st_gid != self._client_gid)
        ):
            raise ValueError("unsafe socket directory")
        for name in ("system.posix_acl_access", "system.posix_acl_default"):
            try:
                os.getxattr(path.parent, name, follow_symlinks=False)
            except OSError as exc:
                if exc.errno not in (
                    errno.ENODATA,
                    errno.ENOTSUP,
                    errno.EOPNOTSUPP,
                ):
                    raise ValueError("socket ACL unavailable") from None
            else:
                raise ValueError("unaccredited socket ACL")
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("socket path already exists")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        owned = None
        server = None
        closing = False
        self._serving = True
        try:
            sock.bind(str(path))
            item = path.lstat()
            owned = (item.st_dev, item.st_ino)
            if self._client_gid is not None:
                os.chown(path, -1, self._client_gid)
            os.chmod(path, 0o600 if self._client_gid is None else 0o660)
            sock.listen(socket.SOMAXCONN)
            sock.setblocking(False)

            def connected(reader, writer):
                if closing or len(self._tasks) >= self._max_connections:
                    writer.close()
                    return
                task = asyncio.create_task(self._connection(reader, writer))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

            server = await asyncio.start_unix_server(
                connected, sock=sock, limit=MAX_FRAME_BYTES + 4
            )
            yield server
        finally:
            closing = True
            if server is not None:
                server.close()
                await server.wait_closed()
            else:
                sock.close()
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                item = path.lstat()
                if owned == (item.st_dev, item.st_ino) and stat.S_ISSOCK(item.st_mode):
                    path.unlink()
            except FileNotFoundError:
                pass
            self._serving = False


class CaptureTransportUncertain(RuntimeError):
    """No authenticated success was obtained; reconcile before retrying."""


@dataclass(frozen=True)
class CaptureAcknowledgement:
    """Authenticated success for one exact request, not human accreditation."""

    attempt_id: UUID
    nonce: UUID
    request_hash: str


def _check_acl(path: Path) -> None:
    for name in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.getxattr(path, name, follow_symlinks=False)
        except OSError as exc:
            if exc.errno not in (
                errno.ENODATA,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
            ):
                raise ValueError("ACL unavailable") from None
        else:
            raise ValueError("unaccredited ACL")


class CaptureUnixClient:
    """Pinned local writer identity and channel key from trusted config.

    Errors/timeouts do not prove rollback: the writer may already have
    committed. Reconcile by attempt ID and retry with a fresh transport nonce.
    """

    def __init__(
        self,
        *,
        path: Path,
        key: bytes,
        server_uid: int,
        client_gid: int | None = None,
        timeout: float = 5.0,
        clock: Callable[[], float] = time.time,
    ):
        if (
            type(key) is not bytes
            or len(key) < 32
            or type(server_uid) is not int
            or server_uid < 0
            or (
                client_gid is not None
                and (type(client_gid) is not int or client_gid < 0)
            )
            or type(timeout) not in (int, float)
            or not 0 < timeout <= 60
            or not callable(clock)
        ):
            raise ValueError("invalid client configuration")
        self._path, self._key, self._uid = Path(path), key, server_uid
        self._gid, self._timeout, self._clock = client_gid, timeout, clock

    def _check_path(self):
        path = self._path
        if not path.is_absolute() or path.parent.resolve() != path.parent:
            raise ValueError("unsafe socket path")
        parent, item = path.parent.lstat(), path.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or not stat.S_ISSOCK(item.st_mode)
            or parent.st_uid != self._uid
            or item.st_uid != self._uid
            or stat.S_IMODE(parent.st_mode) != (0o700 if self._gid is None else 0o710)
            or stat.S_IMODE(item.st_mode) != (0o600 if self._gid is None else 0o660)
            or (
                self._gid is not None
                and (parent.st_gid != self._gid or item.st_gid != self._gid)
            )
        ):
            raise ValueError("untrusted socket path")
        _check_acl(path.parent)
        _check_acl(path)

    async def execute(
        self,
        attempt_id: UUID,
        signed_decision: bytes,
        *,
        nonce: UUID,
    ) -> CaptureAcknowledgement:
        """Return only a MAC-verified acknowledgement of this exact request."""
        writer = None
        try:
            frame = encode_request(
                attempt_id=attempt_id,
                nonce=nonce,
                signed_decision=signed_decision,
                timestamp=int(self._clock()),
                key=self._key,
            )
            request_hash = "sha256:" + hashlib.sha256(frame).hexdigest()
            async with asyncio.timeout(self._timeout):
                self._check_path()
                reader, writer = await asyncio.open_unix_connection(
                    str(self._path), limit=MAX_RESPONSE_BYTES + 4
                )
                peer = writer.get_extra_info("socket")
                _, uid, _ = struct.unpack(
                    "3i",
                    peer.getsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_PEERCRED,
                        struct.calcsize("3i"),
                    ),
                )
                if uid != self._uid:
                    raise ValueError("untrusted server peer")
                writer.write(frame)
                await writer.drain()
                size = struct.unpack("!I", await reader.readexactly(4))[0]
                if not 0 < size <= MAX_RESPONSE_BYTES:
                    raise ValueError("response too large")
                raw = await reader.readexactly(size)
                obj = json.loads(raw, object_pairs_hook=_pairs)
                if type(obj) is not dict or set(obj) != {"body", "mac"}:
                    raise ValueError("invalid response")
                body = obj["body"]
                if (
                    type(body) is not dict
                    or set(body)
                    != {"schema", "ok", "attempt_id", "nonce", "request_hash"}
                    or body["schema"] != "ctt.capture.unix-response.v1"
                    or type(body["ok"]) is not bool
                    or body["attempt_id"] != str(attempt_id)
                    or body["nonce"] != str(nonce)
                    or body["request_hash"] != request_hash
                    or type(obj["mac"]) is not str
                    or _canonical(obj) != raw
                ):
                    raise ValueError("response binding mismatch")
                expected = hmac.new(
                    self._key,
                    RESPONSE_DOMAIN + _canonical(body),
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(obj["mac"], expected) or not body["ok"]:
                    raise ValueError("no authenticated success")
                return CaptureAcknowledgement(attempt_id, nonce, request_hash)
        except Exception:
            raise CaptureTransportUncertain("capture outcome uncertain") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(self._timeout):
                        await writer.wait_closed()
                except Exception:
                    pass
