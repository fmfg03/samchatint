"""Validate immutable Copa Telmex batch-admission manifests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePath
from typing import Any, Mapping
from uuid import UUID

SCHEMA_VERSION = "ctt.registration.batch-admission.v1"
STAFF_ROLES = ("director_tecnico", "auxiliar")
MAX_BATCH_DOCUMENTS = 300
MAX_BATCH_PDFS = 16
MAX_PAGES_PER_DOCUMENT = 4
MAX_BATCH_PAGES = 1200
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BatchAdmissionError(ValueError):
    """A manifest is unsafe, ambiguous, or internally inconsistent."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    """Serialize evidence deterministically without normalizing its values."""
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: Any, code: str) -> str:
    normalized = str(value or "")
    if not _SHA256.fullmatch(normalized):
        raise BatchAdmissionError(code)
    return normalized


def _filename(value: Any) -> str:
    normalized = str(value or "")
    if (
        not normalized
        or "/" in normalized
        or "\\" in normalized
        or PurePath(normalized).name != normalized
        or normalized in {".", ".."}
    ):
        raise BatchAdmissionError("INVALID_SOURCE_FILENAME")
    return normalized


def source_filename(value: Any) -> str:
    """Return one safe manifest basename for local preparation or admission."""
    return _filename(value)


def _pages(value: Any) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_PAGES_PER_DOCUMENT
        or any(type(item) is not int or item < 1 for item in value)
        or len(set(value)) != len(value)
        or value != sorted(value)
    ):
        raise BatchAdmissionError("INVALID_SOURCE_PAGES")
    return list(value)


def _staff(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 2:
        raise BatchAdmissionError("TECHNICAL_STAFF_INCOMPLETE")
    result: list[dict[str, Any]] = []
    for slot, role in enumerate(STAFF_ROLES, 1):
        item = value[slot - 1]
        if not isinstance(item, Mapping):
            raise BatchAdmissionError("TECHNICAL_STAFF_INVALID")
        if int(item.get("slot") or 0) != slot or item.get("role") != role:
            raise BatchAdmissionError("TECHNICAL_STAFF_SCOPE_MISMATCH")
        stable_id = str(item.get("staff_entry_id") or "")
        if not _SHA256.fullmatch(stable_id):
            raise BatchAdmissionError("TECHNICAL_STAFF_ID_INVALID")
        slot_ref = str(item.get("slot_ref") or "")
        if not slot_ref:
            raise BatchAdmissionError("TECHNICAL_STAFF_SOURCE_MISSING")
        evidence = item.get("evidence")
        if not isinstance(evidence, Mapping):
            raise BatchAdmissionError("TECHNICAL_STAFF_EVIDENCE_MISSING")
        expected_id = sha256_value(
            {
                "role": role,
                "slot": slot,
                "slot_ref": slot_ref,
                "evidence": evidence,
            }
        )
        if stable_id != expected_id:
            raise BatchAdmissionError("TECHNICAL_STAFF_ID_MISMATCH")
        result.append(dict(item))
    return result


def validate_manifest(
    raw: Any,
    *,
    expected_tournament_id: str | None = None,
    expected_edition_year: int | None = None,
) -> dict[str, Any]:
    """Return a detached, validated manifest with its immutable hash."""
    if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA_VERSION:
        raise BatchAdmissionError("UNSUPPORTED_MANIFEST")
    edition = raw.get("edition")
    documents = raw.get("documents")
    if (
        not isinstance(edition, Mapping)
        or not isinstance(documents, list)
        or not documents
        or len(documents) > MAX_BATCH_DOCUMENTS
    ):
        raise BatchAdmissionError("MANIFEST_SCOPE_MISSING")
    tournament_id = str(edition.get("tournament_id") or "")
    edition_year = edition.get("edition_year")
    roster_slug = str(edition.get("roster_slug") or "")
    if not tournament_id or type(edition_year) is not int or not roster_slug:
        raise BatchAdmissionError("MANIFEST_SCOPE_MISSING")
    try:
        tournament_id = str(UUID(tournament_id))
    except ValueError as exc:
        raise BatchAdmissionError("TOURNAMENT_ID_INVALID") from exc
    if (
        expected_tournament_id is not None and tournament_id != expected_tournament_id
    ) or (expected_edition_year is not None and edition_year != expected_edition_year):
        raise BatchAdmissionError("TOURNAMENT_EDITION_SCOPE_MISMATCH")

    normalized_documents = []
    seen_documents: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    for document in documents:
        if not isinstance(document, Mapping):
            raise BatchAdmissionError("DOCUMENT_INVALID")
        document_id = _sha(document.get("document_id"), "DOCUMENT_ID_INVALID")
        filename = _filename(document.get("pdf"))
        pdf_sha256 = _sha(document.get("pdf_sha256"), "PDF_HASH_INVALID")
        pages = _pages(document.get("pages"))
        extraction = document.get("extraction")
        if not isinstance(extraction, Mapping):
            raise BatchAdmissionError("EXTRACTION_MISSING")
        staff = _staff(extraction.get("staff"))
        detached = dict(document)
        detached["pdf"] = filename
        detached["pages"] = pages
        detached["extraction"] = {**dict(extraction), "staff": staff}
        payload_sha256 = sha256_value(detached["extraction"])
        declared_payload = document.get("payload_sha256")
        if declared_payload is not None and declared_payload != payload_sha256:
            raise BatchAdmissionError("EXTRACTION_HASH_MISMATCH")
        source_key = (pdf_sha256, sha256_value(pages))
        if document_id in seen_documents or source_key in seen_sources:
            raise BatchAdmissionError("DUPLICATE_DOCUMENT_IDENTITY")
        seen_documents.add(document_id)
        seen_sources.add(source_key)
        detached["payload_sha256"] = payload_sha256
        detached["source_pages_sha256"] = source_key[1]
        normalized_documents.append(detached)

    if len({item["pdf"] for item in normalized_documents}) > MAX_BATCH_PDFS:
        raise BatchAdmissionError("BATCH_PDF_LIMIT_EXCEEDED")
    if sum(len(item["pages"]) for item in normalized_documents) > MAX_BATCH_PAGES:
        raise BatchAdmissionError("BATCH_PAGE_LIMIT_EXCEEDED")
    normalized_documents.sort(key=lambda item: item["document_id"])

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "edition": {
            "tournament_id": tournament_id,
            "edition_year": edition_year,
            "roster_slug": roster_slug,
        },
        "documents": normalized_documents,
    }
    normalized["manifest_sha256"] = sha256_value(normalized)
    return normalized


def admission_receipt(
    *,
    manifest_sha256: str,
    document: Mapping[str, Any],
    actor_id: str,
    review_session_id: str,
) -> dict[str, Any]:
    """Bind a session to the exact immutable document and authenticated actor."""
    event = {
        "schema_version": "ctt.registration.batch-admission-receipt.v1",
        "manifest_sha256": manifest_sha256,
        "document_id": document["document_id"],
        "pdf_sha256": document["pdf_sha256"],
        "source_pages_sha256": document["source_pages_sha256"],
        "payload_sha256": document["payload_sha256"],
        "actor_id": actor_id,
        "review_session_id": review_session_id,
    }
    return {**event, "event_sha256": sha256_value(event)}
