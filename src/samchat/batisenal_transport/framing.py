"""Bounded modern MCP framing; metadata never supplies identity or authority.

Supports the 2026-07-28 core request envelope. Optional extensions not explicitly
implemented are rejected, not forwarded to domain handlers. No network or state.
"""

import base64
import binascii
import re

PROTOCOL = "2026-07-28"
VERSION = "io.modelcontextprotocol/protocolVersion"
CLIENT = "io.modelcontextprotocol/clientInfo"
CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
LOG_LEVEL = "io.modelcontextprotocol/logLevel"
META_FIELDS = {VERSION, CLIENT, CAPABILITIES, LOG_LEVEL, "progressToken"}
LOG_LEVELS = {
    "debug",
    "info",
    "notice",
    "warning",
    "error",
    "critical",
    "alert",
    "emergency",
}
SERVER_INFO = {"name": "samchat-batisenal-candidate", "version": "0.1.1"}
LABEL = r"[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
EXTENSION_KEY = (
    rf"{LABEL}(?:\.{LABEL})*/(?:[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)?"
)


class ProtocolError(ValueError):
    def __init__(self, code, rpc_code, *, status=400, data=None):
        super().__init__(code)
        self.code, self.rpc_code, self.status, self.data = code, rpc_code, status, data


def invalid():
    raise ProtocolError("INVALID_ARGUMENTS", -32602)


def header_error():
    raise ProtocolError("HeaderMismatch", -32020)


def field_text(value):
    return isinstance(value, str) and 0 < len(value) <= 2048


def header_value(headers, name, *, encoded=False, required=True):
    """Take raw ASGI pairs so duplicate headers cannot select another value."""
    values = [v for k, v in headers if k.lower() == name.encode("ascii")]
    if not values and not required:
        return None
    if len(values) != 1:
        header_error()
    try:
        value = values[0].decode("ascii")
    except UnicodeError:
        header_error()
    if (
        not value
        or len(value) > 4096
        or value.strip() != value
        or any(ord(c) < 32 or ord(c) > 126 for c in value)
    ):
        header_error()
    if encoded and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeError, binascii.Error):
            header_error()
    return value


def validate_client_info(value):
    if not isinstance(value, dict) or not {"name", "version"} <= set(value):
        invalid()
    text_fields = {"name", "version", "title", "description", "websiteUrl"}
    if set(value) - text_fields - {"icons"}:
        invalid()
    if any(not isinstance(v, str) for k, v in value.items() if k in text_fields):
        invalid()
    if "icons" in value:
        if not isinstance(value["icons"], list):
            invalid()
        for icon in value["icons"]:
            if (
                not isinstance(icon, dict)
                or "src" not in icon
                or set(icon) - {"src", "mimeType", "sizes", "theme"}
                or any(not isinstance(v, str) for k, v in icon.items() if k != "sizes")
                or ("theme" in icon and icon["theme"] not in {"light", "dark"})
                or (
                    "sizes" in icon
                    and (
                        not isinstance(icon["sizes"], list)
                        or any(not isinstance(v, str) for v in icon["sizes"])
                    )
                )
            ):
                invalid()
    # websiteUrl/icons are descriptive strings only. Never fetch or log them.


def validate_capabilities(value):
    if not isinstance(value, dict) or set(value) - {
        "roots",
        "sampling",
        "elicitation",
        "experimental",
        "extensions",
    }:
        invalid()
    for key, entry in value.items():
        if not isinstance(entry, dict):
            invalid()
        if key == "roots" and entry:
            invalid()
        if key == "sampling" and set(entry) - {"context", "tools"}:
            invalid()
        if key == "elicitation" and set(entry) - {"form", "url"}:
            invalid()
        if key != "roots" and any(not isinstance(v, dict) for v in entry.values()):
            invalid()
        if key == "extensions" and any(
            not re.fullmatch(EXTENSION_KEY, name) for name in entry
        ):
            invalid()
    # Declarations are ignored; no sampling, roots, elicitation or extension
    # handler is activated, and no capability is inherited across requests.


def normalize_request(message, headers):
    """Return a new domain envelope after validating modern transport metadata."""
    version_header = header_value(headers, "mcp-protocol-version")
    params = message.get("params", {})
    meta = params.get("_meta")
    requested = meta.get(VERSION) if isinstance(meta, dict) else None
    if requested is not None and requested != version_header:
        header_error()
    if version_header != PROTOCOL:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", version_header):
            header_error()
        raise ProtocolError(
            "Unsupported protocol version",
            -32022,
            data={"supported": [PROTOCOL], "requested": version_header},
        )
    if not isinstance(meta, dict) or set(meta) - META_FIELDS:
        invalid()
    if not {VERSION, CAPABILITIES} <= set(meta) or requested != PROTOCOL:
        invalid()
    validate_capabilities(meta[CAPABILITIES])
    if CLIENT in meta:
        validate_client_info(meta[CLIENT])
    if LOG_LEVEL in meta and (
        not isinstance(meta[LOG_LEVEL], str) or meta[LOG_LEVEL] not in LOG_LEVELS
    ):
        invalid()
    if "progressToken" in meta and type(meta["progressToken"]) not in (str, int, float):
        invalid()
    if "id" not in message or message["id"] is None:
        raise ProtocolError("INVALID_REQUEST", -32600)
    method = message["method"]
    if header_value(headers, "mcp-method") != method:
        header_error()
    clean = {k: v for k, v in params.items() if k != "_meta"}
    name_field = {
        "tools/call": "name",
        "prompts/get": "name",
        "resources/read": "uri",
    }.get(method)
    if name_field:
        if not field_text(clean.get(name_field)):
            invalid()
        if header_value(headers, "mcp-name", encoded=True) != clean[name_field]:
            header_error()
    elif header_value(headers, "mcp-name", required=False) is not None:
        header_error()
    if method in {"server/discover", "ping", "tools/list"} and clean:
        # No pagination cursor is issued by this finite, single-page catalog.
        invalid()
    if method == "tools/call" and (
        set(clean) != {"name", "arguments"} or not isinstance(clean["arguments"], dict)
    ):
        invalid()
    return {**message, "params": clean}


def modern_result(result, *, cacheable=False):
    """Enrich transport fields only; never cache account-bound results publicly."""
    enriched = {**result, "resultType": "complete"}
    if cacheable:
        enriched.update(ttlMs=0, cacheScope="private")
    return enriched
