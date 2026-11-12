"""Whole-source grouping rejects ambiguous and unsupported physical continuations."""

from copy import deepcopy
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_batch_contracts import BatchContractError
from devnous.tournaments.core.ctt_batch_segmentation import segment_pages

EDITION = str(uuid4())
A, B = "a" * 64, "b" * 64
PROFILE = {
    "template_ids": ["letter-20"],
    "source_groups": {"state-parts": [A, B]},
    "allowed_page_patterns": {"front": list(range(1, 9)), "back": list(range(9, 21))},
}


def page(number, side, pdf=A, **extra):
    return {
        "pdf_sha256": pdf,
        "pdf_page": number,
        "side": side,
        "template_id": "letter-20",
        "category": "femenil",
        "source_group": "state-parts",
        "slots": PROFILE["allowed_page_patterns"].get(side, []),
        **extra,
    }


def test_split_pdf_continuation_preserves_both_original_hashes_and_pages():
    result = segment_pages(
        [page(1, "front"), page(1, "back", B)], edition_id=EDITION, profile=PROFILE
    )
    assert not result["exceptions"]
    assert result["documents"][0]["source_slices"] == [
        {"pdf_sha256": A, "pages": [1]},
        {"pdf_sha256": B, "pages": [1]},
    ]
    assert len(result["page_classifications"]) == 2
    assert {p["classification"] for p in result["page_classifications"]} == {"document"}


def test_missing_or_foreign_source_group_does_not_join_separate_pdfs():
    for extra in ({"source_group": None}, {"source_group": "unadmitted"}):
        result = segment_pages(
            [page(1, "front"), page(1, "back", B, **extra)],
            edition_id=EDITION,
            profile=PROFILE,
        )
        assert result["documents"] == []
        assert len(result["page_classifications"]) == 2
        assert all(
            p["classification"] == "exception" for p in result["page_classifications"]
        )


@pytest.mark.parametrize(
    "change",
    [
        {"category": "juvenil"},
        {"template_id": "unknown"},
        {"slots": list(range(10, 21))},
        {"team_key": "other-team"},
    ],
)
def test_header_category_layout_or_team_conflict_quarantines_whole_document(change):
    result = segment_pages(
        [page(1, "front", team_key="team-a"), page(2, "back", **change)],
        edition_id=EDITION,
        profile=PROFILE,
    )
    assert not result["documents"] and len(result["exceptions"][0]["page_refs"]) == 2


def test_unknown_tail_never_truncates_document_to_supported_twenty_slots():
    result = segment_pages(
        [
            page(1, "front"),
            page(2, "back"),
            page(3, "unknown"),
            page(4, "front"),
            page(5, "back"),
        ],
        edition_id=EDITION,
        profile=PROFILE,
    )
    assert len(result["documents"]) == 1
    assert result["documents"][0]["source_slices"][0]["pages"] == [4, 5]
    assert len(result["exceptions"][0]["page_refs"]) == 3
    assert len(result["page_classifications"]) == 5


def test_unsupported_continuation_does_not_discard_extra_players():
    result = segment_pages(
        [
            page(1, "front"),
            page(2, "back"),
            page(3, "continuation", slots=list(range(21, 26))),
        ],
        edition_id=EDITION,
        profile=PROFILE,
    )
    assert result["documents"] == []
    assert len(result["exceptions"][0]["page_refs"]) == 3


def test_explicit_qualified_continuation_preserves_all_three_pages():
    profile = deepcopy(PROFILE)
    profile["allowed_page_patterns"]["continuation"] = list(range(21, 26))
    result = segment_pages(
        [
            page(1, "front"),
            page(2, "back"),
            page(3, "continuation", slots=list(range(21, 26))),
        ],
        edition_id=EDITION,
        profile=profile,
    )
    assert result["documents"][0]["source_slices"][0]["pages"] == [1, 2, 3]
    assert not result["exceptions"]


def test_orphan_reverse_order_and_gaps_are_not_blindly_paired():
    for pages in (
        [page(1, "back"), page(2, "front")],
        [page(1, "front"), page(3, "back")],
    ):
        result = segment_pages(pages, edition_id=EDITION, profile=PROFILE)
        assert not result["documents"]
        assert len(result["page_classifications"]) == len(pages)


def test_separator_breaks_incomplete_document_and_cannot_hide_player_slots():
    result = segment_pages(
        [page(1, "front"), page(2, "other"), page(3, "back")],
        edition_id=EDITION,
        profile=PROFILE,
    )
    assert not result["documents"]
    assert [p["classification"] for p in result["page_classifications"]] == [
        "exception",
        "other",
        "exception",
    ]
    result = segment_pages(
        [page(1, "other", slots=[1, 2])], edition_id=EDITION, profile=PROFILE
    )
    assert result["page_classifications"][0]["classification"] == "exception"


def test_document_identity_is_independent_of_extractor_or_filename_metadata():
    pages = [page(1, "front"), page(2, "back")]
    first = segment_pages(pages, edition_id=EDITION, profile=PROFILE)
    changed = [
        {
            **p,
            "model_prediction": "different",
            "filename": "renamed.pdf",
            "confidence": 1.0,
        }
        for p in pages
    ]
    second = segment_pages(changed, edition_id=EDITION, profile=PROFILE)
    assert first["documents"][0]["document_id"] == second["documents"][0]["document_id"]


def test_duplicate_page_observation_fails_instead_of_doubling_roster():
    with pytest.raises(BatchContractError, match="DUPLICATE_PAGE"):
        segment_pages(
            [page(1, "front"), page(1, "front")], edition_id=EDITION, profile=PROFILE
        )
