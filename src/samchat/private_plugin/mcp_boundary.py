"""Unmounted MCP SDK boundary; no business tool is exposed or executable.

The eventual authenticated transport must supply a request-local connection
reference through ``current_connection``. This is a composition contract, not
an OAuth implementation. No transport, listener, credential or live runtime is
created here. Canon unchanged: this boundary preserves disabled authority.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError

from .contracts import AuditSink, IdentityProvider


def create_server(
    *,
    identities: IdentityProvider,
    audit: AuditSink,
    current_connection: Callable[[], str],
    now: Callable[[], int],
    issuer: str,
    audience: str,
    installation_id: str,
) -> Server:
    """Build an inert SDK server with trusted dependencies and no runtime mount.

    Refresh identity on every list/call request. Model arguments, including
    actor/scopes/credentials, are never interpreted. Empty discovery is not
    evidence of product parity; business integration remains explicitly absent.
    Audit persistence is solely an injected contract, unproven in production.
    """
    server = Server("samchat-private-review", version="0.0.0")

    def gate(action: str) -> str:
        actor = None
        code = "UNAUTHENTICATED"
        try:
            reference = current_connection()
            if not isinstance(reference, str) or not reference:
                raise ValueError("missing transport context")
            identity = identities.resolve_current(reference)
            if (
                identity.active is True
                and identity.revoked is False
                and type(identity.expires_at) is int
                and identity.expires_at > now()
                and identity.actor_id
                and identity.grant_id
                and identity.organization_id
                and identity.issuer == issuer
                and identity.audience == audience
                and identity.installation_id == installation_id
            ):
                actor = identity.actor_id
                code = "CAPABILITIES_DISABLED"
        except Exception:
            # Never stringify provider exceptions or transport references.
            code = "UNAUTHENTICATED"
        try:
            if not audit.append(actor_id=actor, action=action, outcome=code):
                return "AUDIT_UNAVAILABLE"
        except Exception:
            return "AUDIT_UNAVAILABLE"
        return code

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        code = gate("mcp.tools.list")
        if code != "CAPABILITIES_DISABLED":
            raise McpError(types.ErrorData(code=-32001, message=code))
        return []

    async def deny_call(request: types.CallToolRequest) -> types.ServerResult:
        # Deliberately do not access name/arguments: neither is a dispatcher.
        # Register directly because SDK call_tool() logs unknown tool names and
        # stringifies uncaught exceptions; both may contain sensitive input.
        code = gate("mcp.tools.call")
        return types.ServerResult(
            types.CallToolResult(
                isError=True,
                content=[types.TextContent(type="text", text=code)],
            )
        )

    server.request_handlers[types.CallToolRequest] = deny_call
    return server
