"""Closed MCP tool catalog. Identity, SQL and write actions have no input slot."""

from __future__ import annotations

from typing import Any


def string(enum: list[str] | None = None, limit: int = 200) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": limit}
    if enum:
        result["enum"] = enum
    return result


PAGE = {"type": "integer", "minimum": 1, "maximum": 100}
TOOLS: dict[str, tuple[dict[str, Any], list[str]]] = {
    "samchat.resolve_reference": (
        {
            "reference_kind": string(["operations", "samchat", "base"]),
            "reference": string(),
            "tournament_id": string(),
        },
        ["reference_kind", "reference"],
    ),
    "samchat.document_status": ({"document_id": string()}, ["document_id"]),
    "samchat.document_diagnose": (
        {"document_id": string(), "intent": string(["approve"])},
        ["document_id", "intent"],
    ),
    "samchat.document_audit": (
        {"document_id": string(), "page_size": PAGE, "cursor": string(limit=4096)},
        ["document_id"],
    ),
    "samchat.study": (
        {
            "dataset": string(["documents", "budget"]),
            "version_id": string(),
            "edition_year": {"type": "integer", "minimum": 2000, "maximum": 2100},
            "tournament_id": string(),
            "date_from": string(limit=10),
            "date_to": string(limit=10),
            "state": string(),
            "kind": string(["INFORME", "SOLICITUD"]),
            "group_by": string(["state", "kind", "month", "tournament_id"]),
            "page_size": PAGE,
            "cursor": string(limit=4096),
        },
        ["date_from", "date_to"],
    ),
    "samchat.source_info": ({}, []),
    "samchat.source_read": (
        {
            "source_id": string(limit=64),
            "path": string(limit=250),
            "start_line": {"type": "integer", "minimum": 1, "maximum": 100000},
            "line_count": PAGE,
        },
        ["source_id", "path"],
    ),
}


def tools_list() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": "Receipt-gated scoped read; disabled unless composed.",
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }
        for name, (props, required) in TOOLS.items()
    ]
