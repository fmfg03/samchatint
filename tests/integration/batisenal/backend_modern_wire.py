"""Synthetic fixture wire encoding only; never supplies identity or grants."""

PROTOCOL = "2026-07-28"


def mcp_request(method, params=None):
    values = dict(params or {})
    if "_meta" in values:
        raise ValueError("Fixture metadata is fixed")
    headers = {
        "Accept": "application/json",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = values["name"]
    values["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "synthetic-composition-client",
            "version": "1",
        },
    }
    return headers, {"jsonrpc": "2.0", "id": 1, "method": method, "params": values}
