"""Replayable batch quality evidence; neither model output nor metrics are authority.

These pure functions operate on retained private observations. A candidate is a
proposal: only the separately verified UV consent/activation can accredit the
human declaration. Provider confidence is deliberately absent from admission.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    canonical,
    digest,
    sha256_hex,
    source_refs,
)

METRICS_SCHEMA = "ctt.batch.qualification-metrics.v1"
CANDIDATE_SCHEMA = "ctt.batch.qualification-candidate.v1"


def _timestamp(value: Any) -> datetime:
    try:
        if type(value) is not str:
            raise ValueError
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise BatchContractError("BATCH_TIMESTAMP_INVALID") from None


def _page_ref(value: Mapping) -> tuple[str, int]:
    pdf = sha256_hex(value.get("pdf_sha256"))
    number = value.get("pdf_page")
    if type(number) is not int or number < 1:
        raise BatchContractError("BATCH_SOURCE_INVALID")
    return pdf, number


def _strata(document: Mapping) -> tuple[tuple[str, str, str], ...]:
    template, category = document.get("template_id"), document.get("category")
    if (
        type(template) is not str
        or not template
        or type(category) is not str
        or not category.strip()
    ):
        raise BatchContractError("BATCH_STRATUM_UNRESOLVED")
    return tuple(
        sorted(
            {
                (pdf, template, category)
                for pdf, _ in source_refs(document.get("source_slices"))
            }
        )
    )


def select_sample(documents: Sequence[Mapping], *, corpus_hash: str) -> tuple[str, ...]:
    """One deterministic complete document per PDF/template/explicit-category.

    Sampling depends only on frozen source identity and strata. Predictions,
    confidence, candidate acceptance and input iteration order cannot change it.
    """
    sha256_hex(corpus_hash)
    strata = defaultdict(list)
    seen = set()
    for document in documents:
        document_id = sha256_hex(document.get("document_id"))
        if document_id in seen:
            raise BatchContractError("BATCH_DUPLICATE_DOCUMENT")
        seen.add(document_id)
        for stratum in _strata(document):
            rank = digest(
                {
                    "domain": "ctt.batch.sample.v1",
                    "corpus_hash": corpus_hash,
                    "stratum": list(stratum),
                    "document_id": document_id,
                }
            )
            strata[stratum].append((rank, document_id))
    selected = {min(candidates)[1] for candidates in strata.values()}
    return tuple(
        sorted(
            selected,
            key=lambda doc: digest({"corpus_hash": corpus_hash, "document_id": doc}),
        )
    )


def _normalize(raw: Any, kind: str) -> Any:
    if raw is None:
        return None
    if type(raw) is not str:
        raise ValueError
    value = " ".join(unicodedata.normalize("NFC", raw).split())
    if not value:
        return None
    if kind == "text":
        return value
    if kind != "date":
        raise ValueError
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    raise ValueError


def compare_observations(first: Mapping, second: Mapping, *, profile: Mapping) -> dict:
    """Contrast distinct retained inputs for the exact same field/source ref.

    Field observations contain raw_text, method, input_sha256 and source_ref.
    Source refs identify the target field, while the input hash identifies the
    crop/context/native-text bytes used by that particular reader.
    """
    if not all(isinstance(item, Mapping) for item in (first, second, profile)):
        raise BatchContractError("BATCH_OBSERVATIONS_INVALID")
    normalizers = profile.get("normalizers", {})
    if not isinstance(normalizers, Mapping):
        raise BatchContractError("BATCH_OBSERVATIONS_INVALID")
    fields, agreements, discrepancies = {}, {}, []
    names = set(first) | set(second)
    if any(type(name) is not str for name in names):
        raise BatchContractError("BATCH_OBSERVATIONS_INVALID")
    for name in sorted(names):
        left, right = first.get(name), second.get(name)
        codes = []
        a = b = None
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            codes.append("MISSING_OBSERVATION")
        else:
            try:
                for observation in (left, right):
                    sha256_hex(observation.get("input_sha256"))
                    if (
                        type(observation.get("method")) is not str
                        or not observation["method"]
                    ):
                        raise ValueError
                    _page_ref(observation.get("source_ref", {}))
                if left["input_sha256"] == right["input_sha256"]:
                    codes.append("NON_DISTINCT_INPUTS")
                if canonical(left["source_ref"]) != canonical(right["source_ref"]):
                    codes.append("SOURCE_MISMATCH")
                a = _normalize(left.get("raw_text"), normalizers.get(name, "text"))
                b = _normalize(right.get("raw_text"), normalizers.get(name, "text"))
                if a != b:
                    codes.append("FIELD_DISAGREEMENT")
                if (
                    left.get("requires_review") is True
                    or right.get("requires_review") is True
                ):
                    codes.append("READER_REQUIRES_REVIEW")
            except (ValueError, TypeError, KeyError, BatchContractError):
                codes.append("INVALID_OBSERVATION")
        record = {
            "raw_first": deepcopy(dict(left)) if isinstance(left, Mapping) else None,
            "raw_second": deepcopy(dict(right)) if isinstance(right, Mapping) else None,
            "normalized_first": a,
            "normalized_second": b,
            "agrees": not codes,
            "codes": codes,
        }
        fields[name] = record
        if codes:
            discrepancies.append({"field": name, "codes": codes})
        else:
            agreements[name] = a
    return {
        "fields": fields,
        "agreements": agreements,
        "discrepancies": discrepancies,
        "accepted": bool(fields) and not discrepancies,
    }


def _excluded(item: Mapping, cutoff: datetime, allowed_kinds: set[str]) -> bool:
    proof = item.get("exclusion_proof")
    if (
        not isinstance(proof, Mapping)
        or type(proof.get("kind")) is not str
        or proof.get("kind") not in allowed_kinds
    ):
        return False
    try:
        sha256_hex(proof.get("evidence_sha256"))
        return _timestamp(proof.get("effective_at")) <= cutoff
    except BatchContractError:
        return False


def calculate_metrics(
    members: Sequence[Mapping],
    *,
    source_pages: Sequence[Mapping],
    cutoff: str,
    profile_hash: str,
    corpus_hash: str,
    input_versions: Mapping | None = None,
) -> dict:
    """Freeze operational numerators and denominators BEFORE the first effect.

    ``exclusion_proof`` must reference private evidence already validated by the
    caller. Labels/booleans alone do not remove a document/page from denominators.
    Callers retain the returned snapshot; progress never recalculates its ratios.
    """
    at = _timestamp(cutoff)
    sha256_hex(profile_hash)
    sha256_hex(corpus_hash)
    pages = {_page_ref(page): page for page in source_pages}
    if len(pages) != len(source_pages):
        raise BatchContractError("BATCH_DUPLICATE_PAGE_OBSERVATION")
    pending_pages = {
        ref
        for ref, page in pages.items()
        if not _excluded(
            page, at, {"already_captured", "exact_duplicate", "non_roster"}
        )
    }
    pending_documents, automatic_documents, automatic_pages = [], [], set()
    excluded_documents, seen = [], set()
    for member in members:
        document_id = sha256_hex(member.get("document_id"))
        if document_id in seen:
            raise BatchContractError("BATCH_DUPLICATE_DOCUMENT")
        seen.add(document_id)
        refs = set(source_refs(member.get("source_slices")))
        if not refs.issubset(pages):
            raise BatchContractError("BATCH_UNKNOWN_SOURCE_PAGE")
        if _excluded(member, at, {"already_captured", "exact_duplicate"}):
            excluded_documents.append(document_id)
            # The member's exact pre-cutoff proof also excludes its physical pages.
            pending_pages.difference_update(refs)
            continue
        pending_documents.append(document_id)
        automatic = (
            member.get("auto_ready") is True
            or (
                type(member.get("status")) is str
                and member.get("status")
                in {"ready", "auto_ready", "automatic_verified"}
            )
        ) and member.get("verification_origin") != "human_corrected"
        if automatic:
            automatic_documents.append(document_id)
            automatic_pages.update(refs)
    if not automatic_pages.issubset(pending_pages):
        raise BatchContractError("BATCH_CONFLICTING_SOURCE_CLASSIFICATION")
    body = {
        "schema": METRICS_SCHEMA,
        "cutoff": cutoff,
        "profile_hash": profile_hash,
        "corpus_hash": corpus_hash,
        "input_versions": deepcopy(dict(input_versions or {})),
        "teams": {
            "numerator": len(automatic_documents),
            "denominator": len(pending_documents),
        },
        "pages": {"numerator": len(automatic_pages), "denominator": len(pending_pages)},
        "pending_document_ids": sorted(pending_documents),
        "automatic_document_ids": sorted(automatic_documents),
        "excluded_document_ids": sorted(excluded_documents),
        "pending_page_refs": [
            {"pdf_sha256": pdf, "pdf_page": page} for pdf, page in sorted(pending_pages)
        ],
    }
    return {**body, "snapshot_id": digest(body)}


def scaling_allowed(metrics: Mapping) -> bool:
    """Require both frozen observed operational ratios >=80%, never accuracy."""
    try:
        body = {key: value for key, value in metrics.items() if key != "snapshot_id"}
        if metrics.get("schema") != METRICS_SCHEMA or digest(body) != sha256_hex(
            metrics.get("snapshot_id")
        ):
            return False
        for dimension in ("teams", "pages"):
            numerator, denominator = (
                metrics[dimension]["numerator"],
                metrics[dimension]["denominator"],
            )
            if (
                type(numerator) is not int
                or type(denominator) is not int
                or not 0 <= numerator <= denominator
                or denominator <= 0
            ):
                return False
            if numerator * 100 < denominator * 80:
                return False
        return True
    except (KeyError, TypeError, ValueError, BatchContractError):
        return False


def validate_quality_sample(
    sample_results: Sequence[Mapping],
    *,
    expected_document_ids: Sequence[str] | None = None,
) -> dict:
    """Compute proposed sample diagnostics, without asserting human authority."""
    seen, false_documents, failed_strata = set(), [], set()
    raw_errors = observed_fields = 0
    for result in sample_results:
        document_id = sha256_hex(result.get("document_id"))
        if document_id in seen:
            raise BatchContractError("BATCH_DUPLICATE_SAMPLE")
        seen.add(document_id)
        classification = result.get("classification")
        outcome = result.get("outcome")
        if (
            type(classification) is not str
            or type(outcome) is not str
            or classification not in {"auto_ready", "exception"}
            or outcome not in {"matches", "critical_error", "exception_confirmed"}
        ):
            raise BatchContractError("BATCH_SAMPLE_INVALID")
        comparisons = result.get("comparisons", [])
        if not isinstance(comparisons, (list, tuple)):
            raise BatchContractError("BATCH_SAMPLE_INVALID")
        critical_wrong = False
        for comparison in comparisons:
            if (
                not isinstance(comparison, Mapping)
                or type(comparison.get("error")) is not bool
                or type(comparison.get("critical")) is not bool
            ):
                raise BatchContractError("BATCH_SAMPLE_INVALID")
            observed_fields += 1
            raw_errors += int(comparison["error"])
            critical_wrong = (
                critical_wrong or comparison["error"] and comparison["critical"]
            )
        is_false = classification == "auto_ready" and (
            outcome == "critical_error" or critical_wrong
        )
        if is_false:
            false_documents.append(document_id)
            if not result.get("strata"):
                raise BatchContractError("BATCH_SAMPLE_STRATUM_MISSING")
            for stratum in result.get("strata", []):
                if not isinstance(stratum, Mapping):
                    raise BatchContractError("BATCH_SAMPLE_INVALID")
                pdf = sha256_hex(stratum.get("pdf_sha256"))
                template, category = stratum.get("template_id"), stratum.get("category")
                if (
                    type(template) is not str
                    or not template
                    or type(category) is not str
                    or not category
                ):
                    raise BatchContractError("BATCH_SAMPLE_INVALID")
                failed_strata.add((pdf, template, category))
    if expected_document_ids is not None and seen != set(expected_document_ids):
        raise BatchContractError("BATCH_SAMPLE_COVERAGE_MISMATCH")
    return {
        "sample_count": len(seen),
        "observed_fields": observed_fields,
        "raw_errors": raw_errors,
        "critical_false_admissions": len(false_documents),
        "false_admitted_document_ids": sorted(false_documents),
        "failed_strata": [list(s) for s in sorted(failed_strata)],
        "passed": bool(seen) and not false_documents,
        "human_authority": False,
    }


def create_qualification_candidate(
    documents: Sequence[Mapping],
    *,
    corpus_hash: str,
    profile_hash: str,
    cutoff: str,
    metrics: Mapping,
    sample_results: Sequence[Mapping],
    input_versions: Mapping | None = None,
    proposed_declaration: Mapping | None = None,
) -> dict:
    """Bind immutable observables; the candidate contains no future UV/proof."""
    _timestamp(cutoff)
    sha256_hex(profile_hash)
    expected = select_sample(documents, corpus_hash=corpus_hash)
    document_lookup = {document["document_id"]: document for document in documents}
    automatic_ids = set(metrics.get("automatic_document_ids", []))
    retained_results = []
    for observed in sample_results:
        document_id = observed.get("document_id")
        if document_id not in document_lookup:
            raise BatchContractError("BATCH_SAMPLE_COVERAGE_MISMATCH")
        retained_results.append(
            {
                **deepcopy(dict(observed)),
                "classification": (
                    "auto_ready" if document_id in automatic_ids else "exception"
                ),
                "strata": [
                    {"pdf_sha256": pdf, "template_id": template, "category": category}
                    for pdf, template, category in _strata(document_lookup[document_id])
                ],
            }
        )
    report = validate_quality_sample(retained_results, expected_document_ids=expected)
    metric_body = {key: value for key, value in metrics.items() if key != "snapshot_id"}
    if (
        metrics.get("snapshot_id") != digest(metric_body)
        or metrics.get("cutoff") != cutoff
        or metrics.get("corpus_hash") != corpus_hash
        or metrics.get("profile_hash") != profile_hash
    ):
        raise BatchContractError("BATCH_METRICS_MISMATCH")
    declaration = deepcopy(dict(proposed_declaration or {}))
    forbidden = {
        "uv_proof",
        "activation",
        "activation_id",
        "consent_id",
        "human_verified",
        "approved",
        "verified_by_human",
    }

    def reject_future(value):
        if isinstance(value, Mapping):
            if set(value) & forbidden:
                raise BatchContractError("BATCH_FUTURE_AUTHORITY_FORBIDDEN")
            for nested in value.values():
                reject_future(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                reject_future(nested)

    reject_future(declaration)
    body = {
        "schema": CANDIDATE_SCHEMA,
        "corpus_hash": corpus_hash,
        "profile_hash": profile_hash,
        "cutoff": cutoff,
        "input_versions": deepcopy(
            dict(
                input_versions
                if input_versions is not None
                else metrics.get("input_versions", {})
            )
        ),
        "sample_document_ids": list(expected),
        "sample_results": retained_results,
        "sample_report": report,
        "metrics": deepcopy(dict(metrics)),
        "proposed_declaration": declaration,
        "human_authority": False,
    }
    return {**body, "candidate_hash": digest(body)}
