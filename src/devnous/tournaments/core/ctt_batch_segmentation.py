"""Conservative grouping of retained page observations, without model authority.

Observations must come from a separately admitted private source-analysis path.
This state machine does not infer fields from filenames, image confidence or a
team-name similarity score. Unknown tails quarantine the whole affected group.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    canonical_uuid,
    sha256_hex,
    stable_document_id,
)


def _ref(page: Mapping) -> tuple[str, int]:
    pdf = sha256_hex(page.get("pdf_sha256"))
    number = page.get("pdf_page")
    if type(number) is not int or number < 1:
        raise BatchContractError("BATCH_SOURCE_INVALID")
    return pdf, number


def _refs(pages: Sequence[Mapping]) -> list[dict]:
    return [{"pdf_sha256": pdf, "pdf_page": number} for pdf, number in map(_ref, pages)]


def _slices(pages: Sequence[Mapping]) -> list[dict]:
    result: list[dict[str, Any]] = []
    for pdf, number in map(_ref, pages):
        if result and result[-1]["pdf_sha256"] == pdf:
            result[-1]["pages"].append(number)
        else:
            result.append({"pdf_sha256": pdf, "pages": [number]})
    return result


def segment_pages(
    pages: Sequence[Mapping], *, edition_id: str, profile: Mapping
) -> dict:
    """Return complete source-bound documents or explicit page exceptions.

    Each page has a physical ref, side, template/category and printed slots.
    Cross-PDF continuation additionally requires an operator-bound, ordered
    ``source_group``. Pattern values are slot lists, or ordered lists of slot
    lists for repeated continuation sides. Every input page is accounted for.
    """
    canonical_uuid(edition_id)
    if not isinstance(profile, Mapping) or not isinstance(pages, (list, tuple)):
        raise BatchContractError("BATCH_SEGMENTATION_INPUT_INVALID")
    if len(pages) > 1200 or not all(isinstance(p, Mapping) for p in pages):
        raise BatchContractError("BATCH_SEGMENTATION_INPUT_INVALID")
    allowed_templates = profile.get("template_ids")
    patterns = profile.get("allowed_page_patterns")
    groups = profile.get("source_groups", {})
    if (
        not isinstance(allowed_templates, (list, tuple))
        or not allowed_templates
        or any(type(t) is not str or not t for t in allowed_templates)
        or not isinstance(patterns, Mapping)
        or not {"front", "back"}.issubset(patterns)
        or not isinstance(groups, Mapping)
    ):
        raise BatchContractError("BATCH_SEGMENTATION_PROFILE_INVALID")
    for group, pdfs in groups.items():
        if type(group) is not str or not group or not isinstance(pdfs, (list, tuple)):
            raise BatchContractError("BATCH_SEGMENTATION_PROFILE_INVALID")
        if not pdfs or len(set(pdfs)) != len(pdfs):
            raise BatchContractError("BATCH_SEGMENTATION_PROFILE_INVALID")
        for pdf in pdfs:
            sha256_hex(pdf)
    physical_refs = [_ref(page) for page in pages]
    if len(set(physical_refs)) != len(physical_refs):
        raise BatchContractError("BATCH_DUPLICATE_PAGE_OBSERVATION")
    highest: Counter[str] = Counter()
    for pdf, number in physical_refs:
        highest[pdf] = max(highest[pdf], number)
    documents, exceptions, classifications = [], [], {}
    pending: list[dict] = []
    invalid_reason: str | None = None

    def mark_exception(group, code):
        if not group:
            return
        refs = _refs(group)
        exceptions.append({"page_refs": refs, "code": code})
        for ref in refs:
            classifications[(ref["pdf_sha256"], ref["pdf_page"])] = {
                **ref,
                "classification": "exception",
                "code": code,
            }

    def finish():
        nonlocal pending, invalid_reason
        if not pending:
            return
        if invalid_reason or len(pending) < 2 or pending[1].get("side") != "back":
            mark_exception(pending, invalid_reason or "INCOMPLETE_DOCUMENT")
        else:
            refs = tuple(map(_ref, pending))
            document_id = stable_document_id(edition_id, refs)
            document = {
                "document_id": document_id,
                "source_slices": _slices(pending),
                "template_id": pending[0]["template_id"],
                "category": pending[0]["category"],
                "page_refs": _refs(pending),
            }
            documents.append(document)
            for ref in document["page_refs"]:
                classifications[(ref["pdf_sha256"], ref["pdf_page"])] = {
                    **ref,
                    "classification": "document",
                    "document_id": document_id,
                }
        pending, invalid_reason = [], None

    def expected_slots(side, ordinal):
        values = patterns.get(side)
        if not isinstance(values, (list, tuple)) or not values:
            return None
        if isinstance(values[0], (list, tuple)):
            return values[ordinal] if ordinal < len(values) else None
        return values if ordinal == 0 else None

    def validate(page):
        if type(page.get("side")) is not str or page.get("side") not in {
            "front",
            "back",
            "continuation",
            "other",
            "unknown",
        }:
            return "UNKNOWN_PAGE_SIDE"
        if page.get("side") in {"other", "unknown"}:
            return None
        if page.get("template_id") not in allowed_templates:
            return "UNSUPPORTED_TEMPLATE"
        if type(page.get("category")) is not str or not page["category"].strip():
            return "UNRESOLVED_CATEGORY"
        group = page.get("source_group")
        if group is not None and (
            type(group) is not str
            or group not in groups
            or page["pdf_sha256"] not in groups[group]
        ):
            return "UNKNOWN_SOURCE_GROUP"
        slots = page.get("slots")
        if (
            not isinstance(slots, (list, tuple))
            or any(type(s) is not int or not 1 <= s <= 35 for s in slots)
            or len(set(slots)) != len(slots)
        ):
            return "INVALID_PHYSICAL_SLOTS"
        return None

    def adjacent(previous, current):
        old_pdf, old_page = _ref(previous)
        pdf, number = _ref(current)
        if pdf == old_pdf:
            return number == old_page + 1
        group = previous.get("source_group")
        if not group or group != current.get("source_group") or group not in groups:
            return False
        ordered = groups[group]
        return (
            old_pdf in ordered
            and pdf in ordered
            and ordered.index(pdf) == ordered.index(old_pdf) + 1
            and old_page == highest[old_pdf]
            and number == 1
        )

    for raw in pages:
        page = deepcopy(dict(raw))
        side = page.get("side")
        issue = validate(page)
        if side == "front":
            finish()
            pending = [page]
            expected = expected_slots("front", 0)
            invalid_reason = issue or (
                None if page.get("slots") == expected else "UNEXPECTED_PHYSICAL_SLOTS"
            )
            continue
        if side == "other":
            # A separator never silently joins front/back pages across itself.
            finish()
            ref = _refs([page])[0]
            if page.get("slots"):
                mark_exception([page], "NON_ROSTER_HAS_PLAYER_SLOTS")
            else:
                classifications[_ref(page)] = {**ref, "classification": "other"}
            continue
        if side == "unknown" or issue:
            if pending:
                pending.append(page)
                invalid_reason = invalid_reason or issue or "UNKNOWN_DOCUMENT_TAIL"
            else:
                mark_exception([page], issue or "UNKNOWN_PAGE")
            continue
        if not pending:
            mark_exception([page], "ORPHAN_CONTINUATION")
            continue
        ordinal = sum(p.get("side") == side for p in pending)
        expected = expected_slots(side, ordinal)
        mismatch = (
            len(pending) >= 4
            or not adjacent(pending[-1], page)
            or page.get("template_id") != pending[0].get("template_id")
            or page.get("category") != pending[0].get("category")
            or (
                page.get("team_key") is not None
                and pending[0].get("team_key") is not None
                and page["team_key"] != pending[0]["team_key"]
            )
            or expected is None
            or page.get("slots") != expected
            or (side == "back" and len(pending) != 1)
            or (
                side == "continuation"
                and (len(pending) < 2 or pending[1].get("side") != "back")
            )
        )
        pending.append(page)
        if mismatch:
            invalid_reason = invalid_reason or "AMBIGUOUS_DOCUMENT_CONTINUATION"
    finish()
    return {
        "documents": documents,
        "exceptions": exceptions,
        "page_classifications": [classifications[ref] for ref in physical_refs],
    }
