"""Conversational response packs for owner proposal workflows."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Sequence

from .business_diff_preview import NOT_EXECUTED
from .owner_folder_builder import (
    OwnerFolderProposal,
    build_owner_prompt_folder_proposal,
)
from .owner_folder_revision import (
    BLOCKED_WRITE_DISABLED,
    OwnerFolderRevision,
    revise_owner_folder_proposal,
)
from .owner_needs_eval import OwnerNeedsPrompt


OPERATOR_RESPONSE_PACK_ONLY = "operator_response_pack_only"
SOURCE_FOLDER_PROPOSAL = "folder_proposal"
SOURCE_FOLDER_REVISION = "folder_revision"
SAFETY_READONLY = "readonly_pending_approval"
SAFETY_BLOCKED_WRITE_DISABLED = "blocked_write_disabled"


MEDICAL_EVIDENCE_SOURCES = (
    "medical/event_incident",
    "medical_services_description",
    "accidents_with_transfers",
    "medical_and_insurance_costs",
)


@dataclass(frozen=True)
class OwnerOperatorResponsePack:
    response_id: str
    source_type: str
    source_id: str
    headline: str
    summary: str
    plan: List[str] = field(default_factory=list)
    evidence_found: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    proposed_changes: List[str] = field(default_factory=list)
    approval_boundary: str = ""
    next_questions: List[str] = field(default_factory=list)
    safety_status: str = SAFETY_READONLY
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OPERATOR_RESPONSE_PACK_ONLY

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _response_id(source_type: str, source_id: str) -> str:
    key = f"{source_type}|{source_id}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"orp_{digest}"


def _section_titles(proposal: OwnerFolderProposal) -> List[str]:
    return [section.title for section in proposal.sections]


def _field_labels_by_status(
    proposal: OwnerFolderProposal,
    status: str,
) -> List[str]:
    labels: List[str] = []
    for section in proposal.sections:
        for field_item in section.fields:
            if field_item.status == status:
                labels.append(field_item.label)
    return labels


def _proposal_changes(proposal: OwnerFolderProposal) -> List[str]:
    changes = []
    for section in proposal.sections:
        changes.append(
            f"Seccion pendiente de revision: {section.title}"
        )
    return changes


def _has_medical_gap(missing_evidence: Sequence[str]) -> bool:
    missing = {str(item) for item in missing_evidence}
    return any(item in missing for item in MEDICAL_EVIDENCE_SOURCES)


def _medical_missing_sentence() -> str:
    return (
        "No tengo evidencia concreta cargada de servicios medicos, "
        "accidentes, seguros o traslados para cerrar esa parte."
    )


def _next_questions(missing_evidence: Sequence[str]) -> List[str]:
    questions = [
        "Que evidencia quieres cargar o revisar primero?",
        "Quieres que refine la propuesta antes de preparar aprobacion?",
    ]
    if _has_medical_gap(missing_evidence):
        questions.insert(
            0,
            "Hay documentos medicos, accidentes, seguros o traslados "
            "que deban agregarse como evidencia?",
        )
    return questions


def build_response_pack_from_proposal(
    proposal: OwnerFolderProposal,
) -> OwnerOperatorResponsePack:
    missing = sorted(set(proposal.missing_evidence))
    found = [
        str(field_name)
        for field_name in proposal.evidence_summary.get("supported_fields", [])
    ]
    if not found:
        found = _field_labels_by_status(proposal, "supported")

    missing_fields = _field_labels_by_status(proposal, "missing_evidence")
    summary_parts = [
        "Prepare una propuesta de carpeta en modo solo lectura.",
        "La propuesta queda pendiente de aprobacion humana y no se ejecuto.",
    ]
    if _has_medical_gap(missing):
        summary_parts.append(_medical_missing_sentence())

    return OwnerOperatorResponsePack(
        response_id=_response_id(
            SOURCE_FOLDER_PROPOSAL, proposal.folder_id
        ),
        source_type=SOURCE_FOLDER_PROPOSAL,
        source_id=proposal.folder_id,
        headline="Propuesta de carpeta lista para revision",
        summary=" ".join(summary_parts),
        plan=[
            "Revisar secciones propuestas: "
            + ", ".join(_section_titles(proposal)),
            "Completar evidencia faltante antes de cualquier aprobacion.",
            "Mantener la ejecucion bloqueada hasta aprobacion explicita.",
        ],
        evidence_found=found,
        missing_evidence=missing,
        proposed_changes=_proposal_changes(proposal),
        approval_boundary=(
            "approval_required=true; execution_status=not_executed"
        ),
        next_questions=_next_questions(missing + missing_fields),
        safety_status=SAFETY_READONLY,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OPERATOR_RESPONSE_PACK_ONLY,
    )


def build_response_pack_from_revision(
    revision: OwnerFolderRevision,
) -> OwnerOperatorResponsePack:
    blocked = revision.revision_status == BLOCKED_WRITE_DISABLED
    safety_status = (
        SAFETY_BLOCKED_WRITE_DISABLED if blocked else SAFETY_READONLY
    )
    headline = "Revision pendiente de aprobacion"
    if blocked:
        headline = "Solicitud bloqueada: escrituras deshabilitadas"

    summary_parts = [
        "Prepare una respuesta sobre la revision solicitada.",
        "La revision sigue en modo solo lectura y no se ejecuto.",
    ]
    if blocked:
        summary_parts.append(
            "La solicitud pide una accion de escritura; queda bloqueada "
            "porque las escrituras estan deshabilitadas."
        )
    if _has_medical_gap(revision.missing_evidence):
        summary_parts.append(_medical_missing_sentence())

    proposed_changes = [
        f"Seccion marcada para revision: {section_id}"
        for section_id in revision.changed_sections
    ]
    if blocked:
        proposed_changes = [
            "No se propusieron cambios ejecutables porque la solicitud "
            "requiere escritura."
        ]

    return OwnerOperatorResponsePack(
        response_id=_response_id(
            SOURCE_FOLDER_REVISION, revision.revision_id
        ),
        source_type=SOURCE_FOLDER_REVISION,
        source_id=revision.revision_id,
        headline=headline,
        summary=" ".join(summary_parts),
        plan=[
            "Revisar cambios solicitados: "
            + (", ".join(revision.changed_sections) or "ninguno"),
            "Conservar secciones sin cambio: "
            + (", ".join(revision.unchanged_sections) or "ninguna"),
            "Mantener la ejecucion bloqueada hasta aprobacion explicita.",
        ],
        evidence_found=[],
        missing_evidence=sorted(set(revision.missing_evidence)),
        proposed_changes=proposed_changes,
        approval_boundary=(
            f"{revision.blocked_reason}; "
            "execution_status=not_executed"
        ),
        next_questions=_next_questions(revision.missing_evidence),
        safety_status=safety_status,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OPERATOR_RESPONSE_PACK_ONLY,
    )


def response_pack_contains_execution_claim(
    response_pack: OwnerOperatorResponsePack,
) -> bool:
    payload = response_pack.to_dict()
    text = str(payload).lower()
    text = text.replace(NOT_EXECUTED, "")
    unsafe_terms = (
        "creado",
        "actualizado",
        "ejecutado",
        "enviado",
        "generado",
        "created",
        "updated",
        "executed",
        "sent notification",
        "telegram sent",
        "email sent",
        "webhook sent",
    )
    return any(term in text for term in unsafe_terms)


def evaluate_response_pack_set(
    prompts: Iterable[OwnerNeedsPrompt],
    *,
    requested_change: str = "marca evidencia faltante y separa secciones",
) -> Dict[str, object]:
    proposals = [
        build_owner_prompt_folder_proposal(prompt) for prompt in prompts
    ]
    proposal_packs = [
        build_response_pack_from_proposal(proposal)
        for proposal in proposals
    ]
    revision_packs = [
        build_response_pack_from_revision(
            revise_owner_folder_proposal(proposal, requested_change)
        )
        for proposal in proposals
    ]
    packs = [*proposal_packs, *revision_packs]
    return {
        "proposal_pack_count": len(proposal_packs),
        "revision_pack_count": len(revision_packs),
        "total": len(packs),
        "writes_attempted": sum(pack.writes_attempted for pack in packs),
        "side_effects_detected": sum(
            pack.side_effects_detected for pack in packs
        ),
        "execution_claims_detected": sum(
            1 for pack in packs if response_pack_contains_execution_claim(pack)
        ),
        "packs": [pack.to_dict() for pack in packs],
    }


__all__ = [
    "OPERATOR_RESPONSE_PACK_ONLY",
    "SAFETY_BLOCKED_WRITE_DISABLED",
    "SAFETY_READONLY",
    "SOURCE_FOLDER_PROPOSAL",
    "SOURCE_FOLDER_REVISION",
    "OwnerOperatorResponsePack",
    "build_response_pack_from_proposal",
    "build_response_pack_from_revision",
    "evaluate_response_pack_set",
    "response_pack_contains_execution_claim",
]
