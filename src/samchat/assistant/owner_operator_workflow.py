"""Read-only owner operator workflow orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Mapping, Optional

from .business_diff_preview import (
    NOT_EXECUTED,
    create_business_diff_preview,
)
from .owner_folder_builder import (
    OwnerFolderProposal,
    build_owner_folder_proposal,
)
from .owner_folder_revision import (
    BLOCKED_WRITE_DISABLED,
    OwnerFolderRevision,
    revise_owner_folder_proposal,
)
from .owner_needs_eval import (
    OwnerNeedsAssessment,
    OwnerNeedsPrompt,
    assess_owner_needs_prompt,
)
from .owner_response_pack import (
    OwnerOperatorResponsePack,
    build_response_pack_from_proposal,
    build_response_pack_from_revision,
    response_pack_contains_execution_claim,
)


OWNER_OPERATOR_WORKFLOW_ONLY = "owner_operator_workflow_only"


@dataclass(frozen=True)
class OwnerOperatorWorkflowResult:
    workflow_id: str
    prompt_id: str
    assessment: Dict[str, object]
    preview: Dict[str, object]
    folder_proposal: Dict[str, object]
    revision: Optional[Dict[str, object]]
    response_pack: Dict[str, object]
    trace: Dict[str, object] = field(default_factory=dict)
    safety_summary: Dict[str, object] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = OWNER_OPERATOR_WORKFLOW_ONLY

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _workflow_id(
    prompt: OwnerNeedsPrompt,
    proposal: OwnerFolderProposal,
    revision: Optional[OwnerFolderRevision],
    response_pack: OwnerOperatorResponsePack,
) -> str:
    revision_id = revision.revision_id if revision is not None else ""
    key = (
        f"{prompt.prompt_id}|{proposal.preview_id}|{proposal.folder_id}|"
        f"{revision_id}|{response_pack.response_id}"
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"oow_{digest}"


def _trace(
    assessment: OwnerNeedsAssessment,
    proposal: OwnerFolderProposal,
    revision: Optional[OwnerFolderRevision],
    response_pack: OwnerOperatorResponsePack,
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "assessment_status": assessment.status,
        "preview_id": proposal.preview_id,
        "folder_id": proposal.folder_id,
        "response_id": response_pack.response_id,
    }
    if revision is not None:
        payload["revision_id"] = revision.revision_id
        payload["revision_status"] = revision.revision_status
    return payload


def _safety_summary(
    proposal: OwnerFolderProposal,
    revision: Optional[OwnerFolderRevision],
    response_pack: OwnerOperatorResponsePack,
) -> Dict[str, object]:
    writes_attempted = (
        proposal.writes_attempted + response_pack.writes_attempted
    )
    side_effects = (
        proposal.side_effects_detected
        + response_pack.side_effects_detected
    )
    if revision is not None:
        writes_attempted += revision.writes_attempted
        side_effects += revision.side_effects_detected
    return {
        "approval_required": True,
        "writes_enabled": False,
        "write_handlers_invoked": 0,
        "side_effects_detected": side_effects,
        "runtime_general_enabled": False,
        "writes_attempted": writes_attempted,
    }


def run_owner_operator_workflow(
    prompt: OwnerNeedsPrompt,
    *,
    available_evidence: Optional[Mapping[str, object]] = None,
    requested_revision: Optional[str] = None,
) -> OwnerOperatorWorkflowResult:
    """Run the read-only owner operator workflow for one prompt."""

    assessment = assess_owner_needs_prompt(prompt)
    preview = create_business_diff_preview(
        assessment,
        available_evidence=available_evidence,
    )
    proposal = build_owner_folder_proposal(preview)

    revision = None
    if requested_revision:
        revision = revise_owner_folder_proposal(proposal, requested_revision)
        response_pack = build_response_pack_from_revision(revision)
    else:
        response_pack = build_response_pack_from_proposal(proposal)

    safety_summary = _safety_summary(proposal, revision, response_pack)
    return OwnerOperatorWorkflowResult(
        workflow_id=_workflow_id(prompt, proposal, revision, response_pack),
        prompt_id=prompt.prompt_id,
        assessment=assessment.to_dict(),
        preview=preview.to_dict(),
        folder_proposal=proposal.to_dict(),
        revision=revision.to_dict() if revision is not None else None,
        response_pack=response_pack.to_dict(),
        trace=_trace(assessment, proposal, revision, response_pack),
        safety_summary=safety_summary,
        execution_status=NOT_EXECUTED,
        writes_attempted=safety_summary["writes_attempted"],
        side_effects_detected=safety_summary["side_effects_detected"],
        audit_language=OWNER_OPERATOR_WORKFLOW_ONLY,
    )


def workflow_contains_execution_claim(
    result: OwnerOperatorWorkflowResult,
) -> bool:
    response_pack = OwnerOperatorResponsePack(**result.response_pack)
    if response_pack_contains_execution_claim(response_pack):
        return True
    payload = result.to_dict()
    payload.pop("assessment", None)
    payload.pop("preview", None)
    payload.pop("folder_proposal", None)
    payload.pop("revision", None)
    payload.pop("response_pack", None)
    text = str(payload).lower().replace(NOT_EXECUTED, "")
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


def evaluate_owner_operator_workflow_set(
    prompts: Iterable[OwnerNeedsPrompt],
    *,
    requested_revision: Optional[str] = None,
) -> Dict[str, object]:
    results = [
        run_owner_operator_workflow(
            prompt,
            requested_revision=requested_revision,
        )
        for prompt in prompts
    ]
    blocked_count = 0
    for result in results:
        revision = result.revision or {}
        if revision.get("revision_status") == BLOCKED_WRITE_DISABLED:
            blocked_count += 1
    return {
        "total": len(results),
        "blocked_write_disabled": blocked_count,
        "writes_attempted": sum(result.writes_attempted for result in results),
        "side_effects_detected": sum(
            result.side_effects_detected for result in results
        ),
        "execution_claims_detected": sum(
            1
            for result in results
            if workflow_contains_execution_claim(result)
        ),
        "results": [result.to_dict() for result in results],
    }


__all__ = [
    "OWNER_OPERATOR_WORKFLOW_ONLY",
    "OwnerOperatorWorkflowResult",
    "evaluate_owner_operator_workflow_set",
    "run_owner_operator_workflow",
    "workflow_contains_execution_claim",
]
