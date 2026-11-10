"""Unmounted stateless JSON MCP ASGI factory. No server start, cookies or credentials.

resolve_connection(request) is a mandatory host adapter that verifies credentials
and returns a reference for the existing IdentityProvider; it is never a model
argument. current_connection is ContextVar-isolated and reset after every request.
"""

import json
from contextvars import ContextVar
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from devnous.gastos.services.batisenal_service import BatisenalError

from .framing import (
    PROTOCOL,
    SERVER_INFO,
    ProtocolError,
    modern_result,
    normalize_request,
)

_connection: ContextVar[str] = ContextVar("batisenal_connection")
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def current_connection():
    return _connection.get("")


def object_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("DUPLICATE_FIELD")
        value[key] = item
    return value


def create_app(
    bridge, *, resolve_connection=None, events=None, enabled=False, allowed_origins=()
):
    """Return inert-by-default ASGI with only the bridge's three case tools."""
    if events is not None:
        raise ValueError("PILOT_EVENTS_NOT_SUPPORTED")

    async def endpoint(request: Request):
        identifier = None
        token = None
        actor, method = None, "unknown"
        result: dict[str, Any]
        try:
            if enabled is not True or resolve_connection is None:
                raise BatisenalError("ACTIVATION_REQUIRED", 503)
            if request.url.query or request.headers.get("cookie"):
                raise BatisenalError("TRANSPORT_INVALID", 400)
            origin = request.headers.get("origin")
            if origin is not None and origin not in allowed_origins:
                raise BatisenalError("ORIGIN_DENIED", 403)
            if not request.headers.get("authorization", "").startswith("Bearer "):
                raise BatisenalError("UNAUTHENTICATED", 401)
            if (
                request.headers.get("content-type", "").split(";")[0]
                != "application/json"
            ):
                raise BatisenalError("CONTENT_TYPE_INVALID", 415)
            if "application/json" not in request.headers.get("accept", ""):
                raise BatisenalError("ACCEPT_INVALID", 406)
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 32768:
                    raise BatisenalError("INPUT_LIMIT", 413)
                body.extend(chunk)
            try:
                msg = json.loads(
                    body,
                    object_pairs_hook=object_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except (ValueError, UnicodeError) as exc:
                raise BatisenalError("INVALID_JSON") from exc
            if (
                not isinstance(msg, dict)
                or msg.get("jsonrpc") != "2.0"
                or not isinstance(msg.get("method"), str)
                or set(msg) - {"jsonrpc", "id", "method", "params"}
            ):
                raise BatisenalError("INVALID_REQUEST")
            identifier = msg.get("id")
            if identifier is not None and type(identifier) not in (str, int):
                raise BatisenalError("INVALID_REQUEST")
            method, params = msg["method"], msg.get("params", {})
            if not isinstance(params, dict):
                raise BatisenalError("INVALID_ARGUMENTS")
            msg = normalize_request(msg, request.scope["headers"])
            params = msg["params"]
            connection = await resolve_connection(request)
            if not isinstance(connection, str) or not connection:
                raise BatisenalError("UNAUTHENTICATED", 401)
            token = _connection.set(connection)
            async with bridge.sessions() as session:
                principal, binding, _ = await bridge.authorize(session)
            actor = str(principal.employee)
            bridge.audit_attempt(actor, method, "authorized_attempt")
            if method in {"server/discover", "ping"}:
                capabilities: dict[str, Any] = {"tools": {}}
                if method == "server/discover":
                    result = {
                        "supportedVersions": [PROTOCOL],
                        "capabilities": capabilities,
                        "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
                    }
                else:
                    result = {}
            elif method in {"tools/list", "tools/call"}:
                response = await bridge.handle(msg)
                if "result" in response:
                    response = {
                        **response,
                        "result": modern_result(
                            response["result"], cacheable=method == "tools/list"
                        ),
                    }
                return JSONResponse(response, headers=HEADERS)
            else:
                raise ProtocolError("Method not found", -32601, status=404)
            bridge.audit_attempt(str(principal.employee), method, "read_complete")
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "result": modern_result(
                        result, cacheable=method == "server/discover"
                    ),
                },
                headers=HEADERS,
            )
        except (BatisenalError, ProtocolError) as exc:
            if enabled is True and resolve_connection is not None:
                try:
                    bridge.audit_attempt(actor, method, exc.code)
                except BatisenalError as audit_error:
                    exc = audit_error
            rpc_code = {
                "INVALID_JSON": -32700,
                "INVALID_REQUEST": -32600,
                "INVALID_ARGUMENTS": -32602,
            }.get(exc.code, -32000)
            error: dict[str, Any] = {"code": rpc_code, "message": exc.code}
            if isinstance(exc, ProtocolError):
                error["code"] = exc.rpc_code
                if exc.data is not None:
                    error["data"] = exc.data
            return JSONResponse(
                {"jsonrpc": "2.0", "id": identifier, "error": error},
                status_code=exc.status,
                headers=HEADERS,
            )
        except Exception:
            try:
                bridge.audit_attempt(actor, method, "TRANSPORT_UNAVAILABLE")
            except Exception:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": identifier,
                        "error": {"code": -32000, "message": "AUDIT_UNAVAILABLE"},
                    },
                    status_code=503,
                    headers=HEADERS,
                )
            # Do not stringify host exceptions, tokens, payloads or callback material.
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": -32000, "message": "TRANSPORT_UNAVAILABLE"},
                },
                status_code=503,
                headers=HEADERS,
            )
        finally:
            if token is not None:
                _connection.reset(token)

    return Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
