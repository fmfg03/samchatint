"""Quality snapshots separate observed error, exceptions and capture authority."""

from copy import deepcopy

import pytest

from devnous.copa_telmex.staff_batch_contracts import BatchContractError
from devnous.tournaments.core.ctt_batch_qualification import (
    calculate_metrics,
    compare_observations,
    create_qualification_candidate,
    scaling_allowed,
    select_sample,
    validate_quality_sample,
)

A, B, CORPUS, PROFILE = (c * 64 for c in "abcd")
CUTOFF = "2026-11-09T00:00:00Z"


def document(index, pdf=A, category="femenil", template="letter-20", ready=True):
    return {
        "document_id": f"{index:064x}",
        "source_slices": [{"pdf_sha256": pdf, "pages": [2 * index - 1, 2 * index]}],
        "category": category,
        "template_id": template,
        "status": "ready" if ready else "exception",
        "auto_ready": ready,
    }


def observation(text, input_hash, **extra):
    return {
        "raw_text": text,
        "input_sha256": input_hash,
        "method": "visual" if input_hash == B else "native",
        "source_ref": {"pdf_sha256": A, "pdf_page": 1, "field": "name"},
        **extra,
    }


def metrics_fixture(total=100, ready=80):
    members = [document(i + 1, ready=i < ready) for i in range(total)]
    pages = [{"pdf_sha256": A, "pdf_page": i + 1} for i in range(total * 2)]
    metrics = calculate_metrics(
        members,
        source_pages=pages,
        cutoff=CUTOFF,
        profile_hash=PROFILE,
        corpus_hash=CORPUS,
        input_versions={"corpus": 1},
    )
    return members, pages, metrics


def test_sample_is_frozen_by_source_strata_not_predictions_order_or_ready_flags():
    docs = [
        document(1),
        document(2),
        document(3, category="juvenil"),
        document(4, pdf=B),
        document(5, template="letter-other"),
    ]
    selected = select_sample(docs, corpus_hash=CORPUS)
    assert set(selected) >= {docs[i]["document_id"] for i in (2, 3, 4)}
    assert len(selected) == 4
    changed = [
        {**d, "status": "failed", "prediction": "different"} for d in reversed(docs)
    ]
    assert select_sample(changed, corpus_hash=CORPUS) == selected


def test_unknown_category_and_duplicate_documents_do_not_qualify_sample():
    with pytest.raises(BatchContractError, match="STRATUM"):
        select_sample([document(1, category="")], corpus_hash=CORPUS)
    with pytest.raises(BatchContractError, match="DUPLICATE"):
        select_sample([document(1), document(1)], corpus_hash=CORPUS)


def test_field_agreement_retains_raw_and_does_not_use_confidence_as_truth():
    first = {"name": observation("  Jose\u0301  Luis  ", A, confidence=0.0)}
    second = {"name": observation("José Luis", B, confidence=1.0)}
    result = compare_observations(first, second, profile={})
    assert result["accepted"] and result["agreements"] == {"name": "José Luis"}
    assert result["fields"]["name"]["raw_first"]["raw_text"] == "  Jose\u0301  Luis  "
    first["name"]["raw_text"] = "JOSE Luis"
    assert not compare_observations(first, second, profile={})["accepted"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"input_sha256": A}, "NON_DISTINCT_INPUTS"),
        (
            {"source_ref": {"pdf_sha256": B, "pdf_page": 1, "field": "name"}},
            "SOURCE_MISMATCH",
        ),
        ({"raw_text": "Different"}, "FIELD_DISAGREEMENT"),
        ({"requires_review": True}, "READER_REQUIRES_REVIEW"),
    ],
)
def test_same_cached_input_other_source_or_disagreement_never_admits(change, code):
    result = compare_observations(
        {"name": observation("Same", A)},
        {"name": observation("Same", B, **change)},
        profile={},
    )
    assert not result["accepted"] and code in result["discrepancies"][0]["codes"]


def test_birth_dates_normalize_only_unambiguous_supported_forms():
    a, b = {"birth": observation("01/02/2008", A)}, {
        "birth": observation("2008-02-01", B)
    }
    assert compare_observations(a, b, profile={"normalizers": {"birth": "date"}})[
        "accepted"
    ]
    a["birth"]["raw_text"] = "01/02/08"
    assert not compare_observations(a, b, profile={"normalizers": {"birth": "date"}})[
        "accepted"
    ]
    a["birth"]["raw_text"] = "31/02/2008"
    assert not compare_observations(a, b, profile={"normalizers": {"birth": "date"}})[
        "accepted"
    ]


def test_missing_reader_and_untyped_field_values_fail_closed():
    assert not compare_observations({"name": observation("Same", A)}, {}, profile={})[
        "accepted"
    ]
    assert not compare_observations(
        {"name": observation(12, A)}, {"name": observation(12, B)}, profile={}
    )["accepted"]


def test_frozen_eighty_percent_snapshot_does_not_shrink_after_five_successes():
    members, _, measured = metrics_fixture()
    snapshot = deepcopy(measured)
    assert measured["teams"] == {"numerator": 80, "denominator": 100}
    assert measured["pages"] == {"numerator": 160, "denominator": 200}
    assert scaling_allowed(measured)
    for member in members[:5]:
        member["status"] = "committed"
    assert scaling_allowed(measured) and measured == snapshot


@pytest.mark.parametrize("ready", [0, 70, 79])
def test_sub_eighty_percent_never_scales(ready):
    assert not scaling_allowed(metrics_fixture(100, ready)[2])


def test_empty_population_and_changed_snapshot_never_permit_scaling():
    _, _, measured = metrics_fixture(0, 0)
    assert not scaling_allowed(measured)
    measured = metrics_fixture()[2]
    measured["teams"]["numerator"] = 100
    assert not scaling_allowed(measured)


def test_unknown_fragments_remain_in_page_denominator_and_false_labels_do_not_exclude():
    member = document(1)
    pages = [
        {
            "pdf_sha256": A,
            "pdf_page": i,
            "classification": "other" if i > 2 else "roster",
        }
        for i in range(1, 5)
    ]
    measured = calculate_metrics(
        [member],
        source_pages=pages,
        cutoff=CUTOFF,
        profile_hash=PROFILE,
        corpus_hash=CORPUS,
    )
    assert measured["pages"] == {"numerator": 2, "denominator": 4}
    assert not scaling_allowed(measured)


def test_exclusions_require_exact_evidence_prior_to_snapshot_cutoff():
    members, pages, _ = metrics_fixture(5, 4)
    members[-1]["exclusion_proof"] = {
        "kind": "already_captured",
        "evidence_sha256": "f" * 64,
        "effective_at": "2026-11-08T00:00:00Z",
    }
    measured = calculate_metrics(
        members,
        source_pages=pages,
        cutoff=CUTOFF,
        profile_hash=PROFILE,
        corpus_hash=CORPUS,
    )
    assert measured["teams"] == {"numerator": 4, "denominator": 4}
    assert measured["pages"] == {"numerator": 8, "denominator": 8}
    members[-1]["exclusion_proof"]["effective_at"] = "2026-11-10T00:00:00Z"
    measured = calculate_metrics(
        members,
        source_pages=pages,
        cutoff=CUTOFF,
        profile_hash=PROFILE,
        corpus_hash=CORPUS,
    )
    assert measured["teams"]["denominator"] == 5


def sample_result(doc, *, classification="auto_ready", outcome="matches", error=False):
    return {
        "document_id": doc["document_id"],
        "classification": classification,
        "outcome": outcome,
        "comparisons": [{"critical": True, "error": error}],
        "strata": [
            {
                "pdf_sha256": A,
                "template_id": doc["template_id"],
                "category": doc["category"],
            }
        ],
    }


def test_critical_false_admission_suspends_affected_stratum_not_just_wrong_field():
    doc = document(1)
    result = validate_quality_sample([sample_result(doc, error=True)])
    assert result["critical_false_admissions"] == 1
    assert result["failed_strata"] == [[A, "letter-20", "femenil"]]
    assert not result["passed"] and result["human_authority"] is False


def test_correctly_rejected_raw_error_is_exception_not_false_admission():
    result = validate_quality_sample(
        [
            sample_result(
                document(1),
                classification="exception",
                outcome="exception_confirmed",
                error=True,
            )
        ]
    )
    assert result["raw_errors"] == 1 and result["critical_false_admissions"] == 0
    assert result["passed"] and result["human_authority"] is False


def test_candidate_has_no_future_uv_and_caller_cannot_forge_sample_classification():
    members, _, measured = metrics_fixture(1, 1)
    proposed = sample_result(
        members[0], classification="exception", outcome="critical_error"
    )
    candidate = create_qualification_candidate(
        members,
        corpus_hash=CORPUS,
        profile_hash=PROFILE,
        cutoff=CUTOFF,
        metrics=measured,
        sample_results=[proposed],
        proposed_declaration={"text": "Contraste propuesto"},
    )
    assert candidate["sample_report"]["critical_false_admissions"] == 1
    assert candidate["sample_results"][0]["classification"] == "auto_ready"
    assert candidate["human_authority"] is False
    assert "consent_id" not in candidate and "uv_proof" not in candidate
    with pytest.raises(BatchContractError, match="FUTURE_AUTHORITY"):
        create_qualification_candidate(
            members,
            corpus_hash=CORPUS,
            profile_hash=PROFILE,
            cutoff=CUTOFF,
            metrics=measured,
            sample_results=[proposed],
            proposed_declaration={"nested": {"verified_by_human": True}},
        )


def test_omitting_sample_document_or_rebinding_metrics_is_not_qualification():
    members, _, measured = metrics_fixture(2, 2)
    with pytest.raises(BatchContractError, match="SAMPLE_COVERAGE"):
        create_qualification_candidate(
            members,
            corpus_hash=CORPUS,
            profile_hash=PROFILE,
            cutoff=CUTOFF,
            metrics=measured,
            sample_results=[],
        )
    results = [
        sample_result(next(m for m in members if m["document_id"] == identifier))
        for identifier in select_sample(members, corpus_hash=CORPUS)
    ]
    with pytest.raises(BatchContractError, match="METRICS_MISMATCH"):
        create_qualification_candidate(
            members,
            corpus_hash=CORPUS,
            profile_hash="f" * 64,
            cutoff=CUTOFF,
            metrics=measured,
            sample_results=results,
        )
