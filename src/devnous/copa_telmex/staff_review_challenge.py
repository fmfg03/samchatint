"""Precheck primitives for a staff review challenge, not authority issuance.

Callers must obtain every scope field from protected server state. This module
neither authenticates a reviewer nor verifies a WebAuthn assertion, consumes a
challenge, prevents replay, or authorizes persistence. A protected, atomic
store and the gate must establish those properties before capture.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import NoReturn, Tuple
from uuid import UUID

_BARE_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
_MAX_LIFETIME = timedelta(minutes=5)


class ChallengePrecheckError(ValueError):
    """A challenge candidate failed a non-authoritative precheck."""


def _deny() -> NoReturn:
    # Do not include identifiers, scope, challenge bytes, or other inputs.
    raise ChallengePrecheckError("staff review challenge precheck failed")


def _hash(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _deny()
    try:
        if value.utcoffset() is None:
            _deny()
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _deny()


@dataclass(frozen=True, repr=False)
class SourcePage:
    """One server-resolved review asset; its hash is a source binding."""

    page_index: int
    asset_id: UUID
    image_sha256: str
    pdf_page: int

    def __post_init__(self) -> None:
        if (
            type(self.page_index) is not int
            or self.page_index < 1
            or not isinstance(self.asset_id, UUID)
            or not _hash(self.image_sha256, _SHA)
            or type(self.pdf_page) is not int
            or self.pdf_page < 1
        ):
            _deny()


@dataclass(frozen=True, repr=False)
class ReviewScope:
    """An immutable candidate assembled by a trusted caller from server state.

    Staff and changes are opaque, domain-scoped HMAC bindings supplied by the
    trusted governance builder; this class checks their format, not validity.
    """

    tenant_id: str
    tournament_slug: str
    edition_id: UUID
    session_id: UUID
    draft_id: UUID
    draft_version: int
    draft_content_hash: str
    team_id: UUID
    manifest_sha256: str
    document_id: str
    pdf_sha256: str
    source_pages: Tuple[SourcePage, ...]
    actor_id: UUID
    actor_role: str
    authority_binding: str
    staff_and_changes_binding: str
    decision_hash: str | None
    review_receipt_id: str | None
    policy_hash: str
    operation_id: str
    purpose: str = "capture_staff_v2"
    field_evidence_binding: str | None = None

    def __post_init__(self) -> None:
        identifiers = (
            self.edition_id,
            self.session_id,
            self.draft_id,
            self.team_id,
            self.actor_id,
        )
        if (
            not _hash(self.tenant_id, _SLUG)
            or not _hash(self.tournament_slug, _SLUG)
            or not _hash(self.actor_role, _SLUG)
            or any(not isinstance(item, UUID) for item in identifiers)
            or type(self.draft_version) is not int
            or self.draft_version < 1
            or not _hash(self.draft_content_hash, _SHA)
            or not _hash(self.manifest_sha256, _BARE_SHA)
            or not _hash(self.document_id, _BARE_SHA)
            or not _hash(self.pdf_sha256, _BARE_SHA)
            or not _hash(self.authority_binding, _HMAC)
            or not _hash(self.staff_and_changes_binding, _HMAC)
            or not (
                (self.decision_hash is None and self.review_receipt_id is None)
                or (
                    _hash(self.decision_hash, _SHA)
                    and _hash(self.review_receipt_id, _SHA)
                )
            )
            or not _hash(self.policy_hash, _SHA)
            or not _hash(self.operation_id, _SHA)
            or (
                self.decision_hash is None
                and not _hash(self.field_evidence_binding, _HMAC)
            )
            or (
                self.decision_hash is not None
                and self.field_evidence_binding is not None
            )
            or self.purpose != "capture_staff_v2"
            or type(self.source_pages) is not tuple
            or not self.source_pages
            or any(type(item) is not SourcePage for item in self.source_pages)
        ):
            _deny()
        indexes = [item.page_index for item in self.source_pages]
        asset_ids = [item.asset_id for item in self.source_pages]
        pdf_pages = [item.pdf_page for item in self.source_pages]
        if (
            indexes != sorted(set(indexes))
            or len(asset_ids) != len(set(asset_ids))
            or pdf_pages != sorted(set(pdf_pages))
        ):
            _deny()


def canonical_scope_bytes(scope: ReviewScope) -> bytes:
    """Return versioned bindings with no names or field values.

    UUIDs and hashes can still be correlatable: protect these bytes and do not
    log them. They are candidate claims, not proof of their provenance.
    """
    if type(scope) is not ReviewScope:
        _deny()
    value = {
        "schema": (
            "ctt.staff-review.challenge-scope.v2"
            if scope.decision_hash is None
            else "ctt.staff-review.challenge-scope.v1"
        ),
        "tenant_id": scope.tenant_id,
        "tournament_slug": scope.tournament_slug,
        "edition_id": str(scope.edition_id),
        "session_id": str(scope.session_id),
        "draft_id": str(scope.draft_id),
        "draft_version": scope.draft_version,
        "draft_content_hash": scope.draft_content_hash,
        "team_id": str(scope.team_id),
        "manifest_sha256": scope.manifest_sha256,
        "document_id": scope.document_id,
        "pdf_sha256": scope.pdf_sha256,
        "source_pages": [
            {
                "page_index": page.page_index,
                "asset_id": str(page.asset_id),
                "image_sha256": page.image_sha256,
                "pdf_page": page.pdf_page,
            }
            for page in scope.source_pages
        ],
        "actor_id": str(scope.actor_id),
        "actor_role": scope.actor_role,
        "authority_binding": scope.authority_binding,
        "staff_and_changes_binding": scope.staff_and_changes_binding,
        "policy_hash": scope.policy_hash,
        "operation_id": scope.operation_id,
        "purpose": scope.purpose,
    }
    if scope.decision_hash is not None:
        if scope.review_receipt_id is None:
            _deny()
        value["decision_hash"] = scope.decision_hash
        value["review_receipt_id"] = scope.review_receipt_id
    else:
        if scope.field_evidence_binding is None:
            _deny()
        value["field_evidence_binding"] = scope.field_evidence_binding
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def scope_hash(scope: ReviewScope) -> str:
    """Hash candidate scope; no authentication or authorization implied."""
    return "sha256:" + hashlib.sha256(canonical_scope_bytes(scope)).hexdigest()


@dataclass(frozen=True, repr=False)
class ChallengeCandidate:
    """Private candidate only; it is not an approval or a persisted record."""

    scope: ReviewScope = field(repr=False)
    challenge: bytes = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.scope) is not ReviewScope:
            _deny()
        if type(self.challenge) is not bytes or len(self.challenge) != 32:
            _deny()
        issued, expires = _utc(self.issued_at), _utc(self.expires_at)
        if expires <= issued or expires - issued > _MAX_LIFETIME:
            _deny()


@dataclass(frozen=True)
class ChallengePrecheck:
    """A successful precheck still has no authentication or write authority."""

    identity_verified: bool = field(default=False, init=False)
    replay_protection_verified: bool = field(default=False, init=False)
    materialization_authorized: bool = field(default=False, init=False)


def create_challenge_candidate(
    scope: ReviewScope, *, issued_at: datetime
) -> ChallengeCandidate:
    """Generate a candidate; callers must securely persist it atomically."""
    if type(scope) is not ReviewScope:
        _deny()
    start = _utc(issued_at)
    try:
        expires = start + _MAX_LIFETIME
    except OverflowError:
        _deny()
    return ChallengeCandidate(
        scope=scope,
        challenge=secrets.token_bytes(32),
        issued_at=start,
        expires_at=expires,
    )


def precheck_challenge(
    candidate: ChallengeCandidate,
    *,
    presented_challenge: bytes,
    current_scope: ReviewScope,
    now: datetime,
) -> ChallengePrecheck:
    """Check candidate scope, bytes and time; never consume or authorize it.

    Both candidate and current_scope must be loaded independently from
    protected, server-resolved state. The client must not supply either object.
    An atomic, durable single-use check, current actor/faculty revalidation,
    and WebAuthn signature verification remain mandatory at the gate.
    """
    if (
        type(candidate) is not ChallengeCandidate
        or type(current_scope) is not ReviewScope
        or type(presented_challenge) is not bytes
        or len(presented_challenge) != 32
    ):
        _deny()
    checked_now = _utc(now)
    if (
        checked_now < _utc(candidate.issued_at)
        or checked_now >= _utc(candidate.expires_at)
        or current_scope != candidate.scope
        or not hmac.compare_digest(candidate.challenge, presented_challenge)
    ):
        _deny()
    return ChallengePrecheck()


def parse_canonical_scope(raw: bytes) -> ReviewScope:
    """Parse exact canonical bytes from protected state; not an approval."""
    if not isinstance(raw, bytes) or not 2 <= len(raw) <= 262144:
        _deny()

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                _deny()
            result[key] = value
        return result

    def reject_constant(_value: str) -> NoReturn:
        _deny()

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
        if type(value) is not dict:
            _deny()
        schema = value.pop("schema", None)
        if schema == "ctt.staff-review.challenge-scope.v2":
            if "decision_hash" in value or "review_receipt_id" in value:
                _deny()
            value["decision_hash"] = None
            value["review_receipt_id"] = None
            if "field_evidence_binding" not in value:
                _deny()
        elif schema != "ctt.staff-review.challenge-scope.v1":
            _deny()
        elif "field_evidence_binding" in value:
            _deny()
        pages = value.pop("source_pages")
        if type(pages) is not list:
            _deny()
        parsed_pages = tuple(
            SourcePage(
                page_index=page["page_index"],
                asset_id=UUID(page["asset_id"]),
                image_sha256=page["image_sha256"],
                pdf_page=page["pdf_page"],
            )
            for page in pages
        )
        for name in (
            "edition_id",
            "session_id",
            "draft_id",
            "team_id",
            "actor_id",
        ):
            value[name] = UUID(value[name])
        scope = ReviewScope(source_pages=parsed_pages, **value)
        if not hmac.compare_digest(canonical_scope_bytes(scope), raw):
            _deny()
        return scope
    except Exception:
        _deny()
