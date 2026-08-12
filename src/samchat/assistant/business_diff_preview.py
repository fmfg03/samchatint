"""Read-only business diff previews for owner-needs workflows."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .owner_needs_eval import (
    OwnerNeedsAssessment,
    OwnerNeedsPrompt,
    assess_owner_needs_prompt,
)


CREATE_ENTITY_FOLDER = "create_entity_folder"
CREATE_NATIONAL_PHASE_FOLDER = "create_national_phase_folder"
GENERATE_ACTIVATION_REPORT = "generate_activation_report"
UPDATE_ENTITY_FOLDER = "update_entity_folder"
PLAN_FOLDER_BUILD = "plan_folder_build"

NOT_EXECUTED = "not_executed"
APPROVAL_REQUIRED = "approval_required"
PREVIEW_ONLY = "preview_only"
MISSING_EVIDENCE = "missing_evidence"
PROPOSED = "proposed"
SUPPORTED = "supported"
CANON_REQUIREMENT = "canon_requirement"


ENTITY_FOLDER_FIELDS = (
    "entity_name",
    "tournament",
    "expected_teams",
    "real_teams",
    "players_by_category_age_gender",
    "round_progression",
    "state_phase_operations",
    "operator_payments",
    "equipment_costs",
    "visit_results",
    "photographic_evidence",
)

NATIONAL_PHASE_FIELDS = (
    "tournament_category",
    "host_city",
    "opening_and_final_dates",
    "contracted_hotels_bed_nights",
    "contracted_meals",
    "sports_venue_and_fields",
    "medical_services_description",
    "accidents_with_transfers",
    "staff_travel_costs",
    "hotel_payments",
    "provider_payments",
    "medical_and_insurance_costs",
    "brand_activation_evidence",
)

ACTIVATION_REPORT_FIELDS = (
    "brand_activation_activities",
    "physical_supplier_attendance",
    "sponsor_visitors",
    "photographic_evidence",
    "activation_result",
)


@dataclass(frozen=True)
class ProposedBusinessChange:
    field: str
    proposed_value: object
    source: str
    confidence: str
    reason: str
    status: str = MISSING_EVIDENCE

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BusinessDiffPreview:
    preview_id: str
    operation_type: str
    target: Dict[str, object]
    found_evidence: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    proposed_changes: List[ProposedBusinessChange] = field(default_factory=list)
    blocked_reason: str = APPROVAL_REQUIRED
    approval_required: bool = True
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = PREVIEW_ONLY

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["proposed_changes"] = [
            change.to_dict() for change in self.proposed_changes
        ]
        return payload


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _preview_id(prompt_id: str, operation_type: str, target: Mapping) -> str:
    key = f"{prompt_id}|{operation_type}|{sorted(target.items())}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"bdp_{digest}"


def operation_type_for_owner_prompt(prompt: OwnerNeedsPrompt) -> str:
    text = _normalize(prompt.prompt)
    structural_request = any(
        token in text
        for token in (
            "debe contener",
            "deberia contener",
            "deber\u00eda contener",
            "que datos",
            "qu\u00e9 datos",
            "estructura",
        )
    )
    if "activacion de marcas" in text or "activacion" in text:
        if "genera" in text or "informe" in text:
            return GENERATE_ACTIVATION_REPORT
    if "actualiza" in text:
        return UPDATE_ENTITY_FOLDER
    if "fase nacional" in text and any(
        token in text
        for token in ("crea", "crear", "carpeta", "debe contener", "estructura")
    ):
        return CREATE_NATIONAL_PHASE_FOLDER
    if "carpeta" in text and (
        any(token in text for token in ("crea", "crear"))
        or structural_request
        or "por entidad" in text
        or "cada entidad" in text
    ):
        return CREATE_ENTITY_FOLDER
    return PLAN_FOLDER_BUILD


def _target_for_prompt(prompt: OwnerNeedsPrompt, operation_type: str) -> Dict:
    text = _normalize(prompt.prompt)
    target: Dict[str, object] = {
        "prompt_id": prompt.prompt_id,
        "raw_request": prompt.prompt,
    }
    if "jalisco" in text:
        target["entity"] = "Jalisco"
    if "fase nacional" in text:
        target["folder_scope"] = "national_phase"
    elif "entidad" in text:
        target["folder_scope"] = "entity"
    if "beisbol" in text:
        target["tournament_hint"] = "beisbol"
    if operation_type == GENERATE_ACTIVATION_REPORT:
        target["report_type"] = "brand_activation"
    return target


def _fields_for_operation(
    operation_type: str,
    *,
    assessment: Optional[OwnerNeedsAssessment] = None,
) -> Sequence[str]:
    if assessment is not None:
        expected = set(assessment.prompt.expected_sources)
        if "medical/event_incident" in expected:
            return (
                "medical_services_description",
                "accidents_with_transfers",
                "medical_and_insurance_costs",
            )
    if operation_type == CREATE_NATIONAL_PHASE_FOLDER:
        return NATIONAL_PHASE_FIELDS
    if operation_type == GENERATE_ACTIVATION_REPORT:
        return ACTIVATION_REPORT_FIELDS
    if operation_type in {CREATE_ENTITY_FOLDER, UPDATE_ENTITY_FOLDER}:
        return ENTITY_FOLDER_FIELDS
    return (
        "folder_scope",
        "source_inventory",
        "missing_evidence_inventory",
        "approval_boundary",
    )


def _evidence_by_field(
    available_evidence: Optional[Mapping[str, object]],
) -> Mapping[str, object]:
    if not isinstance(available_evidence, Mapping):
        return {}
    by_field = available_evidence.get("fields")
    if isinstance(by_field, Mapping):
        return by_field
    return available_evidence


def _build_changes(
    *,
    operation_type: str,
    assessment: OwnerNeedsAssessment,
    available_evidence: Optional[Mapping[str, object]],
) -> List[ProposedBusinessChange]:
    evidence = _evidence_by_field(available_evidence)
    changes: List[ProposedBusinessChange] = []
    fields = _fields_for_operation(operation_type, assessment=assessment)
    for field_name in fields:
        if field_name in evidence and evidence[field_name] not in (None, ""):
            changes.append(
                ProposedBusinessChange(
                    field=field_name,
                    proposed_value=evidence[field_name],
                    source="live_evidence",
                    confidence="medium",
                    reason="source_value_available_for_preview",
                    status=SUPPORTED,
                )
            )
            continue

        changes.append(
            ProposedBusinessChange(
                field=field_name,
                proposed_value=None,
                source=CANON_REQUIREMENT,
                confidence="low",
                reason=(
                    assessment.confidence_limit or "live_evidence_not_loaded_for_field"
                ),
                status=MISSING_EVIDENCE,
            )
        )
    return changes


def create_business_diff_preview(
    assessment: OwnerNeedsAssessment,
    *,
    available_evidence: Optional[Mapping[str, object]] = None,
) -> BusinessDiffPreview:
    """Create a read-only preview for one owner-needs assessment."""

    operation_type = operation_type_for_owner_prompt(assessment.prompt)
    target = _target_for_prompt(assessment.prompt, operation_type)
    changes = _build_changes(
        operation_type=operation_type,
        assessment=assessment,
        available_evidence=available_evidence,
    )
    missing = sorted(
        {
            *assessment.evidence_missing,
            *[
                str(change.field)
                for change in changes
                if change.status == MISSING_EVIDENCE
            ],
        }
    )
    found = sorted(
        {
            *assessment.evidence_found,
            *[str(change.field) for change in changes if change.status == SUPPORTED],
        }
    )
    return BusinessDiffPreview(
        preview_id=_preview_id(assessment.prompt.prompt_id, operation_type, target),
        operation_type=operation_type,
        target=target,
        found_evidence=found,
        missing_evidence=missing,
        proposed_changes=changes,
        blocked_reason=APPROVAL_REQUIRED,
        approval_required=True,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=PREVIEW_ONLY,
    )


def create_owner_prompt_business_diff_preview(
    prompt: OwnerNeedsPrompt,
    *,
    available_evidence: Optional[Mapping[str, object]] = None,
) -> BusinessDiffPreview:
    return create_business_diff_preview(
        assess_owner_needs_prompt(prompt),
        available_evidence=available_evidence,
    )


def preview_contains_execution_claim(preview: BusinessDiffPreview) -> bool:
    payload = {
        "blocked_reason": preview.blocked_reason,
        "execution_status": preview.execution_status,
        "audit_language": preview.audit_language,
        "proposed_changes": [
            {
                "field": change.field,
                "source": change.source,
                "confidence": change.confidence,
                "reason": change.reason,
                "status": change.status,
            }
            for change in preview.proposed_changes
        ],
    }
    text = str(payload).lower()
    unsafe_terms = (
        "created",
        "updated",
        "generated",
        "creado",
        "actualizado",
        "generado",
        "ejecutado",
    )
    return any(term in text for term in unsafe_terms)


def evaluate_preview_set(
    prompts: Iterable[OwnerNeedsPrompt],
) -> Dict[str, object]:
    previews = [create_owner_prompt_business_diff_preview(prompt) for prompt in prompts]
    operation_counts: Dict[str, int] = {}
    for preview in previews:
        operation_counts[preview.operation_type] = (
            operation_counts.get(preview.operation_type, 0) + 1
        )
    return {
        "total": len(previews),
        "operation_counts": operation_counts,
        "writes_attempted": sum(p.writes_attempted for p in previews),
        "side_effects_detected": sum(p.side_effects_detected for p in previews),
        "execution_claims_detected": sum(
            1 for preview in previews if preview_contains_execution_claim(preview)
        ),
        "previews": [preview.to_dict() for preview in previews],
    }


__all__ = [
    "APPROVAL_REQUIRED",
    "CREATE_ENTITY_FOLDER",
    "CREATE_NATIONAL_PHASE_FOLDER",
    "GENERATE_ACTIVATION_REPORT",
    "MISSING_EVIDENCE",
    "NOT_EXECUTED",
    "PLAN_FOLDER_BUILD",
    "PREVIEW_ONLY",
    "SUPPORTED",
    "UPDATE_ENTITY_FOLDER",
    "BusinessDiffPreview",
    "ProposedBusinessChange",
    "create_business_diff_preview",
    "create_owner_prompt_business_diff_preview",
    "evaluate_preview_set",
    "operation_type_for_owner_prompt",
    "preview_contains_execution_claim",
]
