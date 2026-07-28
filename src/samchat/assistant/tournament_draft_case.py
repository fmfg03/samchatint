"""Persistent case integration for the inert tournament draft workbench."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from .analyst_case import (
    CASE_STATUS_ANALYZED,
    CASE_STATUS_CLOSED,
    CASE_STATUS_WAITING_CONTEXT,
    AnalystCase,
)
from .analyst_case_store import AnalystCaseStore, AnalystCaseStoreError
from .tournament_case_pointer import (
    TournamentCasePointerError,
    get_active_tournament_case_pointer,
    set_active_tournament_case_pointer,
)
from .tournament_draft_authority import (
    inspect_active_tournament_owner,
    inspect_tournament_draft_authority,
)
from .tournament_draft_workbench import (
    ABANDONED,
    DRAFTING,
    FROZEN,
    FrozenTournamentProposal,
    TournamentDraftPatch,
    TournamentDraftWorkbench,
    TournamentDraftWorkbenchError,
    TournamentReviewInputs,
    abandon_tournament_workbench,
    freeze_tournament_proposal,
    revise_tournament_draft,
)
from .tournament_goal_shadow import (
    BusinessDiffEntry,
    TournamentBusinessDiff,
    TournamentDraft,
    TournamentGoalPlan,
    TournamentGoalShadow,
    TournamentPlanStep,
    TournamentSnapshot,
    TournamentValidation,
    ValidationFinding,
)


CASE_ID_PATTERN = re.compile(r"^analyst_case_[0-9a-f]{32}$")
CASE_KIND = "tournament_goal_shadow"
WORKBENCH_KEY = "draft_workbench"
PUBLIC_KEYS = {
    "case_id",
    "case_version",
    "workbench_status",
    "plan",
    "source",
    "draft",
    "validation",
    "diff",
    "files",
    "next_questions",
    "proposal",
    "allowed_next_actions",
    "operational_writes",
}


class TournamentDraftCaseError(ValueError):
    """Base error for a case-local workbench operation."""


class TournamentDraftCaseNotFoundError(TournamentDraftCaseError):
    """Raised when neither an explicit nor active case can be resolved."""


class TournamentDraftCaseForbiddenError(TournamentDraftCaseError):
    """Raised when case or conversation ownership does not match."""


class TournamentDraftCaseConflictError(TournamentDraftCaseError):
    """Raised when optimistic version or draft bindings are stale."""


def _as_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TournamentDraftCaseError(f"Invalid {field_name} contract")
    return value


def _reconstruct_shadow(contract: Mapping[str, Any]) -> TournamentGoalShadow:
    source_payload = _as_mapping(contract.get("source"), field_name="source")
    draft_payload = _as_mapping(contract.get("draft"), field_name="draft")
    plan_payload = _as_mapping(contract.get("plan"), field_name="plan")
    validation_payload = _as_mapping(
        contract.get("validation"), field_name="validation"
    )
    diff_payload = _as_mapping(
        contract.get("business_diff"), field_name="business_diff"
    )
    source = TournamentSnapshot.from_mapping(source_payload)
    draft = TournamentDraft(
        base_tournament_id=str(draft_payload.get("base_tournament_id") or ""),
        base_snapshot_hash=str(draft_payload.get("base_snapshot_hash") or ""),
        name=str(draft_payload.get("name") or ""),
        description=draft_payload.get("description"),
        active=bool(draft_payload.get("active")),
        display_order=int(draft_payload.get("display_order") or 0),
        accounting_account=draft_payload.get("accounting_account"),
        stages=tuple(draft_payload.get("stages") or ()),
        categories=tuple(draft_payload.get("categories") or ()),
        visibility_areas=tuple(draft_payload.get("visibility_areas") or ()),
        execution_status=str(draft_payload.get("execution_status") or ""),
        operational_writes_allowed=bool(
            draft_payload.get("operational_writes_allowed")
        ),
        schema_version=str(draft_payload.get("schema_version") or ""),
    )
    plan = TournamentGoalPlan(
        goal=str(plan_payload.get("goal") or ""),
        base_tournament_id=str(plan_payload.get("base_tournament_id") or ""),
        steps=tuple(
            TournamentPlanStep(
                step_id=str(item.get("step_id") or ""),
                title=str(item.get("title") or ""),
                status=str(item.get("status") or ""),
            )
            for item in plan_payload.get("steps") or ()
            if isinstance(item, Mapping)
        ),
        execution_status=str(plan_payload.get("execution_status") or ""),
        operational_writes_allowed=bool(plan_payload.get("operational_writes_allowed")),
    )
    validation = TournamentValidation(
        findings=tuple(
            ValidationFinding(
                code=str(item.get("code") or ""),
                severity=str(item.get("severity") or ""),
                field=str(item.get("field") or ""),
                message=str(item.get("message") or ""),
            )
            for item in validation_payload.get("findings") or ()
            if isinstance(item, Mapping)
        )
    )
    business_diff = TournamentBusinessDiff(
        entries=tuple(
            BusinessDiffEntry(
                field=str(item.get("field") or ""),
                label=str(item.get("label") or ""),
                change_type=str(item.get("change_type") or ""),
                before=item.get("before"),
                after=item.get("after"),
            )
            for item in diff_payload.get("entries") or ()
            if isinstance(item, Mapping)
        ),
        base_snapshot_hash=str(diff_payload.get("base_snapshot_hash") or ""),
        draft_hash=str(diff_payload.get("draft_hash") or ""),
    )
    shadow = TournamentGoalShadow(
        plan=plan,
        source=source,
        draft=draft,
        validation=validation,
        business_diff=business_diff,
        missing_information=tuple(contract.get("missing_information") or ()),
        contract_version=str(contract.get("contract_version") or ""),
        execution_status=str(contract.get("execution_status") or ""),
        operational_writes_allowed=bool(contract.get("operational_writes_allowed")),
        blocked_capabilities=tuple(contract.get("blocked_capabilities") or ()),
    )
    if (
        source.to_dict() != dict(source_payload)
        or draft.to_dict() != dict(draft_payload)
        or draft.draft_hash != str(diff_payload.get("draft_hash") or "")
        or shadow.work_product_hash != str(contract.get("work_product_hash") or "")
    ):
        raise TournamentDraftCaseError("Tournament case contract is inconsistent")
    return shadow


def _reconstruct_workbench(contract: Mapping[str, Any]) -> TournamentDraftWorkbench:
    shadow = _reconstruct_shadow(contract)
    metadata = contract.get(WORKBENCH_KEY)
    if not isinstance(metadata, Mapping):
        return TournamentDraftWorkbench(shadow=shadow)
    frozen_payload = metadata.get("frozen_proposal")
    frozen = (
        FrozenTournamentProposal.from_mapping(frozen_payload)
        if isinstance(frozen_payload, Mapping)
        else None
    )
    return TournamentDraftWorkbench(
        shadow=shadow,
        review_inputs=TournamentReviewInputs.from_mapping(
            metadata.get("review_inputs")
            if isinstance(metadata.get("review_inputs"), Mapping)
            else {}
        ),
        state=str(metadata.get("state") or DRAFTING),
        frozen_proposal=frozen,
        abandoned_reason=metadata.get("abandoned_reason"),
    )


def _answer_contract(
    previous: Mapping[str, Any], workbench: TournamentDraftWorkbench
) -> Dict[str, Any]:
    return {
        **dict(previous),
        **workbench.shadow.to_dict(),
        WORKBENCH_KEY: workbench.to_dict(),
        "operational_writes": False,
    }


def _questions(workbench: TournamentDraftWorkbench) -> list[str]:
    questions = [
        item.message
        for item in workbench.shadow.validation.findings
        if item.severity == "error"
    ]
    questions.extend(
        f"Resolve el dato requerido: {item}"
        for item in workbench.shadow.missing_information
    )
    return list(dict.fromkeys(questions))


def _status(workbench: TournamentDraftWorkbench) -> str:
    if workbench.state == ABANDONED:
        return CASE_STATUS_CLOSED
    if workbench.shadow.validation.valid and not workbench.shadow.missing_information:
        return CASE_STATUS_ANALYZED
    return CASE_STATUS_WAITING_CONTEXT


def _case_version_diff(case: AnalystCase) -> Dict[str, Any]:
    if len(case.versions) < 2:
        return {"change_count": 0, "entries": []}
    before = (case.versions[-2].answer_contract or {}).get("draft") or {}
    after = (case.versions[-1].answer_contract or {}).get("draft") or {}
    fields = sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )
    return {
        "change_count": len(fields),
        "entries": [
            {"field": key, "before": before.get(key), "after": after.get(key)}
            for key in fields
        ],
    }


def _public_response(case: AnalystCase) -> Dict[str, Any]:
    latest = case.versions[-1]
    contract = latest.answer_contract or {}
    workbench = _reconstruct_workbench(contract)
    state = workbench.state
    public_state = "draft" if state == DRAFTING else state
    proposal_hash = (
        workbench.frozen_proposal.proposal_hash
        if workbench.frozen_proposal is not None
        else None
    )
    actions = {
        DRAFTING: ["inspect", "revise", "freeze", "cancel"],
        FROZEN: ["inspect", "cancel"],
        ABANDONED: [],
    }[state]
    response = {
        "case_id": case.case_id,
        "case_version": latest.version_number,
        "workbench_status": public_state,
        "plan": workbench.shadow.plan.to_dict(),
        "source": {
            "authority": contract.get("source_authority") or {},
            "bound_snapshot": workbench.shadow.source.to_dict(),
        },
        "draft": workbench.shadow.draft.to_dict(),
        "validation": workbench.shadow.validation.to_dict(),
        "diff": {
            "from_source": workbench.shadow.business_diff.to_dict(),
            "from_previous_version": _case_version_diff(case),
        },
        "files": list(contract.get("files") or []),
        "next_questions": list(case.next_questions),
        "proposal": {"status": public_state, "proposal_hash": proposal_hash},
        "allowed_next_actions": actions,
        "operational_writes": False,
    }
    if set(response) != PUBLIC_KEYS:
        raise AssertionError("Tournament workbench public contract drifted")
    return response


async def _resolve_case(
    session: AsyncSession,
    *,
    case_id: Optional[str],
    employee_id: str,
    conversation_id: str,
) -> AnalystCase:
    try:
        pointer = await get_active_tournament_case_pointer(
            session,
            conversation_id=conversation_id,
            employee_id=employee_id,
        )
    except TournamentCasePointerError as exc:
        raise TournamentDraftCaseForbiddenError(str(exc)) from exc
    resolved = str(case_id or "").strip()
    if not resolved:
        if isinstance(pointer, Mapping):
            resolved = str(pointer.get("case_id") or "").strip()
    if not CASE_ID_PATTERN.fullmatch(resolved):
        raise TournamentDraftCaseNotFoundError("No active tournament draft case")
    case = await session.run_sync(
        lambda sync_session: AnalystCaseStore(sync_session).get_case(resolved)
    )
    if case is None:
        raise TournamentDraftCaseNotFoundError("Tournament draft case was not found")
    if case.user_id != employee_id:
        raise TournamentDraftCaseForbiddenError(
            "Tournament draft case belongs to another employee"
        )
    if str(case.analyst_intent.get("kind") or "") != CASE_KIND:
        raise TournamentDraftCaseForbiddenError("AnalystCase is not a tournament draft")
    return case


def _persist(
    sync_session: Any,
    *,
    case: AnalystCase,
    expected_version: int,
    workbench: TournamentDraftWorkbench,
    employee_id: str,
) -> AnalystCase:
    closed = workbench.state == ABANDONED
    return AnalystCaseStore(sync_session).update_case(
        case.case_id,
        status=_status(workbench),
        current_answer=(
            "El borrador fue abandonado; se conservó toda la evidencia."
            if closed
            else "El borrador de torneo fue actualizado sin escrituras operativas."
        ),
        next_questions=_questions(workbench),
        suggested_routes=[],
        caveats=["Propuesta inerte; no concede autoridad operativa."],
        answer_contract=_answer_contract(
            case.versions[-1].answer_contract or {}, workbench
        ),
        expected_version_number=expected_version,
        updated_by=employee_id,
        closed_by=employee_id if closed else None,
    )


async def run_tournament_draft_workbench(
    session: AsyncSession,
    *,
    action: str,
    current_role: Optional[str],
    current_employee_id: Optional[str],
    current_conversation_id: Optional[str],
    case_id: Optional[str] = None,
    expected_case_version: Optional[int] = None,
    expected_draft_hash: Optional[str] = None,
    changes: Optional[Mapping[str, Any]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect or append one inert workbench version; never mutate a tournament."""

    employee_id = str(current_employee_id or "").strip()
    conversation_id = str(current_conversation_id or "").strip()
    if not employee_id or not str(current_role or "").strip() or not conversation_id:
        raise TournamentDraftCaseError("Trusted assistant identity is required")
    normalized_action = str(action or "").strip().casefold()
    if normalized_action not in {"inspect", "revise", "freeze", "cancel"}:
        raise TournamentDraftCaseError("Unsupported tournament workbench action")
    case = await _resolve_case(
        session,
        case_id=case_id,
        employee_id=employee_id,
        conversation_id=conversation_id,
    )
    workbench = _reconstruct_workbench(case.versions[-1].answer_contract or {})
    if normalized_action == "inspect":
        return _public_response(case)
    if case.status == CASE_STATUS_CLOSED or workbench.state == ABANDONED:
        raise TournamentDraftCaseConflictError("Tournament draft case is closed")
    if (
        isinstance(expected_case_version, bool)
        or not isinstance(expected_case_version, int)
        or expected_case_version != case.versions[-1].version_number
    ):
        raise TournamentDraftCaseConflictError("Stale AnalystCase version")
    try:
        if normalized_action == "revise":
            supplied = dict(changes or {})
            review_changes = supplied.pop("review_inputs", None)
            if not supplied and not review_changes:
                raise TournamentDraftCaseError("At least one revision is required")
            if review_changes is not None and not isinstance(review_changes, Mapping):
                raise TournamentDraftCaseError("review_inputs must be an object")
            await inspect_active_tournament_owner(session, employee_id)
            workbench = revise_tournament_draft(
                workbench,
                patch=TournamentDraftPatch.from_mapping(supplied),
                review_input_changes=review_changes,
            )
        elif normalized_action == "freeze":
            observed_hash = str(expected_draft_hash or "").strip().casefold()
            if observed_hash != workbench.shadow.draft.draft_hash:
                raise TournamentDraftCaseConflictError("Stale tournament draft hash")
            authority = await inspect_tournament_draft_authority(
                session,
                owner_employee_id=employee_id,
                expected_source_hash=workbench.shadow.source.snapshot_hash,
                source_tournament_id=workbench.shadow.source.tournament_id,
            )
            workbench = freeze_tournament_proposal(
                workbench,
                case_id=case.case_id,
                draft_case_version=expected_case_version,
                verified_owner=authority.owner.model_dump(mode="json"),
                verified_source_hash=authority.source.source_hash,
            )
        else:
            workbench = abandon_tournament_workbench(
                workbench,
                reason=reason or "Borrador descartado por el solicitante.",
            )
    except TournamentDraftWorkbenchError as exc:
        raise TournamentDraftCaseError(str(exc)) from exc
    try:
        stored = await session.run_sync(
            lambda sync_session: _persist(
                sync_session,
                case=case,
                expected_version=expected_case_version,
                workbench=workbench,
                employee_id=employee_id,
            )
        )
    except AnalystCaseStoreError as exc:
        if "Stale AnalystCase version" in str(exc):
            raise TournamentDraftCaseConflictError(str(exc)) from exc
        raise TournamentDraftCaseError(str(exc)) from exc
    try:
        await set_active_tournament_case_pointer(
            session,
            conversation_id=conversation_id,
            employee_id=employee_id,
            case_id=stored.case_id,
            case_version=stored.versions[-1].version_number,
            status=workbench.state,
        )
    except TournamentCasePointerError as exc:
        raise TournamentDraftCaseError(str(exc)) from exc
    return _public_response(stored)


__all__ = [
    "TournamentDraftCaseConflictError",
    "TournamentDraftCaseError",
    "TournamentDraftCaseForbiddenError",
    "TournamentDraftCaseNotFoundError",
    "run_tournament_draft_workbench",
]
