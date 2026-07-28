"""Authority-bound approval and application of one frozen tournament proposal."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from devnous.gastos.models import Empleado, Tournament, TournamentOperationsLink

from .analyst_case import CASE_STATUS_CLOSED, CASE_STATUS_REVIEWED, AnalystCase
from .analyst_case_models import AnalystCaseRecord
from .analyst_case_store import AnalystCaseStore, AnalystCaseStoreError
from .tournament_application_contract import (
    APPLIED,
    APPROVED,
    TournamentApplicationContract,
    TournamentApplicationContractError,
    apply_tournament_application,
    approve_tournament_application,
    start_tournament_application,
    verify_tournament_application_contract,
)
from .tournament_application_domain import (
    TournamentApplicationError as TournamentDomainApplicationError,
    TournamentApplicationProjection,
    create_local_tournament_from_projection,
    projection_from_tournament,
)
from .tournament_case_pointer import (
    TournamentCasePointerError,
    get_active_tournament_case_pointer,
    set_active_tournament_case_pointer,
)
from .tournament_draft_authority import (
    TournamentDraftAuthorityError,
    inspect_tournament_draft_authority,
)
from .tournament_draft_case import (
    CASE_ID_PATTERN,
    CASE_KIND,
    WORKBENCH_KEY,
    _reconstruct_workbench,
)
from .tournament_draft_workbench import FROZEN, verify_frozen_proposal


APPLICATION_KEY = "tournament_application"
AUTHORIZED_ROLES = frozenset({"admin", "super_admin", "superadmin"})


class TournamentApplicationCaseError(ValueError):
    """Base failure for authority-bound tournament application."""


class TournamentApplicationCaseNotFoundError(TournamentApplicationCaseError):
    """Raised when no explicit or conversation-local case can be resolved."""


class TournamentApplicationCaseForbiddenError(TournamentApplicationCaseError):
    """Raised when the active employee lacks application authority."""


class TournamentApplicationCaseConflictError(TournamentApplicationCaseError):
    """Raised when a version or receipt binding is stale."""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(prefix: str, receipt_hash: str) -> str:
    return f"{prefix}_{receipt_hash.removeprefix('sha256:')[:24]}"


def _expected_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TournamentApplicationCaseConflictError(
            "expected_case_version must be a positive integer"
        )
    return value


def _enforce_actor_separation(
    proposal_payload: Mapping[str, Any], *, employee_id: str
) -> None:
    if str(proposal_payload.get("owner_employee_id") or "") == employee_id:
        raise TournamentApplicationCaseForbiddenError(
            "Proposal owner cannot approve or apply the proposal"
        )


async def _resolve_case_id(
    session: AsyncSession,
    *,
    case_id: Optional[str],
    employee_id: str,
    conversation_id: str,
) -> str:
    try:
        pointer = await get_active_tournament_case_pointer(
            session,
            conversation_id=conversation_id,
            employee_id=employee_id,
        )
    except TournamentCasePointerError as exc:
        raise TournamentApplicationCaseForbiddenError(str(exc)) from exc
    resolved = str(case_id or "").strip()
    if not resolved and isinstance(pointer, Mapping):
        resolved = str(pointer.get("case_id") or "").strip()
    if not CASE_ID_PATTERN.fullmatch(resolved):
        raise TournamentApplicationCaseNotFoundError(
            "No active tournament proposal case"
        )
    return resolved


def _locked_case(sync_session: Any, case_id: str) -> Optional[AnalystCase]:
    record = (
        sync_session.query(AnalystCaseRecord)
        .options(selectinload(AnalystCaseRecord.versions))
        .filter(AnalystCaseRecord.case_id == case_id)
        .with_for_update()
        .one_or_none()
    )
    if record is None:
        return None
    return AnalystCaseStore(sync_session).get_case(case_id)


async def _case_and_actor(
    session: AsyncSession,
    *,
    case_id: str,
    employee_id: str,
) -> tuple[AnalystCase, Dict[str, Any]]:
    case = await session.run_sync(lambda sync: _locked_case(sync, case_id))
    if case is None:
        raise TournamentApplicationCaseNotFoundError(
            "Tournament proposal case was not found"
        )
    if str(case.analyst_intent.get("kind") or "") != CASE_KIND:
        raise TournamentApplicationCaseForbiddenError(
            "AnalystCase is not a tournament proposal"
        )
    try:
        actor_id = UUID(employee_id)
    except ValueError as exc:
        raise TournamentApplicationCaseForbiddenError(
            "Invalid active employee identity"
        ) from exc
    actor = (
        await session.execute(
            select(Empleado)
            .where(Empleado.id == actor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if actor is None or not bool(actor.activo):
        raise TournamentApplicationCaseForbiddenError(
            "Tournament authority requires an active employee"
        )
    role = str(actor.rol or "").strip().casefold()
    if role not in AUTHORIZED_ROLES:
        raise TournamentApplicationCaseForbiddenError(
            "Tournament approval and application require an active administrator"
        )
    return case, {
        "id": str(actor.id),
        "nombre": str(actor.nombre or "").strip(),
        "departamento": (
            str(actor.departamento).strip() if actor.departamento else None
        ),
        "rol": str(actor.rol or "").strip(),
        "activo": True,
    }


def _frozen_contract(case: AnalystCase) -> tuple[Mapping[str, Any], Any]:
    answer = case.versions[-1].answer_contract or {}
    workbench = _reconstruct_workbench(answer)
    if workbench.state != FROZEN or workbench.frozen_proposal is None:
        raise TournamentApplicationCaseConflictError(
            "Tournament proposal is not frozen"
        )
    return answer, workbench.frozen_proposal


def _application_contract(
    answer: Mapping[str, Any], proposal: Any
) -> TournamentApplicationContract:
    stored = answer.get(APPLICATION_KEY)
    try:
        if isinstance(stored, Mapping):
            contract = TournamentApplicationContract.from_mapping(stored)
        else:
            contract = start_tournament_application(proposal)
        verify_tournament_application_contract(contract)
    except TournamentApplicationContractError as exc:
        raise TournamentApplicationCaseConflictError(str(exc)) from exc
    if contract.frozen_proposal.to_dict() != proposal.to_dict():
        raise TournamentApplicationCaseConflictError(
            "Application lifecycle does not bind the frozen proposal"
        )
    return contract


async def _fresh_authority(
    session: AsyncSession, proposal_payload: Mapping[str, Any]
) -> None:
    draft = proposal_payload.get("draft")
    if not isinstance(draft, Mapping):
        raise TournamentApplicationCaseConflictError("Frozen proposal draft is invalid")
    try:
        owner_id = UUID(str(proposal_payload.get("owner_employee_id") or ""))
        source_id = UUID(str(draft.get("base_tournament_id") or ""))
        owner = (
            await session.execute(
                select(Empleado)
                .where(Empleado.id == owner_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        source = (
            await session.execute(
                select(Tournament)
                .where(Tournament.id == source_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if owner is None or not bool(owner.activo):
            raise TournamentApplicationCaseConflictError(
                "Tournament proposal owner is no longer active"
            )
        if source is None:
            raise TournamentApplicationCaseConflictError(
                "Tournament proposal source no longer exists"
            )
        await session.execute(
            select(TournamentOperationsLink)
            .where(TournamentOperationsLink.tournament_id == source_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await inspect_tournament_draft_authority(
            session,
            owner_employee_id=str(proposal_payload.get("owner_employee_id") or ""),
            expected_source_hash=str(
                proposal_payload.get("source_authority_hash") or ""
            ),
            source_tournament_id=str(draft.get("base_tournament_id") or ""),
        )
    except (TournamentDraftAuthorityError, ValueError) as exc:
        raise TournamentApplicationCaseConflictError(str(exc)) from exc


def _persist_transition(
    sync_session: Any,
    *,
    case: AnalystCase,
    expected_version: int,
    answer_contract: Dict[str, Any],
    actor_id: str,
    applied: bool,
) -> AnalystCase:
    return AnalystCaseStore(sync_session).update_case(
        case.case_id,
        status=CASE_STATUS_CLOSED if applied else CASE_STATUS_REVIEWED,
        current_answer=(
            "La propuesta aprobada fue aplicada como un único torneo local."
            if applied
            else "La propuesta fue aprobada sin escrituras operativas."
        ),
        next_questions=[],
        suggested_routes=[],
        caveats=[
            "La aprobación no crea calendario, equipos, jugadores ni comunicaciones."
        ],
        answer_contract=answer_contract,
        expected_version_number=expected_version,
        updated_by=actor_id,
        closed_by=actor_id if applied else None,
    )


def _proposal_summary(contract: TournamentApplicationContract) -> Dict[str, Any]:
    return {
        "status": contract.state,
        "proposal_hash": contract.frozen_proposal.proposal_hash,
    }


def _approval_summary(contract: TournamentApplicationContract) -> Dict[str, Any]:
    receipt = contract.approval_receipt
    if receipt is None:
        raise TournamentApplicationCaseConflictError("Approval receipt is missing")
    payload = receipt.payload
    actor = payload.get("approved_by") or {}
    return {
        "approval_id": _identifier("tournament_approval", receipt.receipt_hash),
        "approval_hash": receipt.receipt_hash,
        "approved_by_employee_id": str(actor.get("id") or ""),
        "approved_role": str(actor.get("rol") or ""),
        "proposal_hash": str(payload.get("proposal_hash") or ""),
    }


def _application_summary(
    contract: TournamentApplicationContract,
    *,
    replay: bool,
    verification: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    receipt = contract.application_receipt
    if receipt is None:
        raise TournamentApplicationCaseConflictError("Application receipt is missing")
    payload = receipt.payload
    record = (payload.get("write_set") or {}).get("record") or {}
    return {
        "application_id": _identifier("tournament_application", receipt.receipt_hash),
        "application_hash": receipt.receipt_hash,
        "status": APPLIED,
        "target": {
            "tournament_id": str(payload.get("target_tournament_id") or ""),
            "name": str(record.get("name") or ""),
        },
        "bindings": {
            "approval_hash": str(payload.get("approval_receipt_hash") or ""),
            "proposal_hash": str(payload.get("proposal_hash") or ""),
            "draft_hash": str(payload.get("draft_hash") or ""),
            "source_authority_hash": str(payload.get("source_hash") or ""),
        },
        "write_set": {
            "inserted": {"tournaments": 1},
            "updated": {},
            "deleted": {},
        },
        "idempotent_replay": replay,
        "operational_write_performed_this_call": not replay,
        "postcommit_verification": dict(verification or {"status": "not_checked"}),
    }


async def _verify_committed_target(
    session: AsyncSession,
    contract: TournamentApplicationContract,
) -> Dict[str, Any]:
    receipt = contract.application_receipt
    if receipt is None:
        return {"status": "unavailable", "reason": "application receipt missing"}
    payload = receipt.payload
    try:
        target_id = UUID(str(payload.get("target_tournament_id") or ""))
        expected = TournamentApplicationProjection.from_mapping(
            payload.get("effective_projection") or {}
        )
        persisted = (
            await session.execute(
                select(Tournament)
                .where(Tournament.id == target_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if persisted is None:
            return {"status": "missing", "target_tournament_id": str(target_id)}
        observed = projection_from_tournament(persisted)
        if observed != expected:
            return {
                "status": "drift_detected",
                "target_tournament_id": str(target_id),
                "expected": expected.to_dict(),
                "observed": observed.to_dict(),
            }
        return {
            "status": "verified",
            "target_tournament_id": str(target_id),
            "effective_projection": observed.to_dict(),
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": type(exc).__name__,
        }


async def approve_tournament_proposal(
    session: AsyncSession,
    *,
    expected_case_version: int,
    expected_proposal_hash: str,
    current_employee_id: str,
    current_conversation_id: str,
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Approve one frozen proposal without performing a domain write."""

    employee_id = str(current_employee_id or "").strip()
    conversation_id = str(current_conversation_id or "").strip()
    expected_case_version = _expected_version(expected_case_version)
    resolved = await _resolve_case_id(
        session,
        case_id=case_id,
        employee_id=employee_id,
        conversation_id=conversation_id,
    )
    case, actor = await _case_and_actor(
        session, case_id=resolved, employee_id=employee_id
    )
    answer, proposal = _frozen_contract(case)
    contract = _application_contract(answer, proposal)
    current_version = case.versions[-1].version_number
    normalized_proposal_hash = str(expected_proposal_hash or "").strip().casefold()
    if contract.state == APPROVED and contract.approval_receipt is not None:
        approval_payload = contract.approval_receipt.payload
        approved_version = int(approval_payload.get("approved_case_version") or 0)
        approved_actor = approval_payload.get("approved_by") or {}
        if (
            expected_case_version not in {approved_version - 1, approved_version}
            or normalized_proposal_hash != proposal.proposal_hash
            or str(approved_actor.get("id") or "") != employee_id
        ):
            raise TournamentApplicationCaseConflictError(
                "Tournament approval replay does not match the durable receipt"
            )
        return {
            "case_id": case.case_id,
            "case_version": current_version,
            "workbench_status": APPROVED,
            "proposal": _proposal_summary(contract),
            "approval": _approval_summary(contract),
            "allowed_next_actions": ["inspect", "apply"],
            "operational_writes": False,
        }
    if contract.state != "frozen":
        raise TournamentApplicationCaseConflictError(
            "Tournament proposal has already been decided"
        )
    if expected_case_version != current_version:
        raise TournamentApplicationCaseConflictError("Stale AnalystCase version")
    if normalized_proposal_hash != proposal.proposal_hash:
        raise TournamentApplicationCaseConflictError("Stale tournament proposal hash")
    payload = verify_frozen_proposal(proposal)
    _enforce_actor_separation(payload, employee_id=employee_id)
    await _fresh_authority(session, payload)
    try:
        approved = approve_tournament_application(
            contract,
            approved_by=actor,
            approved_case_version=current_version + 1,
            approved_at=_utc_iso(),
            expected_proposal_hash=proposal.proposal_hash,
            expected_draft_hash=str(payload.get("draft_hash") or ""),
            verified_source_hash=str(payload.get("source_authority_hash") or ""),
        )
        next_answer = {**dict(answer), APPLICATION_KEY: approved.to_dict()}
        stored = await session.run_sync(
            lambda sync: _persist_transition(
                sync,
                case=case,
                expected_version=current_version,
                answer_contract=next_answer,
                actor_id=employee_id,
                applied=False,
            )
        )
        await set_active_tournament_case_pointer(
            session,
            conversation_id=conversation_id,
            employee_id=employee_id,
            case_id=stored.case_id,
            case_version=stored.versions[-1].version_number,
            status=APPROVED,
        )
        await session.commit()
    except (
        AnalystCaseStoreError,
        TournamentApplicationContractError,
        TournamentCasePointerError,
    ) as exc:
        await session.rollback()
        raise TournamentApplicationCaseConflictError(str(exc)) from exc
    return {
        "case_id": stored.case_id,
        "case_version": stored.versions[-1].version_number,
        "workbench_status": APPROVED,
        "proposal": _proposal_summary(approved),
        "approval": _approval_summary(approved),
        "allowed_next_actions": ["inspect", "apply"],
        "operational_writes": False,
    }


async def review_tournament_proposal(
    session: AsyncSession,
    *,
    current_employee_id: str,
    current_conversation_id: str,
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the exact authority artifact an independent admin may decide."""

    employee_id = str(current_employee_id or "").strip()
    conversation_id = str(current_conversation_id or "").strip()
    resolved = await _resolve_case_id(
        session,
        case_id=case_id,
        employee_id=employee_id,
        conversation_id=conversation_id,
    )
    case, _actor = await _case_and_actor(
        session, case_id=resolved, employee_id=employee_id
    )
    answer, proposal = _frozen_contract(case)
    contract = _application_contract(answer, proposal)
    payload = verify_frozen_proposal(proposal)
    await _fresh_authority(session, payload)
    owner_id = str(payload.get("owner_employee_id") or "")
    approval = (
        _approval_summary(contract) if contract.approval_receipt is not None else None
    )
    await set_active_tournament_case_pointer(
        session,
        conversation_id=conversation_id,
        employee_id=employee_id,
        case_id=case.case_id,
        case_version=case.versions[-1].version_number,
        status=contract.state,
    )
    return {
        "case_id": case.case_id,
        "case_version": case.versions[-1].version_number,
        "workbench_status": contract.state,
        "proposal": {
            "proposal_hash": proposal.proposal_hash,
            "draft_hash": str(payload.get("draft_hash") or ""),
            "source_authority_hash": str(payload.get("source_authority_hash") or ""),
            "owner_employee_id": owner_id,
            "target": dict(payload.get("draft") or {}),
            "validation": dict(payload.get("validation") or {}),
            "business_diff": dict(payload.get("business_diff") or {}),
        },
        "approval": approval,
        "decision": {
            "current_employee_is_owner": employee_id == owner_id,
            "can_approve": contract.state == "frozen" and employee_id != owner_id,
            "can_apply": contract.state == APPROVED and employee_id != owner_id,
        },
        "write_boundary": {
            "on_apply": {"inserted": {"tournaments": 1}},
            "no_claims": [
                "calendar",
                "communications",
                "media",
                "operations_link",
                "players",
                "registrations",
                "teams",
            ],
        },
        "operational_writes": False,
    }


async def _applied_replay(
    session: AsyncSession,
    *,
    case: AnalystCase,
    contract: TournamentApplicationContract,
    expected_case_version: int,
    expected_proposal_hash: str,
    expected_approval_hash: str,
    current_employee_id: str,
) -> Dict[str, Any]:
    receipt = contract.application_receipt
    approval = contract.approval_receipt
    if receipt is None or approval is None:
        raise TournamentApplicationCaseConflictError("Applied receipt is incomplete")
    owner_id = str(contract.frozen_proposal.payload.get("owner_employee_id") or "")
    if current_employee_id == owner_id:
        raise TournamentApplicationCaseForbiddenError(
            "Proposal owner cannot apply the proposal"
        )
    applied_version = int(receipt.payload.get("applied_case_version") or 0)
    if expected_case_version not in {applied_version - 1, applied_version}:
        raise TournamentApplicationCaseConflictError("Stale AnalystCase version")
    if expected_proposal_hash != contract.frozen_proposal.proposal_hash:
        raise TournamentApplicationCaseConflictError("Stale tournament proposal hash")
    if expected_approval_hash != approval.receipt_hash:
        raise TournamentApplicationCaseConflictError("Stale approval receipt hash")
    verification = await _verify_committed_target(session, contract)
    return {
        "case_id": case.case_id,
        "case_version": case.versions[-1].version_number,
        "workbench_status": APPLIED,
        "proposal": _proposal_summary(contract),
        "approval": _approval_summary(contract),
        "application": _application_summary(
            contract,
            replay=True,
            verification=verification,
        ),
        "allowed_next_actions": ["inspect"],
        "operational_writes": True,
    }


async def apply_tournament_proposal(
    session: AsyncSession,
    *,
    expected_case_version: int,
    expected_proposal_hash: str,
    expected_approval_hash: str,
    current_employee_id: str,
    current_conversation_id: str,
    case_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically create the one approved local Tournament and durable receipt."""

    employee_id = str(current_employee_id or "").strip()
    conversation_id = str(current_conversation_id or "").strip()
    expected_case_version = _expected_version(expected_case_version)
    resolved = await _resolve_case_id(
        session,
        case_id=case_id,
        employee_id=employee_id,
        conversation_id=conversation_id,
    )
    case, actor = await _case_and_actor(
        session, case_id=resolved, employee_id=employee_id
    )
    answer, proposal = _frozen_contract(case)
    contract = _application_contract(answer, proposal)
    if contract.state == APPLIED:
        return await _applied_replay(
            session,
            case=case,
            contract=contract,
            expected_case_version=expected_case_version,
            expected_proposal_hash=str(expected_proposal_hash or "").strip().casefold(),
            expected_approval_hash=str(expected_approval_hash or "").strip().casefold(),
            current_employee_id=employee_id,
        )
    if contract.state != APPROVED or contract.approval_receipt is None:
        raise TournamentApplicationCaseConflictError(
            "Tournament proposal is not approved"
        )
    current_version = case.versions[-1].version_number
    if expected_case_version != current_version:
        raise TournamentApplicationCaseConflictError("Stale AnalystCase version")
    if str(expected_proposal_hash or "").strip().casefold() != proposal.proposal_hash:
        raise TournamentApplicationCaseConflictError("Stale tournament proposal hash")
    if (
        str(expected_approval_hash or "").strip().casefold()
        != contract.approval_receipt.receipt_hash
    ):
        raise TournamentApplicationCaseConflictError("Stale approval receipt hash")
    payload = verify_frozen_proposal(proposal)
    _enforce_actor_separation(payload, employee_id=employee_id)
    await _fresh_authority(session, payload)
    draft = payload.get("draft")
    if not isinstance(draft, Mapping):
        raise TournamentApplicationCaseConflictError("Frozen draft is invalid")
    projection = {
        key: draft.get(key)
        for key in (
            "name",
            "description",
            "active",
            "display_order",
            "accounting_account",
            "stages",
            "categories",
            "visibility_areas",
        )
    }
    try:
        write_result = await create_local_tournament_from_projection(
            session, projection=projection
        )
        operations_link = (
            await session.execute(
                select(TournamentOperationsLink.id).where(
                    TournamentOperationsLink.tournament_id == write_result.tournament_id
                )
            )
        ).scalar_one_or_none()
        if operations_link is not None:
            raise TournamentDomainApplicationError(
                "Application transaction unexpectedly created an operations link"
            )
        applied = apply_tournament_application(
            contract,
            applied_by=actor,
            applied_case_version=current_version + 1,
            applied_at=_utc_iso(),
            target_tournament_id=str(write_result.tournament_id),
            expected_approval_receipt_hash=contract.approval_receipt.receipt_hash,
            expected_proposal_hash=proposal.proposal_hash,
            expected_draft_hash=str(payload.get("draft_hash") or ""),
            verified_source_hash=str(payload.get("source_authority_hash") or ""),
            effective_projection=write_result.projection.to_dict(),
        )
        next_answer = {**dict(answer), APPLICATION_KEY: applied.to_dict()}
        stored = await session.run_sync(
            lambda sync: _persist_transition(
                sync,
                case=case,
                expected_version=current_version,
                answer_contract=next_answer,
                actor_id=employee_id,
                applied=True,
            )
        )
        await set_active_tournament_case_pointer(
            session,
            conversation_id=conversation_id,
            employee_id=employee_id,
            case_id=stored.case_id,
            case_version=stored.versions[-1].version_number,
            status=APPLIED,
        )
        await session.commit()
    except (
        AnalystCaseStoreError,
        TournamentApplicationContractError,
        TournamentDomainApplicationError,
        TournamentCasePointerError,
    ) as exc:
        await session.rollback()
        raise TournamentApplicationCaseConflictError(str(exc)) from exc
    verification = await _verify_committed_target(session, applied)
    return {
        "case_id": stored.case_id,
        "case_version": stored.versions[-1].version_number,
        "workbench_status": APPLIED,
        "proposal": _proposal_summary(applied),
        "approval": _approval_summary(applied),
        "application": _application_summary(
            applied,
            replay=False,
            verification=verification,
        ),
        "allowed_next_actions": ["inspect"],
        "operational_writes": True,
    }


__all__ = [
    "TournamentApplicationCaseConflictError",
    "TournamentApplicationCaseError",
    "TournamentApplicationCaseForbiddenError",
    "TournamentApplicationCaseNotFoundError",
    "apply_tournament_proposal",
    "approve_tournament_proposal",
    "review_tournament_proposal",
]
