"""Explicit local MCP read composition; never mounted by the web application.

This factory accepts trusted identity, canonical reader and audit dependencies.
It is not an OAuth/HTTP server, feature flag or permission to wire live stores.
The default inert factory in mcp_boundary remains unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError

from .contracts import AuditSink, Denied, Identity, IdentityProvider
from .direction_read import DIRECTION_OUTPUT_SCHEMA, DIRECTION_SCOPES_OUTPUT_SCHEMA
from .perimeter import digest


class ReadAuditSink(AuditSink, Protocol):
    def append_read(
        self, *, actor_id: str, action: str, evidence_digest: str, scope_digest: str
    ) -> str:
        """Persist a read receipt binding exact output and authorized scope.

        Never retain raw report/PII/token payloads. Failure must suppress output.
        A fixture implementation proves no production durability or retention.
        """


class DirectionReader(Protocol):
    async def list_scopes(
        self, *, identity: Identity, cursor: str | None = None
    ) -> dict:
        """List current canonical portfolio/tournament selectors only."""

    async def read(
        self,
        *,
        identity: Identity,
        portfolio_id: str | None = None,
        tournament_id: str | None = None,
        year: int = 2026,
    ) -> dict:
        """Repeat canonical employee/scope/source authorization before reading."""


PROFILE_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string", "minLength": 1, "pattern": r"\S"}},
    "required": ["id"],
    "additionalProperties": False,
}
EMPTY_INPUT = {"type": "object", "properties": {}, "additionalProperties": False}
SCOPES_INPUT = {
    "type": "object",
    "properties": {"cursor": {"type": "string", "minLength": 1, "maxLength": 1024}},
    "additionalProperties": False,
}
DIRECTION_INPUT = {
    "type": "object",
    "properties": {
        "portfolio_id": {"type": "string", "format": "uuid"},
        "tournament_id": {"type": "string", "format": "uuid"},
        "year": {"type": "integer", "minimum": 2000, "maximum": 2100},
    },
    "required": ["year"],
    "additionalProperties": False,
}


def _descriptor(name: str, description: str, schema: dict, scopes: list) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        inputSchema=schema,
        annotations=types.ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, openWorldHint=False
        ),
        securitySchemes=[{"type": "oauth2", "scopes": scopes}],
    )


def descriptors(identity: Identity) -> list[types.Tool]:
    """Finite local catalog; scope membership restricts, never grants authority."""
    profile = _descriptor(
        "get_profile",
        "Read the single account linked to the current authenticated connection.",
        EMPTY_INPUT,
        [],
    )
    profile.outputSchema = PROFILE_SCHEMA
    profile.meta = {"openai/profile": True}
    result = [profile]
    if "direction:read" in identity.oauth_scopes:
        direction = _descriptor(
            "direction_read_summary",
            "Read a minimized Direction summary in the current employee's "
            "authorized portfolios and tournaments. Specific denials prevail. "
            "Indicators cover the full selected scope; scope_manifest identifies "
            "large scopes whose ID arrays are omitted. Enumerate with direction_list_scopes. "
            "Unavailable facts remain gaps. Does not approve or pay anything.",
            DIRECTION_INPUT,
            ["direction:read"],
        )
        direction.outputSchema = DIRECTION_OUTPUT_SCHEMA
        result.append(direction)
        scopes = _descriptor(
            "direction_list_scopes",
            "List IDs and labels of portfolios and tournaments currently "
            "authorized for this employee. Follow pagination.next_cursor for all pages. "
            "Summary indicators cover the full scope even when ID arrays are omitted; "
            "scope_manifest records counts, completeness and digest. No personal records.",
            SCOPES_INPUT,
            ["direction:read"],
        )
        scopes.outputSchema = DIRECTION_SCOPES_OUTPUT_SCHEMA
        result.append(scopes)
    return result


def _selectors(arguments: dict) -> dict:
    if set(arguments) - {"portfolio_id", "tournament_id", "year"}:
        raise Denied("INVALID_ARGUMENTS")
    year = arguments.get("year")
    if type(year) is not int or not 2000 <= year <= 2100:
        raise Denied("INVALID_ARGUMENTS")
    result = {"year": year}
    for key in ("portfolio_id", "tournament_id"):
        if key not in arguments:
            continue
        value = arguments[key]
        if not isinstance(value, str) or len(value) != 36:
            raise Denied("INVALID_ARGUMENTS")
        try:
            result[key] = str(UUID(value))
        except ValueError:
            raise Denied("INVALID_ARGUMENTS") from None
    return result


def create_local_read_server(
    *,
    identities: IdentityProvider,
    audit: ReadAuditSink,
    direction: DirectionReader,
    current_connection: Callable[[], str],
    now: Callable[[], int],
    issuer: str,
    audience: str,
    installation_id: str,
) -> Server:
    """Construct only an in-process SDK object; callers supply no runtime config.

    No model-provided identity, arbitrary handler, query, code or bearer is used.
    Authentication is re-resolved on list/call, plus after a domain read. A failed
    audit prevents results from being released. Persistence remains an adapter
    obligation, not proven by this composition or its synthetic tests.
    """
    server = Server("samchat-local-read-review", version="0.1.0")

    def authenticate() -> Identity:
        try:
            reference = current_connection()
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError()
            identity = identities.resolve_current(reference)
            if (
                not isinstance(identity, Identity)
                or identity.active is not True
                or identity.revoked is not False
                or type(identity.expires_at) is not int
                or identity.expires_at <= now()
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in (
                        identity.actor_id,
                        identity.grant_id,
                        identity.organization_id,
                        identity.profile_id,
                    )
                )
                or identity.issuer != issuer
                or identity.audience != audience
                or identity.installation_id != installation_id
                or not isinstance(identity.oauth_scopes, frozenset)
            ):
                raise ValueError()
            return identity
        except Exception:
            raise Denied("UNAUTHENTICATED") from None

    def record(identity: Identity | None, action: str, outcome: str) -> str:
        try:
            receipt = audit.append(
                actor_id=identity.actor_id if identity else None,
                action=action,
                outcome=outcome,
            )
            if not isinstance(receipt, str) or not receipt:
                raise ValueError()
            return receipt
        except Exception:
            raise Denied("AUDIT_UNAVAILABLE") from None

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        identity = None
        try:
            identity = authenticate()
            record(identity, "mcp.tools.list", "READ_CATALOG")
            return descriptors(identity)
        except Denied as exc:
            code = str(exc)
            if code != "AUDIT_UNAVAILABLE":
                try:
                    record(identity, "mcp.tools.list", code)
                except Denied:
                    code = "AUDIT_UNAVAILABLE"
            raise McpError(types.ErrorData(code=-32001, message=code)) from None

    async def call_tool(request: types.CallToolRequest) -> types.ServerResult:
        identity = None
        action = "unregistered"
        try:
            identity = authenticate()
            if request.params.name not in {
                "get_profile",
                "direction_read_summary",
                "direction_list_scopes",
            }:
                raise Denied("CAPABILITY_DISABLED")
            action = request.params.name
            arguments = request.params.arguments
            if arguments is None:
                arguments = {}
            if type(arguments) is not dict:
                raise Denied("INVALID_ARGUMENTS")
            if action == "get_profile":
                if arguments:
                    raise Denied("INVALID_ARGUMENTS")
                data = {"id": identity.profile_id}
            else:
                if "direction:read" not in identity.oauth_scopes:
                    raise Denied("FORBIDDEN")
                if action == "direction_list_scopes":
                    if not Draft202012Validator(SCOPES_INPUT).is_valid(arguments):
                        raise Denied("INVALID_ARGUMENTS")
                    selectors = arguments
                else:
                    selectors = _selectors(arguments)
                record(identity, action, "READ_REQUESTED")
                if action == "direction_list_scopes":
                    data = await direction.list_scopes(identity=identity, **selectors)
                    output_schema = DIRECTION_SCOPES_OUTPUT_SCHEMA
                else:
                    data = await direction.read(identity=identity, **selectors)
                    output_schema = DIRECTION_OUTPUT_SCHEMA
                # No cross-user connection switch or revocation during the read.
                if authenticate() != identity:
                    raise Denied("UNAUTHENTICATED")
                if not Draft202012Validator(output_schema).is_valid(data):
                    raise Denied("CANONICAL_RESULT_INVALID")
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > 65536:
                raise Denied("CANONICAL_RESULT_INVALID")
            try:
                receipt = audit.append_read(
                    actor_id=identity.actor_id,
                    action=action,
                    evidence_digest=digest(data),
                    scope_digest=digest(
                        {
                            "installation": identity.installation_id,
                            "organization": identity.organization_id,
                            "actor": identity.actor_id,
                            "grant": identity.grant_id,
                            "scope": data.get("scope", {}),
                            "scope_manifest": data.get("scope_manifest"),
                            "tournament_ids": data.get(
                                "tournament_ids",
                                [row["id"] for row in data.get("tournaments", [])],
                            ),
                        }
                    ),
                )
                if not isinstance(receipt, str) or not receipt:
                    raise ValueError()
            except Exception:
                raise Denied("AUDIT_UNAVAILABLE") from None
            # Persistence can wait on a lock; do not release data under a grant
            # that expired, changed identity or was revoked during that wait.
            if authenticate() != identity:
                raise Denied("UNAUTHENTICATED")
            return types.ServerResult(
                types.CallToolResult(
                    isError=False,
                    content=[types.TextContent(type="text", text=encoded)],
                    structuredContent=data,
                    _meta={"samchat/receipt": receipt, "samchat/localReview": True},
                )
            )
        except Exception as exc:
            # Dependency messages, names and tool payloads are never reflected.
            code = str(exc) if isinstance(exc, Denied) else "DEPENDENCY_UNAVAILABLE"
            if code not in {
                "UNAUTHENTICATED",
                "FORBIDDEN",
                "INVALID_ARGUMENTS",
                "CAPABILITY_DISABLED",
                "AUDIT_UNAVAILABLE",
                "SCOPE_UNPROVEN",
                "CANONICAL_RESULT_INVALID",
                "DEPENDENCY_UNAVAILABLE",
                "ORGANIZATION_UNPROVEN",
                "SCOPE_CHANGED",
                "SOURCE_UNAVAILABLE",
            }:
                code = "DEPENDENCY_UNAVAILABLE"
            if code != "AUDIT_UNAVAILABLE":
                try:
                    record(identity, action, code)
                except Denied:
                    code = "AUDIT_UNAVAILABLE"
            return types.ServerResult(
                types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=code)]
                )
            )

    # SDK decorator logs unknown names; typed handler avoids reflecting input.
    server.request_handlers[types.CallToolRequest] = call_tool
    return server
