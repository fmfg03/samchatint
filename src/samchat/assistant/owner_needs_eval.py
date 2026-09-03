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
    "PASS",
    "PASS_WITH_CLASSIFIED_GAPS",
    "OwnerNeedsAssessment",
    "OwnerNeedsGap",
    "OwnerNeedsPrompt",
    "assess_owner_needs_prompt",
    "build_owner_evidence_gap_response",
    "evaluate_owner_needs_prompts",
    "parse_owner_needs_eval_set",
    "recommended_next_action",
]
