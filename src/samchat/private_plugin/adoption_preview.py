"""Offline adapter to the existing CTT field-adoption owner, never a capture.

Not registered as a tool. Composition must supply promote_canonical_fields from
ctt_canonical_promotion and already-authorized review data. The live package
imports initialize other tournament components, so this module deliberately
does not import them or provide a dynamic import/dispatcher escape hatch.
"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .contracts import Denied


def adoption_preview(
    canonical_promoter: Callable[..., Any],
    *,
    raw_payload: Mapping,
    extraction: Mapping,
    selections: Sequence[str],
    expected_hash: str,
    draft_version: str,
    actor_id: str,
    observed_at: str,
) -> dict:
    """Run canonical adoption on copies and return only a minimized summary.

    Arguments except field selectors/hash come from trusted server composition,
    not a tool payload. This does NOT authorize access, persist an adoption,
    validate roster eligibility, build the human business diff or commit players.
    Those operations remain disabled pending canonical scope and transaction proof.
    Missing values and non-existing roster slots are governed by the existing
    promoter; no birthday, phone, category or player defaults are added here.
    """
    if not actor_id or not draft_version or not observed_at:
        raise Denied("PREVIEW_CONTEXT_REQUIRED")
    result = canonical_promoter(
        deepcopy(raw_payload),
        deepcopy(extraction),
        tuple(selections),
        expected_hash=expected_hash,
        actor={"user_id": actor_id},
        promoted_at=observed_at,
    )
    # Never serialize extraction, before/after values, crop paths, or child PII.
    return {
        "draft_version": draft_version,
        "canonical_hash": result.canonical_hash,
        "changed_fields": len(result.field_events),
        "persisted": False,
        "commit_enabled": False,
        "human_diff_required": True,
    }
