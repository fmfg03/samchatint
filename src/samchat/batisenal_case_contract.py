"""Unmounted v0.1 wire contract; never resolves identity or grants authority.

Call parse_json before authentication only for bounded syntactic parsing. Call
validate_request after authenticating and authorizing the referenced object.
canonical_request_hash accepts ONLY a server-derived binding. These helpers do
not persist snapshots/drafts or replace the canonical owner/lock protocol.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 32 * 1024
TOOLS = frozenset({"get_case_context", "submit_case_draft", "get_case_operation"})


class ContractError(ValueError):
    """Fixed public codes only; never include user input or exception messages."""


def _invalid() -> None:
    raise ContractError("INVALID_REQUEST")


def _string(value: Any, limit: int, *, nonempty: bool = True) -> None:
    if type(value) is not str or len(value) > limit or (nonempty and not value):
        _invalid()
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        _invalid()


def _object(value: Any, fields: set[str]) -> None:
    if type(value) is not dict or set(value) != fields:
        _invalid()


def _array(value: Any, maximum: int) -> None:
    if type(value) is not list or len(value) > maximum:
        _invalid()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("INVALID_JSON")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ContractError("INVALID_JSON")


def _unicode(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ContractError("INVALID_JSON")
    elif isinstance(value, dict):
        for key, item in value.items():
            _unicode(key)
            _unicode(item)
    elif isinstance(value, list):
        for item in value:
            _unicode(item)


def parse_json(raw: bytes) -> dict[str, Any]:
    """Bounded syntax only; does not consult objects or authorize requests."""
    if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
        raise ContractError("INVALID_JSON")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        _unicode(value)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("INVALID_JSON") from None
    if type(value) is not dict:
        raise ContractError("INVALID_JSON")
    return value


def validate_request(tool: str, value: dict[str, Any]) -> None:
    """Semantic validation ONLY after the caller's current object authority check."""
    common = {"schema_version", "case_ref"}
    if tool == "get_case_context":
        _object(value, common)
    elif tool == "get_case_operation":
        if type(value) is not dict:
            _invalid()
        selectors = set(value) & {"operation_id", "idempotency_key"}
        if len(selectors) != 1:
            _invalid()
        _object(value, common | selectors)
        _string(value[next(iter(selectors))], 128)
    elif tool == "submit_case_draft":
        _object(
            value,
            common
            | {
                "snapshot_id",
                "expected_snapshot_version",
                "idempotency_key",
                "proposal",
            },
        )
        for key in ("snapshot_id", "expected_snapshot_version", "idempotency_key"):
            _string(value[key], 128)
        proposal = value["proposal"]
        _object(proposal, {"summary", "claims", "recommendations", "uncertainties"})
        _string(proposal["summary"], 2000)
        _array(proposal["claims"], 20)
        for claim in proposal["claims"]:
            _object(claim, {"text", "source_refs"})
            _string(claim["text"], 2000)
            _array(claim["source_refs"], 8)
            if not claim["source_refs"]:
                _invalid()
            for source in claim["source_refs"]:
                _object(source, {"source_id", "version"})
                _string(source["source_id"], 128)
                _string(source["version"], 128)
        for key in ("recommendations", "uncertainties"):
            # Their byte limit bounds cardinality; the spec sets no count limit.
            if type(proposal[key]) is not list:
                _invalid()
            for item in proposal[key]:
                _string(item, 2000)
    else:
        _invalid()
    if value["schema_version"] != "1":
        _invalid()
    _string(value["case_ref"], 128)
    if len(canonical_json(value)) > MAX_REQUEST_BYTES:
        _invalid()


def canonical_json(value: Any) -> bytes:
    """Spec v1: ASCII-sorted keys; UTF-8; no normalization; string scalars only.

    Unlike json.dumps, escapes all controls as lowercase \\u00xx (not \\n).
    Arrays retain order. Reject identity-shaped unknown fields at validation,
    not here: this serializer also hashes trusted server binding dictionaries.
    """

    def emit(item: Any) -> str:
        if type(item) is str:
            _string(item, MAX_REQUEST_BYTES, nonempty=False)
            return (
                '"'
                + "".join(
                    (
                        '\\"'
                        if char == '"'
                        else (
                            "\\\\"
                            if char == "\\"
                            else f"\\u{ord(char):04x}" if ord(char) < 32 else char
                        )
                    )
                    for char in item
                )
                + '"'
            )
        if type(item) is list:
            return "[" + ",".join(emit(child) for child in item) + "]"
        if type(item) is dict:
            if any(type(key) is not str or not key.isascii() for key in item):
                _invalid()
            return (
                "{"
                + ",".join(emit(key) + ":" + emit(item[key]) for key in sorted(item))
                + "}"
            )
        _invalid()
        raise AssertionError("unreachable")

    try:
        return emit(value).encode("utf-8")
    except RecursionError:
        raise ContractError("INVALID_REQUEST") from None


def canonical_request_hash(
    request: dict[str, Any], *, server_binding: dict[str, str]
) -> str:
    """No authority here: binding MUST come from locked canonical server state."""
    validate_request("submit_case_draft", request)
    _object(
        server_binding,
        {"actor", "organization", "project", "case", "grant", "client"},
    )
    for value in server_binding.values():
        _string(value, 512)
    return hashlib.sha256(
        canonical_json(
            {
                "binding": server_binding,
                "operation": "submit_case_draft",
                "request": request,
            }
        )
    ).hexdigest()


def validate_source_refs(
    request: dict[str, Any], *, snapshot_sources: list[dict[str, str]]
) -> None:
    """Validate references against the authorized snapshot, never retrieve URLs.

    Does not assert factual entailment. The human must review whether a source
    supports a claim. Hostile source content is never interpreted as a command.
    """
    validate_request("submit_case_draft", request)
    _array(snapshot_sources, 12)
    sources: dict[str, str] = {}
    for source in snapshot_sources:
        _object(source, {"source_id", "version", "kind", "excerpt"})
        _string(source["source_id"], 128)
        _string(source["version"], 128)
        _string(source["kind"], 128)
        _string(source["excerpt"], 2000, nonempty=False)
        if source["source_id"] in sources:
            _invalid()
        sources[source["source_id"]] = source["version"]
    for claim in request["proposal"]["claims"]:
        for ref in claim["source_refs"]:
            if sources.get(ref["source_id"]) != ref["version"]:
                raise ContractError("SOURCE_UNAVAILABLE")


def _utc(value: Any) -> datetime:
    _string(value, 40)
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)",
        value,
    ):
        _invalid()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("INVALID_REQUEST") from None


def encode_response(tool: str, value: dict[str, Any]) -> bytes:
    """Validate the proposed output and bound the *actual* UTF-8 wire bytes.

    No authorization is provided here. The host must hold canonical locks and
    revalidate current access before releasing these bytes. A receipt's shape
    does not prove commit, immutability, or current usability. No truncation or
    invented zero is used when a source cannot populate the proposed schema.
    """
    if type(tool) is not str:
        _invalid()
    if tool == "get_case_context":
        _object(
            value,
            {
                "schema_version",
                "case_ref",
                "snapshot_id",
                "snapshot_version",
                "captured_at",
                "grant_expires_at",
                "report",
                "sources",
                "omissions",
                "complete_for_scope",
            },
        )
        if value["schema_version"] != "1":
            _invalid()
        for key in ("case_ref", "snapshot_id", "snapshot_version"):
            _string(value[key], 128)
        captured = _utc(value["captured_at"])
        remaining = _utc(value["grant_expires_at"]) - captured
        if not timedelta(0) < remaining <= timedelta(hours=1):
            _invalid()
        report = value["report"]
        _object(report, {"status", "currency", "claimed_total"})
        _string(report["status"], 128)
        _string(report["currency"], 3)
        if not re.fullmatch(r"[A-Z]{3}", report["currency"]):
            _invalid()
        _string(report["claimed_total"], 128)
        if not re.fullmatch(
            r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", report["claimed_total"]
        ):
            _invalid()
        # Reuse source validation without interpreting excerpts or manufacturing
        # claims. These still must be the sources authorized by the host.
        _array(value["sources"], 12)
        identifiers = set()
        for source in value["sources"]:
            _object(source, {"source_id", "version", "kind", "excerpt"})
            for key in ("source_id", "version", "kind"):
                _string(source[key], 128)
            _string(source["excerpt"], 2000, nonempty=False)
            if source["source_id"] in identifiers:
                _invalid()
            identifiers.add(source["source_id"])
        if type(value["omissions"]) is not list:
            _invalid()
        for omission in value["omissions"]:
            _string(omission, 2000)
        if type(value["complete_for_scope"]) is not bool:
            _invalid()
    elif tool in {"get_case_operation", "submit_case_draft"}:
        if tool == "get_case_operation" and value == {"status": "not_found"}:
            return b'{"status":"not_found"}'
        _object(value, {"status", "usability", "receipt"})
        _string(value["status"], 16)
        _string(value["usability"], 16)
        if value["status"] != "stored" or value["usability"] not in {
            "current",
            "stale",
        }:
            _invalid()
        receipt = value["receipt"]
        _object(
            receipt, {"operation_id", "draft_id", "committed_at", "snapshot_version"}
        )
        for key in ("operation_id", "draft_id", "snapshot_version"):
            _string(receipt[key], 128)
        _utc(receipt["committed_at"])
    else:
        _invalid()
    wire = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(wire) > MAX_RESPONSE_BYTES:
        raise ContractError("RESPONSE_LIMIT_EXCEEDED")
    return wire
