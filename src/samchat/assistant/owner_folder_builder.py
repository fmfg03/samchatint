"""Read-only owner folder proposal builder for SamChat assistant previews."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from .business_diff_preview import (
    CREATE_ENTITY_FOLDER,
    CREATE_NATIONAL_PHASE_FOLDER,
    GENERATE_ACTIVATION_REPORT,
    MISSING_EVIDENCE,
    NOT_EXECUTED,
    SUPPORTED,
    UPDATE_ENTITY_FOLDER,
    BusinessDiffPreview,
    ProposedBusinessChange,
    create_owner_prompt_business_diff_preview,
)
from .owner_needs_eval import OwnerNeedsPrompt


ENTITY_FOLDER_PROPOSAL = "entity_folder_proposal"
NATIONAL_PHASE_FOLDER_PROPOSAL = "national_phase_folder_proposal"
ACTIVATION_REPORT_PROPOSAL = "activation_report_proposal"
FOLDER_BUILD_PLAN_PROPOSAL = "folder_build_plan_proposal"

FOLDER_PROPOSAL_ONLY = "folder_proposal_only"
APPROVAL_REQUIRED = "approval_required"
SUPPORTED_STATUS = "supported"
MISSING_EVIDENCE_STATUS = "missing_evidence"


ENTITY_SECTION_FIELDS = {
    "operations": {
        "title": "Operaciones",
        "fields": (
            "entity_name",
            "tournament",
            "expected_teams",
            "real_teams",
            "players_by_category_age_gender",
            "round_progression",
            "state_phase_operations",
            "visit_results",
        ),
    },
    "finance": {
        "title": "Finanzas",
        "fields": (
            "operator_payments",
            "equipment_costs",
        ),
    },
    "marketing_materiality": {
        "title": "Marketing / Materialidad",
        "fields": ("photographic_evidence",),
    },
}

NATIONAL_PHASE_SECTION_FIELDS = {
    "operations": {
        "title": "Operaciones",
        "fields": (
            "tournament_category",
            "host_city",
            "opening_and_final_dates",
            "contracted_hotels_bed_nights",
            "contracted_meals",
            "sports_venue_and_fields",
            "medical_services_description",
            "accidents_with_transfers",
        ),
    },
    "finance": {
        "title": "Finanzas",
        "fields": (
            "staff_travel_costs",
            "hotel_payments",
            "provider_payments",
            "medical_and_insurance_costs",
        ),
    },
    "marketing": {
        "title": "Marketing",
        "fields": ("brand_activation_evidence",),
    },
}

ACTIVATION_SECTION_FIELDS = {
    "activation": {
        "title": "Activacion",
        "fields": (
            "brand_activation_activities",
            "physical_supplier_attendance",
            "sponsor_visitors",
            "activation_result",
        ),
    },
    "photographic_evidence": {
        "title": "Evidencia Fotografica",
        "fields": ("photographic_evidence",),
    },
}

PLAN_SECTION_FIELDS = {
    "scope": {
        "title": "Alcance",
        "fields": ("folder_scope",),
    },
    "sources_to_query": {
        "title": "Fuentes a consultar",
        "fields": ("source_inventory",),
    },
    "pending": {
        "title": "Pendientes",
        "fields": ("missing_evidence_inventory", "approval_boundary"),
    },
}


@dataclass(frozen=True)
class OwnerFolderField:
    field: str
    label: str
    value: object
    source: str
    status: str
    confidence: str
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerFolderSection:
    section_id: str
    title: str
    fields: List[OwnerFolderField] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["fields"] = [field_item.to_dict() for field_item in self.fields]
        return payload


@dataclass(frozen=True)
class OwnerFolderProposal:
    folder_id: str
    folder_type: str
    target: Dict[str, object]
    sections: List[OwnerFolderSection] = field(default_factory=list)
    evidence_summary: Dict[str, object] = field(default_factory=dict)
    missing_evidence: List[str] = field(default_factory=list)
    preview_id: str = ""
    approval_required: bool = True
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = FOLDER_PROPOSAL_ONLY

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["sections"] = [section.to_dict() for section in self.sections]
        return payload


def folder_type_for_preview(preview: BusinessDiffPreview) -> str:
    if preview.operation_type in {CREATE_ENTITY_FOLDER, UPDATE_ENTITY_FOLDER}:
        return ENTITY_FOLDER_PROPOSAL
    if preview.operation_type == CREATE_NATIONAL_PHASE_FOLDER:
        return NATIONAL_PHASE_FOLDER_PROPOSAL
    if preview.operation_type == GENERATE_ACTIVATION_REPORT:
        return ACTIVATION_REPORT_PROPOSAL
    return FOLDER_BUILD_PLAN_PROPOSAL


def _proposal_id(preview: BusinessDiffPreview, folder_type: str) -> str:
    key = f"{preview.preview_id}|{folder_type}|" f"{sorted(preview.target.items())}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"ofp_{digest}"


FIELD_LABELS = {
    "entity_name": "Nombre de la entidad",
    "tournament": "Torneo",
    "expected_teams": "Equipos esperados por categoria/genero",
    "real_teams": "Equipos reales participantes",
    "players_by_category_age_gender": "Jugadores por categoria, edad y genero",
    "round_progression": "Equipos que superan cada ronda",
    "state_phase_operations": "Organizacion de fase estatal",
    "operator_payments": "Ayudas y pagos sucesivos al operador",
    "equipment_costs": "Uniformes, balones, equipamiento y utileria",
    "visit_results": "Resultados y gastos de visitas",
    "photographic_evidence": "Fotografias y materialidad",
    "tournament_category": "Torneo y categoria",
    "host_city": "Ciudad sede",
    "opening_and_final_dates": "Fechas de inauguracion, clausura y finales",
    "contracted_hotels_bed_nights": "Hoteles y camas-noche contratadas",
    "contracted_meals": "Desayunos, comidas, box lunch y cenas",
    "sports_venue_and_fields": "Unidad deportiva, numero y tipo de canchas",
    "medical_services_description": "Servicios medicos en sede",
    "accidents_with_transfers": "Accidentes con traslado",
    "staff_travel_costs": "Viajes del personal de PS a la sede",
    "hotel_payments": "Pagos a hoteles por servicio",
    "provider_payments": "Pagos a proveedores de finales",
    "medical_and_insurance_costs": "Costos medicos y seguros",
    "brand_activation_evidence": "Evidencia de activacion de marcas",
    "brand_activation_activities": "Actividades de activacion realizadas",
    "physical_supplier_attendance": "Proveedores presentes en activacion",
    "sponsor_visitors": "Visitantes vinculados al patrocinador",
    "activation_result": "Resultado de la activacion",
    "folder_scope": "Alcance de la carpeta",
    "source_inventory": "Fuentes a consultar",
    "missing_evidence_inventory": "Inventario de evidencia faltante",
    "approval_boundary": "Frontera de aprobacion",
}


def _label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


def _field_status(change: ProposedBusinessChange) -> str:
    if change.status == SUPPORTED:
        return SUPPORTED_STATUS
    return MISSING_EVIDENCE_STATUS


def _field_from_change(change: ProposedBusinessChange) -> OwnerFolderField:
    return OwnerFolderField(
        field=change.field,
        label=_label(change.field),
        value=change.proposed_value,
        source=change.source,
        status=_field_status(change),
        confidence=change.confidence,
        reason=change.reason,
    )


def _section_schema(folder_type: str) -> Mapping[str, Mapping[str, object]]:
    if folder_type == ENTITY_FOLDER_PROPOSAL:
        return ENTITY_SECTION_FIELDS
    if folder_type == NATIONAL_PHASE_FOLDER_PROPOSAL:
        return NATIONAL_PHASE_SECTION_FIELDS
    if folder_type == ACTIVATION_REPORT_PROPOSAL:
        return ACTIVATION_SECTION_FIELDS
    return PLAN_SECTION_FIELDS


def _sections_for_preview(
    preview: BusinessDiffPreview,
    folder_type: str,
) -> List[OwnerFolderSection]:
    changes = {change.field: change for change in preview.proposed_changes}
    sections: List[OwnerFolderSection] = []
    used_fields = set()
    for section_id, spec in _section_schema(folder_type).items():
        fields = []
        for field_name in spec["fields"]:
            change = changes.get(str(field_name))
            if change is None:
                continue
            fields.append(_field_from_change(change))
            used_fields.add(change.field)
        if fields:
            sections.append(
                OwnerFolderSection(
                    section_id=section_id,
                    title=str(spec["title"]),
                    fields=fields,
                )
            )

    remaining_missing = [
        _field_from_change(change)
        for change in preview.proposed_changes
        if (change.field not in used_fields and change.status == MISSING_EVIDENCE)
    ]
    if remaining_missing:
        sections.append(
            OwnerFolderSection(
                section_id="missing",
                title="Faltantes",
                fields=remaining_missing,
            )
        )
    return sections


def _evidence_summary(preview: BusinessDiffPreview) -> Dict[str, object]:
    supported_fields = [
        change.field
        for change in preview.proposed_changes
        if change.status == SUPPORTED
    ]
    missing_fields = [
        change.field
        for change in preview.proposed_changes
        if change.status == MISSING_EVIDENCE
    ]
    return {
        "preview_id": preview.preview_id,
        "found_evidence_count": len(preview.found_evidence),
        "missing_evidence_count": len(preview.missing_evidence),
        "supported_field_count": len(supported_fields),
        "missing_field_count": len(missing_fields),
        "supported_fields": supported_fields,
        "missing_fields": missing_fields,
    }


def build_owner_folder_proposal(
    preview: BusinessDiffPreview,
) -> OwnerFolderProposal:
    """Build an inert owner folder proposal from a read-only preview."""

    folder_type = folder_type_for_preview(preview)
    return OwnerFolderProposal(
        folder_id=_proposal_id(preview, folder_type),
        folder_type=folder_type,
        target=dict(preview.target),
        sections=_sections_for_preview(preview, folder_type),
        evidence_summary=_evidence_summary(preview),
        missing_evidence=sorted(set(preview.missing_evidence)),
        preview_id=preview.preview_id,
        approval_required=True,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=FOLDER_PROPOSAL_ONLY,
    )


def build_owner_prompt_folder_proposal(
    prompt: OwnerNeedsPrompt,
    *,
    available_evidence: Optional[Mapping[str, object]] = None,
) -> OwnerFolderProposal:
    preview = create_owner_prompt_business_diff_preview(
        prompt,
        available_evidence=available_evidence,
    )
    return build_owner_folder_proposal(preview)


def folder_proposal_contains_execution_claim(
    proposal: OwnerFolderProposal,
) -> bool:
    payload = proposal.to_dict()
    text = str(payload).lower()
    text = text.replace(NOT_EXECUTED, "")
    text = re.sub(r"\b(create|update|generate)_[a-z_]+\b", "", text)
    unsafe_terms = (
        "created",
        "updated",
        "generated",
        "creado",
        "actualizado",
        "generado",
        "ejecutado",
        "executed successfully",
        "sent notification",
    )
    return any(term in text for term in unsafe_terms)


def evaluate_owner_folder_proposal_set(
    prompts: Iterable[OwnerNeedsPrompt],
) -> Dict[str, object]:
    proposals = [build_owner_prompt_folder_proposal(prompt) for prompt in prompts]
    folder_type_counts: Dict[str, int] = {}
    for proposal in proposals:
        folder_type_counts[proposal.folder_type] = (
            folder_type_counts.get(proposal.folder_type, 0) + 1
        )
    return {
        "total": len(proposals),
        "folder_type_counts": folder_type_counts,
        "writes_attempted": sum(proposal.writes_attempted for proposal in proposals),
        "side_effects_detected": sum(
            proposal.side_effects_detected for proposal in proposals
        ),
        "execution_claims_detected": sum(
            1
            for proposal in proposals
            if folder_proposal_contains_execution_claim(proposal)
        ),
        "proposals": [proposal.to_dict() for proposal in proposals],
    }


__all__ = [
    "ACTIVATION_REPORT_PROPOSAL",
    "APPROVAL_REQUIRED",
    "ENTITY_FOLDER_PROPOSAL",
    "FOLDER_BUILD_PLAN_PROPOSAL",
    "FIELD_LABELS",
    "FOLDER_PROPOSAL_ONLY",
    "MISSING_EVIDENCE_STATUS",
    "NATIONAL_PHASE_FOLDER_PROPOSAL",
    "SUPPORTED_STATUS",
    "OwnerFolderField",
    "OwnerFolderProposal",
    "OwnerFolderSection",
    "build_owner_folder_proposal",
    "build_owner_prompt_folder_proposal",
    "evaluate_owner_folder_proposal_set",
    "folder_proposal_contains_execution_claim",
    "folder_type_for_preview",
]
