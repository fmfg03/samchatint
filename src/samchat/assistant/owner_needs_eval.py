"""Deterministic owner-needs eval contracts for read-only canary closure."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence


PASS = "PASS"
PASS_WITH_CLASSIFIED_GAPS = "PASS_WITH_CLASSIFIED_GAPS"

CODE_FIX_REQUIRED = "CODE_FIX_REQUIRED"
CANON_UPDATE_REQUIRED = "CANON_UPDATE_REQUIRED"
EVIDENCE_DATA_MISSING = "EVIDENCE_DATA_MISSING"
CONFIG_OR_CANARY_GAP = "CONFIG_OR_CANARY_GAP"
PRODUCT_DECISION_REQUIRED = "PRODUCT_DECISION_REQUIRED"
EXPECTED_LIMITATION = "EXPECTED_LIMITATION"
TEST_HARNESS_GAP = "TEST_HARNESS_GAP"

GAP_TYPES = frozenset(
    {
        CODE_FIX_REQUIRED,
        CANON_UPDATE_REQUIRED,
        EVIDENCE_DATA_MISSING,
        CONFIG_OR_CANARY_GAP,
        PRODUCT_DECISION_REQUIRED,
        EXPECTED_LIMITATION,
        TEST_HARNESS_GAP,
    }
)

CANON_SOURCE_TYPES = frozenset(
    {
        "canon",
        "owner_needs",
        "product_canon",
        "context_corpus",
        "tools",
    }
)

LIVE_EVIDENCE_SOURCE_TYPES = frozenset(
    {
        "authority_preview",
        "document",
        "entity",
        "event_incident",
        "expense",
        "finance",
        "inventory/equipment",
        "marketing",
        "media",
        "medical/event_incident",
        "memory",
        "player",
        "provider",
        "sql",
        "team",
        "tournament",
    }
)

SENSITIVE_EVIDENCE_TERMS = (
    "accidente",
    "accidentes",
    "ambulancia",
    "ambulancias",
    "medico",
    "medicos",
    "médico",
    "médicos",
    "seguro",
    "seguros",
    "servicios medicos",
    "servicios médicos",
    "traslado",
)

WRITE_INTENT_TERMS = (
    "actualiza",
    "crear",
    "crea",
    "genera",
    "publica",
)

# This is deliberately a small, read-only cross-section of the owner-needs
# canon.  Create/update prompts are evaluated by the separate preview-boundary
# contract and are not submitted by the authenticated live measurement.
LIVE_CANARY_OWNER_PROMPT_IDS = (
    "AI-OWNER-002",
    "AI-OWNER-003",
    "AI-OWNER-004",
    "AI-OWNER-005",
    "AI-OWNER-007",
    "AI-OWNER-009",
    "AI-OWNER-010",
    "AI-OWNER-015",
    "AI-OWNER-018",
    "AI-OWNER-029",
)

_EXPLICIT_GAP_PHRASES = (
    "no tengo evidencia",
    "no hay evidencia",
    "faltan datos",
    "falta informacion",
    "falta información",
    "informacion insuficiente",
    "información insuficiente",
    "evidencia insuficiente",
)


@dataclass(frozen=True)
class OwnerNeedsPrompt:
    prompt_id: str
    prompt: str
    expected_sources: List[str]
    forbidden_behaviors: List[str]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerNeedsGap:
    prompt_id: str
    summary: str
    current_result: str
    gap_type: str
    probable_cause: str
    requires: str
    decision: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerNeedsAssessment:
    prompt: OwnerNeedsPrompt
    status: str
    evidence_found: List[str] = field(default_factory=list)
    evidence_missing: List[str] = field(default_factory=list)
    confidence_limit: str = ""
    recommended_next_action: str = ""
    gaps: List[OwnerNeedsGap] = field(default_factory=list)
    writes_attempted: int = 0
    side_effects_detected: int = 0

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["prompt"] = self.prompt.to_dict()
        payload["gaps"] = [gap.to_dict() for gap in self.gaps]
        return payload


@dataclass(frozen=True)
class OwnerNeedsLiveCanaryVerdict:
    """Safe, metadata-only verdict for one authenticated owner-needs turn."""

    prompt_id: str
    status: str
    expected_sources: List[str]
    observed_trace_source_categories: List[str]
    missing_expected_sources: List[str]
    evidence_gap_declared: bool
    manual_review_required: bool
    manual_review_reason: str
    policy_failures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _split_cell(value: str) -> List[str]:
    return [
        item.strip().lower()
        for item in re.split(r",|;", value or "")
        if item.strip()
    ]


def parse_owner_needs_eval_set(markdown: str) -> List[OwnerNeedsPrompt]:
    """Parse the owner-needs markdown table into prompt records."""

    prompts: List[OwnerNeedsPrompt] = []
    row_pattern = re.compile(
        r"^\|\s*(AI-OWNER-\d{3})\s*\|\s*\"(.*?)\"\s*\|\s*"
        r"(.*?)\s*\|\s*(.*?)\s*\|$"
    )
    for line in (markdown or "").splitlines():
        match = row_pattern.match(line.strip())
        if not match:
            continue
        prompt_id, prompt, sources, forbidden = match.groups()
        prompts.append(
            OwnerNeedsPrompt(
                prompt_id=prompt_id,
                prompt=prompt,
                expected_sources=_split_cell(sources),
                forbidden_behaviors=_split_cell(forbidden),
            )
        )
    return prompts


def selected_live_canary_owner_prompts(markdown: str) -> List[OwnerNeedsPrompt]:
    """Return the fixed, non-mutating measurement cohort in canon order."""

    prompts_by_id = {
        prompt.prompt_id: prompt for prompt in parse_owner_needs_eval_set(markdown)
    }
    missing = [
        prompt_id
        for prompt_id in LIVE_CANARY_OWNER_PROMPT_IDS
        if prompt_id not in prompts_by_id
    ]
    if missing:
        raise ValueError(
            "owner_needs_live_canary_prompts_missing:" + ",".join(missing)
        )
    return [prompts_by_id[prompt_id] for prompt_id in LIVE_CANARY_OWNER_PROMPT_IDS]


def _trace_source_values(value: object):
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in {
                "source",
                "source_type",
                "sources",
                "source_categories",
                "evidence_type",
            }:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, (list, tuple, set)):
                    for candidate in item:
                        if isinstance(candidate, str):
                            yield candidate
            yield from _trace_source_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _trace_source_values(item)


def observed_trace_source_categories(
    prompt: OwnerNeedsPrompt,
    tool_trace: Iterable[Mapping[str, object]] | None,
) -> List[str]:
    """Report only canon source *categories* seen in a trace, never raw refs.

    A category appearing here is observability metadata, not proof that a
    business fact is correct.  Raw retrieval entries can include operational
    identifiers, so they intentionally do not leave the runner.
    """

    source_values = {
        value.strip().lower()
        for value in _trace_source_values(list(tool_trace or []))
        if value.strip()
    }
    return [
        source
        for source in prompt.expected_sources
        if source in LIVE_EVIDENCE_SOURCE_TYPES and source in source_values
    ]


def assess_owner_needs_live_response(
    prompt: OwnerNeedsPrompt,
    *,
    assistant_message: str,
    tool_trace: Iterable[Mapping[str, object]] | None,
) -> OwnerNeedsLiveCanaryVerdict:
    """Classify a live response without retaining its text or trace payload."""

    message = (assistant_message or "").strip().lower()
    observed = observed_trace_source_categories(prompt, tool_trace)
    expected_live = _requires_live_evidence(prompt)
    missing = [source for source in expected_live if source not in observed]
    gap_declared = any(phrase in message for phrase in _EXPLICIT_GAP_PHRASES)
    failures: List[str] = []

    if not message:
        failures.append("empty_assistant_message")
    if missing and not gap_declared:
        failures.append("missing_evidence_disclosure")

    if failures:
        status = "FAIL"
        manual_review = False
        reason = "Automated contract failure; inspect the safe canary metadata."
    elif missing:
        status = PASS_WITH_CLASSIFIED_GAPS
        manual_review = True
        reason = (
            "The response declared an evidence limit, but human semantic review "
            "is still required to ensure it did not pair that caveat with an "
            "unsupported factual claim."
        )
    else:
        status = PASS_WITH_CLASSIFIED_GAPS
        manual_review = True
        reason = (
            "Trace categories were observed, but factual correctness and the "
            "prompt-specific forbidden behaviors require human semantic review."
        )

    return OwnerNeedsLiveCanaryVerdict(
        prompt_id=prompt.prompt_id,
        status=status,
        expected_sources=list(prompt.expected_sources),
        observed_trace_source_categories=observed,
        missing_expected_sources=missing,
        evidence_gap_declared=gap_declared,
        manual_review_required=manual_review,
        manual_review_reason=reason,
        policy_failures=failures,
    )


def _summary(prompt: OwnerNeedsPrompt) -> str:
    text = prompt.prompt.strip()
    return text[:140]


def _requires_live_evidence(prompt: OwnerNeedsPrompt) -> List[str]:
    return [
        source
        for source in prompt.expected_sources
        if source in LIVE_EVIDENCE_SOURCE_TYPES
    ]


def _has_write_or_artifact_intent(prompt: OwnerNeedsPrompt) -> bool:
    normalized = prompt.prompt.lower()
    return any(term in normalized for term in WRITE_INTENT_TERMS)


def _has_sensitive_evidence_need(prompt: OwnerNeedsPrompt) -> bool:
    normalized = prompt.prompt.lower()
    return any(term in normalized for term in SENSITIVE_EVIDENCE_TERMS)


def _missing_evidence_gap(
    prompt: OwnerNeedsPrompt,
    missing_sources: Sequence[str],
) -> OwnerNeedsGap:
    return OwnerNeedsGap(
        prompt_id=prompt.prompt_id,
        summary=_summary(prompt),
        current_result="pass_with_gap_no_live_evidence_loaded",
        gap_type=EVIDENCE_DATA_MISSING,
        probable_cause=(
            "The eval prompt requires live operational evidence, but this "
            "read-only closure only has canon/eval metadata available."
        ),
        requires=", ".join(missing_sources),
        decision=(
            "Answer with explicit missing-evidence language and do not fill "
            "the gap with generic facts."
        ),
    )


def _preview_gap(prompt: OwnerNeedsPrompt) -> OwnerNeedsGap:
    return OwnerNeedsGap(
        prompt_id=prompt.prompt_id,
        summary=_summary(prompt),
        current_result="pass_with_gap_preview_required",
        gap_type=EXPECTED_LIMITATION,
        probable_cause=(
            "The user asked for create/update/report behavior, but business "
            "diff preview and durable write authority are outside 009K."
        ),
        requires="business_diff_preview_and_human_approval",
        decision=(
            "Return a proposed plan or preview requirement; do not claim the "
            "folder, report, or update was executed."
        ),
    )


def _normalized_supported_evidence(
    *,
    prompt: OwnerNeedsPrompt,
    available_evidence_sources: Iterable[str] | None,
    available_evidence_by_prompt: Mapping[str, Iterable[str]] | None,
) -> List[str]:
    available = {
        str(source or "").strip().lower()
        for source in available_evidence_sources or []
        if str(source or "").strip()
    }
    prompt_sources = (
        available_evidence_by_prompt.get(prompt.prompt_id, [])
        if available_evidence_by_prompt
        else []
    )
    available.update(
        str(source or "").strip().lower()
        for source in prompt_sources
        if str(source or "").strip()
    )
    return [
        source
        for source in prompt.expected_sources
        if source in LIVE_EVIDENCE_SOURCE_TYPES and source in available
    ]


def assess_owner_needs_prompt(
    prompt: OwnerNeedsPrompt,
    *,
    available_evidence_sources: Iterable[str] | None = None,
    available_evidence_by_prompt: Mapping[str, Iterable[str]] | None = None,
) -> OwnerNeedsAssessment:
    """Classify one prompt under the 009K read-only quality contract."""

    supported_live_sources = _normalized_supported_evidence(
        prompt=prompt,
        available_evidence_sources=available_evidence_sources,
        available_evidence_by_prompt=available_evidence_by_prompt,
    )
    supported_live = set(supported_live_sources)
    missing_sources = [
        source
        for source in _requires_live_evidence(prompt)
        if source not in supported_live
    ]
    gaps: List[OwnerNeedsGap] = []
    if missing_sources:
        gaps.append(_missing_evidence_gap(prompt, missing_sources))
    if _has_write_or_artifact_intent(prompt):
        gaps.append(_preview_gap(prompt))

    if gaps:
        status = PASS_WITH_CLASSIFIED_GAPS
        evidence_found = [
            source
            for source in prompt.expected_sources
            if source in CANON_SOURCE_TYPES or source in supported_live
        ]
        if supported_live_sources:
            confidence_limit = (
                "Some live operational evidence is available, but remaining "
                "missing sources still limit any complete folder claim."
            )
        else:
            confidence_limit = (
                "Canon can define the required folder fields, but live facts are "
                "not established for the missing evidence sources."
            )
        if _has_sensitive_evidence_need(prompt):
            confidence_limit = (
                "No concrete medical, accident, ambulance, insurance, or "
                "transfer evidence is loaded for this prompt."
            )
    else:
        status = PASS
        evidence_found = list(prompt.expected_sources)
        confidence_limit = (
            "The prompt can be answered from versioned owner-needs canon "
            "without asserting live operational facts."
        )

    return OwnerNeedsAssessment(
        prompt=prompt,
        status=status,
        evidence_found=evidence_found,
        evidence_missing=missing_sources,
        confidence_limit=confidence_limit,
        recommended_next_action=recommended_next_action(prompt),
        gaps=gaps,
    )


def recommended_next_action(prompt: OwnerNeedsPrompt) -> str:
    if _has_sensitive_evidence_need(prompt):
        return (
            "Inspect document, medical/event_incident, finance, and provider "
            "evidence before describing services, accidents, transfers, "
            "costs, or insurance."
        )
    if _has_write_or_artifact_intent(prompt):
        return (
            "Produce a read-only preview/diff with missing fields and sources "
            "before any durable create/update/report action."
        )
    if _requires_live_evidence(prompt):
        return (
            "Retrieve the listed live evidence sources, then answer with "
            "found evidence and missing fields separated."
        )
    return (
        "Answer from canon and state that it is a product requirement, not "
        "proof that the facts already exist."
    )


def build_owner_evidence_gap_response(
    assessment: OwnerNeedsAssessment,
) -> Dict[str, object]:
    """Build the mandatory read-only owner-needs response contract."""

    if assessment.evidence_missing:
        missing = ", ".join(assessment.evidence_missing)
        answer = (
            "No tengo evidencia concreta cargada para: "
            f"{missing}. Puedo usar el canon para decir que debe revisarse, "
            "pero no debo presentarlo como hecho ocurrido."
        )
    elif assessment.gaps:
        answer = (
            "Tengo evidencia suficiente para las fuentes requeridas, pero esta "
            "accion necesita preview/diff y autorizacion antes de cualquier "
            "cambio durable."
        )
    else:
        answer = (
            "Puedo responder desde el canon versionado del owner-needs sin "
            "afirmar evidencia viva."
        )

    return {
        "status": assessment.status,
        "answer": answer,
        "canon": list(assessment.prompt.expected_sources),
        "evidence_found": list(assessment.evidence_found),
        "evidence_missing": list(assessment.evidence_missing),
        "confidence_limit": assessment.confidence_limit,
        "recommended_next_action": assessment.recommended_next_action,
        "gap_classifications": [gap.to_dict() for gap in assessment.gaps],
        "writes_attempted": assessment.writes_attempted,
        "side_effects_detected": assessment.side_effects_detected,
        "audit_language": "proposed_or_missing_evidence_only",
    }


def evaluate_owner_needs_prompts(
    prompts: Iterable[OwnerNeedsPrompt],
    *,
    available_evidence_sources: Iterable[str] | None = None,
    available_evidence_by_prompt: Mapping[str, Iterable[str]] | None = None,
) -> Dict[str, object]:
    assessments = [
        assess_owner_needs_prompt(
            prompt,
            available_evidence_sources=available_evidence_sources,
            available_evidence_by_prompt=available_evidence_by_prompt,
        )
        for prompt in prompts
    ]
    status_counts: Dict[str, int] = {}
    gap_counts: Dict[str, int] = {}
    for assessment in assessments:
        status_counts[assessment.status] = (
            status_counts.get(assessment.status, 0) + 1
        )
        for gap in assessment.gaps:
            gap_counts[gap.gap_type] = gap_counts.get(gap.gap_type, 0) + 1

    final_decision = (
        PASS
        if status_counts.get(PASS_WITH_CLASSIFIED_GAPS, 0) == 0
        else PASS_WITH_CLASSIFIED_GAPS
    )
    return {
        "final_decision": final_decision,
        "total": len(assessments),
        "status_counts": status_counts,
        "gap_counts": gap_counts,
        "writes_attempted": sum(a.writes_attempted for a in assessments),
        "side_effects_detected": sum(
            a.side_effects_detected for a in assessments
        ),
        "assessments": [assessment.to_dict() for assessment in assessments],
    }


__all__ = [
    "EVIDENCE_DATA_MISSING",
    "EXPECTED_LIMITATION",
    "GAP_TYPES",
    "LIVE_CANARY_OWNER_PROMPT_IDS",
    "PASS",
    "PASS_WITH_CLASSIFIED_GAPS",
    "OwnerNeedsAssessment",
    "OwnerNeedsGap",
    "OwnerNeedsLiveCanaryVerdict",
    "OwnerNeedsPrompt",
    "assess_owner_needs_live_response",
    "assess_owner_needs_prompt",
    "build_owner_evidence_gap_response",
    "evaluate_owner_needs_prompts",
    "parse_owner_needs_eval_set",
    "recommended_next_action",
    "selected_live_canary_owner_prompts",
]
