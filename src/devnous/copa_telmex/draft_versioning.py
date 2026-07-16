"""Append-only, Zaubern-authorized registration review draft versioning."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
from typing import Any, Mapping, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import RegistrationReviewDraft, RegistrationReviewSession
from .registration_governance import (
    RegistrationGovernanceClient,
    RegistrationGovernanceDenied,
)


class DraftVersionConflict(RegistrationGovernanceDenied):
    """The caller attempted to append from a stale draft version."""

    def __init__(self, detail: str):
        super().__init__("STALE_DRAFT_VERSION", detail)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def actor_binding(actor_id: Optional[Any]) -> Optional[str]:
    if actor_id in (None, ""):
        return None
    return _sha256({"actor_id": str(actor_id)})


def draft_content_hash(values: Mapping[str, Any]) -> str:
    return _sha256(
        {
            "ocr_raw": values.get("ocr_raw"),
            "extraction": values.get("extraction"),
            "validation": values.get("validation"),
            "review_edits": values.get("review_edits"),
            "layout_regions": values.get("layout_regions"),
            "overall_confidence": float(values.get("overall_confidence") or 0.0),
            "needs_review": bool(values.get("needs_review")),
        }
    )


def _snapshot(draft: Optional[RegistrationReviewDraft]) -> dict[str, Any]:
    if draft is None:
        return {
            "ocr_raw": None,
            "extraction": None,
            "validation": None,
            "review_edits": None,
            "layout_regions": None,
            "overall_confidence": 0.0,
            "needs_review": True,
        }
    return {
        "ocr_raw": copy.deepcopy(draft.ocr_raw),
        "extraction": copy.deepcopy(draft.extraction),
        "validation": copy.deepcopy(draft.validation),
        "review_edits": copy.deepcopy(draft.review_edits),
        "layout_regions": copy.deepcopy(draft.layout_regions),
        "overall_confidence": float(draft.overall_confidence or 0.0),
        "needs_review": bool(draft.needs_review),
    }


async def latest_draft(
    db_session: AsyncSession, session_id: UUID
) -> Optional[RegistrationReviewDraft]:
    result = await db_session.execute(
        select(RegistrationReviewDraft)
        .where(RegistrationReviewDraft.session_id == session_id)
        .order_by(
            RegistrationReviewDraft.draft_version.desc(),
            RegistrationReviewDraft.created_at.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def append_draft_version(
    db_session: AsyncSession,
    review_session: RegistrationReviewSession,
    *,
    mutation_type: str,
    actor_id: Optional[Any] = None,
    expected_draft: Optional[RegistrationReviewDraft] = None,
    operation_id: Optional[str] = None,
    governance_client: Optional[RegistrationGovernanceClient] = None,
    **changes: Any,
) -> RegistrationReviewDraft:
    """Authorize and insert one immutable successor while holding the session CAS lock."""
    allowed = {
        "ocr_raw",
        "extraction",
        "validation",
        "review_edits",
        "layout_regions",
        "overall_confidence",
        "needs_review",
    }
    unexpected = set(changes) - allowed
    if unexpected:
        raise ValueError(f"unsupported draft fields: {sorted(unexpected)}")
    if not mutation_type.strip():
        raise ValueError("mutation_type is required")

    await db_session.execute(
        select(RegistrationReviewSession.id)
        .where(RegistrationReviewSession.id == review_session.id)
        .with_for_update()
    )
    current = await latest_draft(db_session, review_session.id)
    if expected_draft is not None and (
        current is None
        or current.id != expected_draft.id
        or current.draft_version != expected_draft.draft_version
        or current.content_hash != expected_draft.content_hash
    ):
        raise DraftVersionConflict(
            "The draft changed after it was read; reload before writing."
        )

    values = _snapshot(current)
    for key, value in changes.items():
        values[key] = copy.deepcopy(value)
    content_hash = draft_content_hash(values)
    new_id = uuid4()
    new_version = int(current.draft_version if current else 0) + 1
    operation_id = operation_id or secrets.token_hex(20)
    payload = {
        "tenant_id": os.getenv("ZAUBERN_TENANT_ID", "samchat-prod"),
        "session_id": str(review_session.id),
        "previous_draft_id": str(current.id) if current else None,
        "previous_draft_version": int(current.draft_version) if current else 0,
        "previous_content_hash": current.content_hash if current else None,
        "new_draft_id": str(new_id),
        "new_draft_version": new_version,
        "new_content_hash": content_hash,
        "mutation_type": mutation_type,
        "actor_binding": actor_binding(actor_id),
        "operation_id": operation_id,
    }
    client = governance_client or RegistrationGovernanceClient.from_environment()
    authorization = await client.authorize_draft_version(payload)
    decision = authorization.get("draft_decision") or {}
    receipt = authorization.get("draft_receipt") or {}
    if (
        authorization.get("authorized") is not True
        or decision.get("decision") != "AUTHORIZE_DRAFT_VERSION"
        or decision.get("new_draft_id") != str(new_id)
        or decision.get("new_content_hash") != content_hash
        or receipt.get("verified") is not True
        or not decision.get("decision_id")
        or not receipt.get("receipt_id")
    ):
        raise RegistrationGovernanceDenied(
            "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
            "Zaubern returned an incomplete draft authorization",
        )

    successor = RegistrationReviewDraft(
        id=new_id,
        session_id=review_session.id,
        draft_version=new_version,
        predecessor_draft_id=current.id if current else None,
        predecessor_content_hash=current.content_hash if current else None,
        content_hash=content_hash,
        mutation_type=mutation_type,
        mutation_actor_binding=payload["actor_binding"],
        mutation_operation_id=operation_id,
        mutation_decision_id=str(decision["decision_id"]),
        mutation_receipt_id=str(receipt["receipt_id"]),
        **values,
    )
    db_session.add(successor)
    await db_session.flush()
    return successor
