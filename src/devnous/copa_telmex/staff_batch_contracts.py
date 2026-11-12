"""Authority-free, deterministic source identities for batch review.

These values describe evidence, never permission to capture or human review.
Original PDF identities remain distinct when a document crosses PDF parts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from pathlib import PurePath
from typing import Any, Mapping, Sequence
from uuid import UUID

SOURCE_SCHEMA = "ctt.registration.source-manifest.v2"
MAX_DOCUMENTS = 300
MAX_PDFS = 16
MAX_DOCUMENT_PAGES = 4
MAX_SOURCE_PAGES = 1200
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")


class BatchContractError(ValueError):
    """A bounded, non-personal error safe to return through a private channel."""


def _json_tree(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise BatchContractError("BATCH_STRUCTURE_INVALID")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) in (list, tuple):
        for child in value:
            _json_tree(child, depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for child in value.values():
            _json_tree(child, depth + 1)
        return
    raise BatchContractError("BATCH_STRUCTURE_INVALID")


def canonical(value: Any) -> bytes:
    """Encode a strict JSON tree; reject key coercion, NaN and foreign objects."""
    _json_tree(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    """Return an unprefixed SHA-256 of canonical JSON."""
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_loads(raw: bytes, limit: int = 8 * 1024 * 1024) -> Any:
    """Reject duplicate keys and excessive JSON nesting before decoding."""
    if type(raw) is not bytes or type(limit) is not int or not 0 < len(raw) <= limit:
        raise BatchContractError("BATCH_STRUCTURE_INVALID")
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
            if depth > 64:
                raise BatchContractError("BATCH_STRUCTURE_INVALID")
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                raise BatchContractError("BATCH_STRUCTURE_INVALID")
    if depth or quoted:
        raise BatchContractError("BATCH_STRUCTURE_INVALID")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BatchContractError("BATCH_STRUCTURE_INVALID")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=pairs)
        _json_tree(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise BatchContractError("BATCH_STRUCTURE_INVALID") from None


def sha256_hex(value: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise BatchContractError("BATCH_HASH_INVALID")
    return value


def canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value) if type(value) is str else None
        if parsed is None or parsed.int == 0 or str(parsed) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise BatchContractError("BATCH_ID_INVALID") from None


def source_refs(source_slices: Sequence[Mapping]) -> tuple[tuple[str, int], ...]:
    """Flatten physical references in logical document order, without sorting."""
    if type(source_slices) is not list or not source_slices:
        raise BatchContractError("BATCH_SOURCE_INVALID")
    refs = []
    for item in source_slices:
        if type(item) is not dict or set(item) != {"pdf_sha256", "pages"}:
            raise BatchContractError("BATCH_SOURCE_INVALID")
        pdf = sha256_hex(item["pdf_sha256"])
        pages = item["pages"]
        if type(pages) is not list or not pages:
            raise BatchContractError("BATCH_SOURCE_INVALID")
        for number in pages:
            if type(number) is not int or number < 1:
                raise BatchContractError("BATCH_SOURCE_INVALID")
            refs.append((pdf, number))
    if not 1 <= len(refs) <= MAX_DOCUMENT_PAGES or len(set(refs)) != len(refs):
        raise BatchContractError("BATCH_SOURCE_INVALID")
    return tuple(refs)


def stable_document_id(edition_id: str, ordered_refs: Sequence) -> str:
    """Identity excludes model, partition, payload, render and approval versions."""
    edition_id = canonical_uuid(edition_id)
    refs = []
    for pdf, page in ordered_refs:
        if type(page) is not int or page < 1:
            raise BatchContractError("BATCH_SOURCE_INVALID")
        refs.append([sha256_hex(pdf), page])
    if not 1 <= len(refs) <= MAX_DOCUMENT_PAGES or len({tuple(r) for r in refs}) != len(
        refs
    ):
        raise BatchContractError("BATCH_SOURCE_INVALID")
    return digest(
        {
            "domain": "ctt.source-document.v2",
            "edition_id": edition_id,
            "source_refs": refs,
        }
    )


def validate_source_manifest(raw: Any, expected_edition_id: str | None = None) -> dict:
    """Validate source descriptions only, with the existing admission bounds."""
    raw = strict_loads(canonical(raw))
    if (
        type(raw) is not dict
        or set(raw)
        not in (
            {"schema_version", "edition", "pdfs", "documents"},
            {"schema_version", "edition", "pdfs", "documents", "manifest_sha256"},
        )
        or raw["schema_version"] != SOURCE_SCHEMA
    ):
        raise BatchContractError("BATCH_MANIFEST_INVALID")
    edition = raw["edition"]
    if type(edition) is not dict or set(edition) != {
        "tenant_id",
        "edition_id",
        "tournament_id",
        "edition_year",
        "roster_slug",
        "phase",
    }:
        raise BatchContractError("BATCH_EDITION_INVALID")
    for key in ("edition_id", "tournament_id"):
        canonical_uuid(edition[key])
    if expected_edition_id is not None and edition["edition_id"] != canonical_uuid(
        expected_edition_id
    ):
        raise BatchContractError("BATCH_EDITION_INVALID")
    if any(
        type(edition[k]) is not str or not _SLUG.fullmatch(edition[k])
        for k in ("tenant_id", "roster_slug", "phase")
    ):
        raise BatchContractError("BATCH_EDITION_INVALID")
    if (
        type(edition["edition_year"]) is not int
        or not 1 <= edition["edition_year"] <= 9999
    ):
        raise BatchContractError("BATCH_EDITION_INVALID")
    pdfs = raw["pdfs"]
    if type(pdfs) is not list or not 1 <= len(pdfs) <= MAX_PDFS:
        raise BatchContractError("BATCH_SOURCE_INVALID")
    by_hash, filenames = {}, set()
    for pdf in pdfs:
        if type(pdf) is not dict or set(pdf) != {"filename", "sha256", "page_count"}:
            raise BatchContractError("BATCH_SOURCE_INVALID")
        name = pdf["filename"]
        if (
            type(name) is not str
            or not name
            or len(name) > 255
            or PurePath(name).name != name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            raise BatchContractError("BATCH_SOURCE_INVALID")
        sha = sha256_hex(pdf["sha256"])
        if (
            sha in by_hash
            or name in filenames
            or type(pdf["page_count"]) is not int
            or not 1 <= pdf["page_count"] <= MAX_SOURCE_PAGES
        ):
            raise BatchContractError("BATCH_SOURCE_INVALID")
        by_hash[sha] = pdf["page_count"]
        filenames.add(name)
    docs = raw["documents"]
    if type(docs) is not list or not 1 <= len(docs) <= MAX_DOCUMENTS:
        raise BatchContractError("BATCH_DOCUMENT_INVALID")
    identifiers: set[str] = set()
    used: set[tuple[str, int]] = set()
    total = 0
    for document in docs:
        if type(document) is not dict or set(document) != {
            "document_id",
            "source_slices",
            "extraction",
        }:
            raise BatchContractError("BATCH_DOCUMENT_INVALID")
        refs = source_refs(document["source_slices"])
        identity = stable_document_id(edition["edition_id"], refs)
        if (
            document["document_id"] != identity
            or identity in identifiers
            or type(document["extraction"]) is not dict
        ):
            raise BatchContractError("BATCH_DOCUMENT_INVALID")
        if any(
            pdf not in by_hash or page > by_hash[pdf] or (pdf, page) in used
            for pdf, page in refs
        ):
            raise BatchContractError("BATCH_SOURCE_INVALID")
        identifiers.add(identity)
        used.update(refs)
        total += len(refs)
    if total > MAX_SOURCE_PAGES:
        raise BatchContractError("BATCH_SOURCE_INVALID")
    supplied = raw.pop("manifest_sha256", None)
    computed = digest(raw)
    if supplied is not None and not hmac.compare_digest(sha256_hex(supplied), computed):
        raise BatchContractError("BATCH_HASH_INVALID")
    raw["manifest_sha256"] = computed
    return raw
