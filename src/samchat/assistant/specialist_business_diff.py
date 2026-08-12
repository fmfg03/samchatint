"""Business-facing previews for specialist-agent outputs.

These previews are intentionally read-only. They convert the deterministic
specialist workflow into something a user can inspect like a small business diff:
what SamChat knows, what it proposes to prepare, what evidence backs it, and
what still blocks execution.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .samchat_task_schema import SamchatVisibleTask
from .specialist_agents import SpecialistWorkflowResult


NOT_EXECUTED = "not_executed"
PREVIEW_ONLY = "preview_only"
APPROVAL_REQUIRED = "approval_required"
SUPPORTED = "supported"
PROPOSED = "proposed"


@dataclass(frozen=True)
class SpecialistBusinessChange:
    field: str
    proposed_value: Any
    source: str
    evidence_id: str | None = None
    status: str = PROPOSED
    reason: str = "verified_claim_available_for_preview"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpecialistBusinessDiffPreview:
    preview_id: str
    task_id: str
    preview_type: str
    title: str
    target: Mapping[str, Any]
    proposed_changes: Tuple[SpecialistBusinessChange, ...] = ()
    found_evidence: Tuple[str, ...] = ()
    missing_evidence: Tuple[str, ...] = ()
    steps: Tuple[str, ...] = ()
    checks: Tuple[str, ...] = ()
    blocked_reason: str = APPROVAL_REQUIRED
    approval_required: bool = True
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = PREVIEW_ONLY

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["target"] = dict(self.target)
        payload["proposed_changes"] = [
            change.to_dict() for change in self.proposed_changes
        ]
        payload["found_evidence"] = list(self.found_evidence)
        payload["missing_evidence"] = list(self.missing_evidence)
        payload["steps"] = list(self.steps)
        payload["checks"] = list(self.checks)
        return payload


def _preview_id(task: SamchatVisibleTask, preview_type: str) -> str:
    digest = hashlib.sha256(
        f"{task.task_id}|{preview_type}|{task.case_type}".encode("utf-8")
    ).hexdigest()[:16]
    return f"sbdp_{digest}"


def _claim_changes(
    workflow: SpecialistWorkflowResult,
) -> Tuple[SpecialistBusinessChange, ...]:
    changes: List[SpecialistBusinessChange] = []
    for item in workflow.verification.content.get("verified_claims") or []:
        if item.get("status") != SUPPORTED:
            continue
        claim = item.get("claim") or {}
        fact_key = str(claim.get("fact_key") or "")
        if not fact_key or fact_key.endswith("_evidence_id"):
            continue
        changes.append(
            SpecialistBusinessChange(
                field=fact_key,
                proposed_value=claim.get("value"),
                source=str(claim.get("case_id") or "verified_case"),
                evidence_id=claim.get("evidence_id"),
                status=SUPPORTED,
            )
        )
    return tuple(changes)


def _found_evidence(workflow: SpecialistWorkflowResult) -> Tuple[str, ...]:
    evidence_ids = []
    for item in workflow.verification.content.get("verified_claims") or []:
        if item.get("status") != SUPPORTED:
            continue
        claim = item.get("claim") or {}
        evidence_id = claim.get("evidence_id")
        if evidence_id:
            evidence_ids.append(str(evidence_id))
    return tuple(sorted(set(evidence_ids)))


def _missing_evidence(workflow: SpecialistWorkflowResult) -> Tuple[str, ...]:
    missing = []
    for item in workflow.knowledge.content.get("missing_evidence") or []:
        missing.append(f"{item.get('case_id')}:{item.get('fact_key')}")
    for item in workflow.verification.unsupported_claims:
        missing.append(str(item))
    return tuple(sorted(set(missing)))


def _target(task: SamchatVisibleTask, domain_summary: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "case_type": task.case_type,
        "agent_type": task.agent_type,
        "capability": domain_summary.get("capability"),
        "summary_label": domain_summary.get("summary_label"),
    }


def create_specialist_business_diff_preview(
    *,
    task: SamchatVisibleTask,
    workflow: SpecialistWorkflowResult,
) -> SpecialistBusinessDiffPreview:
    finance_content = workflow.finance.content
    domain_summary = finance_content.get("domain_summary") or {}
    action_preview = finance_content.get("action_preview") or {}
    preview_type = str(
        action_preview.get("preview_type")
        or finance_content.get("finance_capability")
        or "specialist_preview"
    )
    return SpecialistBusinessDiffPreview(
        preview_id=_preview_id(task, preview_type),
        task_id=task.task_id,
        preview_type=preview_type,
        title=str(domain_summary.get("summary_label") or task.title),
        target=_target(task, domain_summary),
        proposed_changes=_claim_changes(workflow),
        found_evidence=_found_evidence(workflow),
        missing_evidence=_missing_evidence(workflow),
        steps=tuple(str(item) for item in (action_preview.get("steps") or ())),
        checks=tuple(str(item) for item in (action_preview.get("checks") or ())),
        side_effects_detected=workflow.side_effects_detected,
    )


def preview_contains_execution_claim(
    preview: SpecialistBusinessDiffPreview | Mapping[str, Any]
) -> bool:
    payload = preview.to_dict() if hasattr(preview, "to_dict") else dict(preview)
    if payload.get("writes_attempted"):
        return True
    if payload.get("side_effects_detected"):
        return True
    if payload.get("execution_status") != NOT_EXECUTED:
        return True
    if payload.get("approval_required") is not True:
        return True
    return False


def summarize_specialist_business_previews(
    previews: Iterable[SpecialistBusinessDiffPreview],
) -> Dict[str, Any]:
    items = tuple(previews)
    return {
        "total": len(items),
        "preview_types": sorted({item.preview_type for item in items}),
        "writes_attempted": sum(item.writes_attempted for item in items),
        "side_effects_detected": sum(item.side_effects_detected for item in items),
        "execution_claims_detected": sum(
            1 for item in items if preview_contains_execution_claim(item)
        ),
        "missing_evidence_count": sum(len(item.missing_evidence) for item in items),
    }
