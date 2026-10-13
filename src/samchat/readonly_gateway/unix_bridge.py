"""Closed readonly bridge protocol over an owner-managed AF_UNIX socket.

No HTTP listener, credential, SQL, principal or actor is accepted in requests.
The server serializes calls and resolves its own expiring delegation each time.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Any, Callable

from .contracts import Principal, ReadError
from .live_informes import REPORT_TOOLS, ReportOnlyGateway

MAX_FRAME = 65536


def _peer_uid(connection: socket.socket) -> int:
    return int(
        struct.unpack(
            "3i",
            connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            ),
        )[1]
    )


def _receive(connection: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while True:
        chunk = connection.recv(min(4096, MAX_FRAME + 1 - len(data)))
        if not chunk:
            raise ReadError("SOURCE_UNAVAILABLE")
        data.extend(chunk)
        if len(data) > MAX_FRAME:
            raise ReadError("OUTPUT_LIMIT")
        if b"\n" in chunk:
            if not data.endswith(b"\n") or data.count(b"\n") != 1:
                raise ReadError("INVALID_ARGUMENTS")
            break
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ReadError("INVALID_ARGUMENTS") from exc
    if not isinstance(value, dict):
        raise ReadError("INVALID_ARGUMENTS")
    return value


def _send(connection: socket.socket, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise ReadError("OUTPUT_LIMIT")
    connection.sendall(data)


def _deny(connection: socket.socket, code: str) -> None:
    try:
        _send(connection, {"error": code})
    except OSError:
        # A disconnected rejected peer must not terminate the accept loop.
        return


class UnixReadServer:
    def __init__(
        self,
        path: Path,
        gateway: ReportOnlyGateway,
        resolve: Callable[[], Principal],
        peer_uid: int,
    ) -> None:
        if not path.is_absolute() or peer_uid < 0:
            raise ReadError("ACTIVATION_REQUIRED")
        self.path, self.gateway, self.resolve = path, gateway, resolve
        self.peer_uid = peer_uid

    def handle(self, connection: socket.socket) -> None:
        connection.settimeout(15)
        try:
            if _peer_uid(connection) != self.peer_uid:
                # Drain one bounded frame before closing. Closing with unread
                # request bytes can reset AF_UNIX and discard the denial reply.
                # No delegation or gateway dispatch is performed for this peer.
                connection.settimeout(1)
                try:
                    _receive(connection)
                except (ReadError, OSError):
                    raise ReadError("AUTH_REQUIRED") from None
                raise ReadError("AUTH_REQUIRED")
            request = _receive(connection)
            if (
                set(request) != {"operation", "arguments"}
                or request["operation"] not in REPORT_TOOLS
                or not isinstance(request["arguments"], dict)
            ):
                raise ReadError("INVALID_ARGUMENTS")
            result = self.gateway.call(
                request["operation"], request["arguments"], self.resolve()
            )
            _send(connection, {"result": result})
        except ReadError as exc:
            _deny(connection, exc.code)
        except Exception:
            # Never expose database errors, delegation content or filesystem paths.
            _deny(connection, "SOURCE_UNAVAILABLE")

    def serve(self) -> None:
        # An existing socket is a startup error, not permission to unlink it.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o660)
            listener.listen(2)
            try:
                while True:
                    connection, _ = listener.accept()
                    with connection:
                        self.handle(connection)
            finally:
                self.path.unlink()


class UnixReportProxy:
    allowed_tools = REPORT_TOOLS

    def __init__(self, path: Path, server_uid: int = 988) -> None:
        if not path.is_absolute() or server_uid < 0:
            raise ReadError("ACTIVATION_REQUIRED")
        self.path, self.server_uid = path, server_uid

    def call(
        self,
        operation: str,
        arguments: dict[str, Any],
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        if principal is not None or operation not in REPORT_TOOLS:
            raise ReadError("AUTH_REQUIRED")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(15)
                connection.connect(str(self.path))
                if _peer_uid(connection) != self.server_uid:
                    raise ReadError("AUTH_REQUIRED")
                _send(connection, {"operation": operation, "arguments": arguments})
                response = _receive(connection)
            if set(response) == {"error"} and isinstance(response["error"], str):
                raise ReadError(response["error"])
            if set(response) != {"result"} or not isinstance(response["result"], dict):
                raise ReadError("SOURCE_UNAVAILABLE")
            return response["result"]
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError("SOURCE_UNAVAILABLE") from exc
