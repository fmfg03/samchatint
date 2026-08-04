"""Conversational revisions for read-only owner folder proposals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List

from .business_diff_preview import NOT_EXECUTED
from .owner_folder_builder import (
    FOLDER_PROPOSAL_ONLY,
    OwnerFolderProposal,
    build_owner_prompt_folder_proposal,
)
from .owner_needs_eval import OwnerNeedsPrompt


FOLDER_REVISION_PROPOSAL_ONLY = "folder_revision_proposal_only"
REVISION_PROPOSED = "revision_proposed"
BLOCKED_WRITE_DISABLED = "blocked_write_disabled"
APPROVAL_REQUIRED = "approval_required"


WRITE_REQUEST_TERMS = (
    "actualiza",
    "actualizala",
    "actualízala",
    "crea",
    "creala",
    "créala",
    "ejecuta",
    "envia",
    "envía",
    "exporta",
    "genera",
    "guarda",
    "manda",
    "publica",
)

SECTION_HINTS = {
    "finance": (
        "finanza",
        "finanzas",
        "gasto",
        "gastos",
        "pago",
        "pagos",
        "presupuesto",
        "proveedor",
    ),
    "marketing_materiality": (
        "foto",
        "fotos",
        "marca",
        "marketing",
        "materialidad",
        "patrocinador",
    ),
    "medical": (
        "accidente",
        "accidentes",
        "ambulancia",
        "medico",
        "medicos",
        "médico",
        "médicos",
        "seguro",
        "traslado",
    ),
    "operations": (
        "cancha",
        "equipo",
        "equipos",
        "hotel",
        "jugador",
        "jugadores",
        "operacion",
        "operación",
        "sede",
    ),
}


@dataclass(frozen=True)
class OwnerFolderRevision:
    revision_id: str
    base_folder_id: str
    base_preview_id: str
    requested_change: str
    revision_status: str
    changed_sections: List[str] = field(default_factory=list)
    unchanged_sections: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    blocked_reason: str = APPROVAL_REQUIRED
    approval_required: bool = True
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = FOLDER_REVISION_PROPOSAL_ONLY

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _revision_id(
    proposal: OwnerFolderProposal,
    requested_change: str,
) -> str:
    key = f"{proposal.folder_id}|{proposal.preview_id}|{requested_change}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"ofr_{digest}"


def _requested_write(text: str) -> bool:
    normalized = _normalize(text)
    return any(term in normalized for term in WRITE_REQUEST_TERMS)


def _proposal_section_ids(proposal: OwnerFolderProposal) -> List[str]:
    return [section.section_id for section in proposal.sections]


def _section_hints_for_change(requested_change: str) -> List[str]:
    normalized = _normalize(requested_change)
    sections = [
        section_id
        for section_id, hints in SECTION_HINTS.items()
        if any(hint in normalized for hint in hints)
    ]
    if "faltante" in normalized or "faltantes" in normalized:
        sections.append("missing")
    return sorted(set(sections))


def _changed_sections(
    proposal: OwnerFolderProposal,
    requested_change: str,
) -> List[str]:
    existing = set(_proposal_section_ids(proposal))
    hinted = _section_hints_for_change(requested_change)
    if not hinted:
        return ["revision_notes"]
    return sorted(set(hinted) | (set(hinted) & existing))


def _unchanged_sections(
    proposal: OwnerFolderProposal,
    changed_sections: List[str],
) -> List[str]:
    changed = set(changed_sections)
    return [
        section_id
        for section_id in _proposal_section_ids(proposal)
        if section_id not in changed
    ]


def revise_owner_folder_proposal(
    proposal: OwnerFolderProposal,
    requested_change: str,
) -> OwnerFolderRevision:
    """Return an inert conversational revision for a folder proposal."""

    wants_write = _requested_write(requested_change)
    changed_sections: List[str] = []
    if not wants_write:
        changed_sections = _changed_sections(proposal, requested_change)

    status = REVISION_PROPOSED
    blocked_reason = APPROVAL_REQUIRED
    if wants_write:
        status = BLOCKED_WRITE_DISABLED
        blocked_reason = BLOCKED_WRITE_DISABLED

    return OwnerFolderRevision(
        revision_id=_revision_id(proposal, requested_change),
        base_folder_id=proposal.folder_id,
        base_preview_id=proposal.preview_id,
        requested_change=requested_change,
        revision_status=status,
        changed_sections=changed_sections,
        unchanged_sections=_unchanged_sections(proposal, changed_sections),
        missing_evidence=sorted(set(proposal.missing_evidence)),
        blocked_reason=blocked_reason,
        approval_required=True,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=FOLDER_REVISION_PROPOSAL_ONLY,
    )


def folder_revision_contains_execution_claim(
    revision: OwnerFolderRevision,
) -> bool:
    payload = revision.to_dict()
    payload.pop("requested_change", None)
    text = str(payload).lower()
    text = text.replace(NOT_EXECUTED, "")
    text = text.replace(FOLDER_PROPOSAL_ONLY, "")
    text = text.replace(FOLDER_REVISION_PROPOSAL_ONLY, "")
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
        "telegram sent",
        "email sent",
        "webhook sent",
    )
    return any(term in text for term in unsafe_terms)


def evaluate_owner_folder_revision_set(
    prompts: Iterable[OwnerNeedsPrompt],
    *,
    requested_change: str = "marca evidencia faltante y separa secciones",
) -> Dict[str, object]:
    revisions = [
        revise_owner_folder_proposal(
            build_owner_prompt_folder_proposal(prompt),
            requested_change,
        )
        for prompt in prompts
    ]
    status_counts: Dict[str, int] = {}
    for revision in revisions:
        status_counts[revision.revision_status] = (
            status_counts.get(revision.revision_status, 0) + 1
        )
    return {
        "total": len(revisions),
        "status_counts": status_counts,
        "writes_attempted": sum(
            revision.writes_attempted for revision in revisions
        ),
        "side_effects_detected": sum(
            revision.side_effects_detected for revision in revisions
        ),
        "execution_claims_detected": sum(
            1
            for revision in revisions
            if folder_revision_contains_execution_claim(revision)
        ),
        "revisions": [revision.to_dict() for revision in revisions],
    }


__all__ = [
    "APPROVAL_REQUIRED",
    "BLOCKED_WRITE_DISABLED",
    "FOLDER_REVISION_PROPOSAL_ONLY",
    "REVISION_PROPOSED",
    "OwnerFolderRevision",
    "evaluate_owner_folder_revision_set",
    "folder_revision_contains_execution_claim",
    "revise_owner_folder_proposal",
]
