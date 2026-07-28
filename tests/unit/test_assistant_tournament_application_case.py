from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest

from devnous.gastos.models import Tournament
from samchat.assistant.analyst_case import (
    CASE_STATUS_ANALYZED,
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
    CASE_WRITE_POLICY,
    AnalystCase,
    AnalystCaseVersion,
)
from samchat.assistant.tournament_application_case import (
    APPLICATION_KEY,
    TournamentApplicationCaseConflictError,
    TournamentApplicationCaseForbiddenError,
    apply_tournament_proposal,
    approve_tournament_proposal,
    review_tournament_proposal,
)
from samchat.assistant.tournament_application_contract import (
    apply_tournament_application,
    approve_tournament_application,
    start_tournament_application,
)
from samchat.assistant.tournament_application_domain import (
    LocalTournamentApplicationResult,
    TournamentApplicationDuplicateNameError,
    TournamentApplicationProjection,
)
from samchat.assistant.tournament_draft_case import WORKBENCH_KEY
from samchat.assistant.tournament_draft_workbench import (
    TournamentDraftWorkbench,
    freeze_tournament_proposal,
)
from samchat.assistant.tournament_goal_shadow import (
    TournamentSnapshot,
    build_tournament_goal_shadow,
)


CASE_ID = "analyst_case_" + "5" * 32
OWNER_ID = "00000000-0000-0000-0000-000000000053"
ADMIN_ID = "00000000-0000-0000-0000-000000000054"
CONVERSATION_ID = "10000000-0000-0000-0000-000000000054"
TARGET_ID = UUID("20000000-0000-0000-0000-000000000054")


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Session:
    def __init__(self, execute_values: list[Any] | None = None) -> None:
        self.execute_values = list(execute_values or [])
        self.commits = 0
        self.rollbacks = 0
        self.statements = []

    async def run_sync(self, operation):
        return operation(object())

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.execute_values.pop(0))


def _frozen() -> tuple[dict[str, Any], Any]:
    source = TournamentSnapshot.from_mapping(
        {
            "id": "00000000-0000-0000-0000-000000000052",
            "name": "Torneo 2026",
            "description": "Base",
            "active": True,
            "display_order": 2,
            "stages": ["Estatal"],
            "categories": ["2012"],
            "visibility_areas": ["Operaciones"],
            "source_authority_hash": "sha256:" + "a" * 64,
        }
    )
    shadow = build_tournament_goal_shadow(
        source,
        requested_name="Torneo 2027",
        goal="Crear el torneo siguiente",
    )
    workbench = freeze_tournament_proposal(
        TournamentDraftWorkbench(shadow=shadow),
        case_id=CASE_ID,
        draft_case_version=1,
        verified_owner={
            "id": OWNER_ID,
            "nombre": "Owner",
            "rol": "admin",
            "activo": True,
        },
        verified_source_hash=source.snapshot_hash,
    )
    return {
        **shadow.to_dict(),
        WORKBENCH_KEY: workbench.to_dict(),
        "operational_writes": False,
    }, workbench.frozen_proposal


def _case(answer: dict[str, Any], *, version: int, status: str) -> AnalystCase:
    item = AnalystCaseVersion(
        version_id=f"analyst_case_version_{version}",
        version_number=version,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=OWNER_ID if version == 1 else ADMIN_ID,
        status=status,
        answer="state",
        evidence=[],
        next_questions=[],
        suggested_routes=[],
        caveats=[],
        answer_contract=answer,
    )
    return AnalystCase(
        case_id=CASE_ID,
        user_id=OWNER_ID,
        role="admin",
        question="Crear torneo",
        analyst_intent={"kind": "tournament_goal_shadow"},
        status=status,
        evidence=[],
        current_answer="state",
        next_questions=[],
        suggested_routes=[],
        caveats=[],
        versions=[item],
        writes_policy=dict(CASE_WRITE_POLICY),
    )


def _actor() -> dict[str, Any]:
    return {"id": ADMIN_ID, "nombre": "Admin", "rol": "admin", "activo": True}


def _projection(proposal: Any) -> TournamentApplicationProjection:
    payload = proposal.payload["draft"]
    return TournamentApplicationProjection.from_mapping(
        {
            key: payload[key]
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
    )


def _tournament(projection: TournamentApplicationProjection) -> Tournament:
    tournament = Tournament(
        id=TARGET_ID,
        name=projection.name,
        description=projection.description,
        active=projection.active,
        display_order=projection.display_order,
        cuenta_contable_relacionada=projection.accounting_account,
        etapas=list(projection.stages) or None,
        categorias=list(projection.categories) or None,
        form_visibility_areas=list(projection.visibility_areas) or None,
    )
    return tournament


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: AnalystCase,
    stored: AnalystCase,
    captured: dict[str, Any],
) -> None:
    async def resolve(*_args: Any, **_kwargs: Any) -> str:
        return CASE_ID

    async def case_actor(*_args: Any, **_kwargs: Any):
        return case, _actor()

    async def authority(*_args: Any, **_kwargs: Any) -> None:
        captured["authority_checks"] = captured.get("authority_checks", 0) + 1

    def persist(*_args: Any, **kwargs: Any) -> AnalystCase:
        captured["persist"] = kwargs
        return stored

    async def pointer(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["pointer"] = kwargs
        return kwargs

    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case._resolve_case_id", resolve
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case._case_and_actor", case_actor
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case._fresh_authority", authority
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case._persist_transition", persist
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case.set_active_tournament_case_pointer",
        pointer,
    )


@pytest.mark.asyncio
async def test_approval_commits_authority_receipt_without_domain_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    case = _case(answer, version=2, status=CASE_STATUS_ANALYZED)
    stored = _case(answer, version=3, status=CASE_STATUS_REVIEWED)
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=stored, captured=captured)
    session = _Session()

    result = await approve_tournament_proposal(
        session,
        case_id=CASE_ID,
        expected_case_version=2,
        expected_proposal_hash=proposal.proposal_hash,
        current_employee_id=ADMIN_ID,
        current_conversation_id=CONVERSATION_ID,
    )

    assert session.commits == 1 and session.rollbacks == 0
    assert captured["authority_checks"] == 1
    assert captured["persist"]["applied"] is False
    assert (
        captured["persist"]["answer_contract"][APPLICATION_KEY]["state"] == "approved"
    )
    assert result["operational_writes"] is False
    assert result["case_version"] == 3


@pytest.mark.asyncio
async def test_independent_admin_review_exposes_exact_decision_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    case = _case(answer, version=2, status=CASE_STATUS_ANALYZED)
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=case, captured=captured)

    review = await review_tournament_proposal(
        _Session(),
        case_id=CASE_ID,
        current_employee_id=ADMIN_ID,
        current_conversation_id=CONVERSATION_ID,
    )

    assert review["case_version"] == 2
    assert review["proposal"]["proposal_hash"] == proposal.proposal_hash
    assert review["proposal"]["target"]["name"] == "Torneo 2027"
    assert review["decision"] == {
        "current_employee_is_owner": False,
        "can_approve": True,
        "can_apply": False,
    }
    assert review["write_boundary"]["on_apply"] == {"inserted": {"tournaments": 1}}
    assert captured["pointer"]["status"] == "frozen"
    assert review["operational_writes"] is False


@pytest.mark.asyncio
async def test_approval_exact_retry_returns_durable_receipt_without_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    contract = approve_tournament_application(
        start_tournament_application(proposal),
        approved_by=_actor(),
        approved_case_version=3,
        approved_at=datetime.now(timezone.utc).isoformat(),
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    approved_answer = {**answer, APPLICATION_KEY: contract.to_dict()}
    case = _case(approved_answer, version=3, status=CASE_STATUS_REVIEWED)
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=case, captured=captured)
    session = _Session()

    result = await approve_tournament_proposal(
        session,
        case_id=CASE_ID,
        expected_case_version=2,
        expected_proposal_hash=proposal.proposal_hash,
        current_employee_id=ADMIN_ID,
        current_conversation_id=CONVERSATION_ID,
    )

    assert session.commits == 0 and "persist" not in captured
    assert result["approval"]["approval_hash"] == contract.approval_receipt.receipt_hash


@pytest.mark.asyncio
async def test_apply_commits_one_tournament_and_verifies_no_operations_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    approved = approve_tournament_application(
        start_tournament_application(proposal),
        approved_by=_actor(),
        approved_case_version=3,
        approved_at=datetime.now(timezone.utc).isoformat(),
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    approved_answer = {**answer, APPLICATION_KEY: approved.to_dict()}
    case = _case(approved_answer, version=3, status=CASE_STATUS_REVIEWED)
    stored = _case(approved_answer, version=4, status=CASE_STATUS_CLOSED)
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=stored, captured=captured)
    projection = _projection(proposal)

    async def writer(*_args: Any, **_kwargs: Any):
        captured["writes"] = captured.get("writes", 0) + 1
        return LocalTournamentApplicationResult(TARGET_ID, projection)

    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case.create_local_tournament_from_projection",
        writer,
    )
    session = _Session([None, _tournament(projection)])

    result = await apply_tournament_proposal(
        session,
        case_id=CASE_ID,
        expected_case_version=3,
        expected_proposal_hash=proposal.proposal_hash,
        expected_approval_hash=approved.approval_receipt.receipt_hash,
        current_employee_id=ADMIN_ID,
        current_conversation_id=CONVERSATION_ID,
    )

    assert captured["writes"] == 1
    assert captured["persist"]["applied"] is True
    assert session.commits == 1 and session.rollbacks == 0
    assert len(session.statements) == 2
    assert result["application"]["write_set"] == {
        "inserted": {"tournaments": 1},
        "updated": {},
        "deleted": {},
    }
    assert result["application"]["operational_write_performed_this_call"] is True


@pytest.mark.asyncio
async def test_apply_domain_failure_rolls_back_case_and_tournament(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    approved = approve_tournament_application(
        start_tournament_application(proposal),
        approved_by=_actor(),
        approved_case_version=3,
        approved_at=datetime.now(timezone.utc).isoformat(),
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    case = _case(
        {**answer, APPLICATION_KEY: approved.to_dict()},
        version=3,
        status=CASE_STATUS_REVIEWED,
    )
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=case, captured=captured)

    async def writer(*_args: Any, **_kwargs: Any):
        raise TournamentApplicationDuplicateNameError("duplicate")

    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case.create_local_tournament_from_projection",
        writer,
    )
    session = _Session()

    with pytest.raises(TournamentApplicationCaseConflictError, match="duplicate"):
        await apply_tournament_proposal(
            session,
            case_id=CASE_ID,
            expected_case_version=3,
            expected_proposal_hash=proposal.proposal_hash,
            expected_approval_hash=approved.approval_receipt.receipt_hash,
            current_employee_id=ADMIN_ID,
            current_conversation_id=CONVERSATION_ID,
        )

    assert session.commits == 0 and session.rollbacks == 1
    assert "persist" not in captured


@pytest.mark.asyncio
async def test_owner_is_rejected_before_application_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    approved = approve_tournament_application(
        start_tournament_application(proposal),
        approved_by=_actor(),
        approved_case_version=3,
        approved_at=datetime.now(timezone.utc).isoformat(),
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    case = _case(
        {**answer, APPLICATION_KEY: approved.to_dict()},
        version=3,
        status=CASE_STATUS_REVIEWED,
    )
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=case, captured=captured)

    async def owner_case_actor(*_args: Any, **_kwargs: Any):
        return case, {
            "id": OWNER_ID,
            "nombre": "Owner",
            "rol": "admin",
            "activo": True,
        }

    async def writer(*_args: Any, **_kwargs: Any):
        captured["writes"] = 1
        raise AssertionError("owner must fail before the domain writer")

    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case._case_and_actor",
        owner_case_actor,
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case.create_local_tournament_from_projection",
        writer,
    )

    with pytest.raises(TournamentApplicationCaseForbiddenError, match="owner"):
        await apply_tournament_proposal(
            _Session(),
            case_id=CASE_ID,
            expected_case_version=3,
            expected_proposal_hash=proposal.proposal_hash,
            expected_approval_hash=approved.approval_receipt.receipt_hash,
            current_employee_id=OWNER_ID,
            current_conversation_id=CONVERSATION_ID,
        )

    assert "writes" not in captured


@pytest.mark.asyncio
async def test_boolean_case_version_fails_closed_before_case_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def resolve(*_args: Any, **_kwargs: Any) -> str:
        captured["resolved"] = True
        return CASE_ID

    monkeypatch.setattr(
        "samchat.assistant.tournament_application_case._resolve_case_id", resolve
    )
    with pytest.raises(
        TournamentApplicationCaseConflictError, match="positive integer"
    ):
        await approve_tournament_proposal(
            _Session(),
            case_id=CASE_ID,
            expected_case_version=True,
            expected_proposal_hash="sha256:" + "a" * 64,
            current_employee_id=ADMIN_ID,
            current_conversation_id=CONVERSATION_ID,
        )
    assert "resolved" not in captured


@pytest.mark.asyncio
async def test_apply_exact_retry_returns_same_target_without_second_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    approved = approve_tournament_application(
        start_tournament_application(proposal),
        approved_by=_actor(),
        approved_case_version=3,
        approved_at=datetime.now(timezone.utc).isoformat(),
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    applied = apply_tournament_application(
        approved,
        applied_by=_actor(),
        applied_case_version=4,
        applied_at=datetime.now(timezone.utc).isoformat(),
        target_tournament_id=str(TARGET_ID),
        expected_approval_receipt_hash=approved.approval_receipt.receipt_hash,
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    case = _case(
        {**answer, APPLICATION_KEY: applied.to_dict()},
        version=4,
        status=CASE_STATUS_CLOSED,
    )
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=case, captured=captured)
    session = _Session([_tournament(_projection(proposal))])

    result = await apply_tournament_proposal(
        session,
        case_id=CASE_ID,
        expected_case_version=3,
        expected_proposal_hash=proposal.proposal_hash,
        expected_approval_hash=approved.approval_receipt.receipt_hash,
        current_employee_id=ADMIN_ID,
        current_conversation_id=CONVERSATION_ID,
    )

    assert session.commits == 0 and "persist" not in captured
    assert result["application"]["target"]["tournament_id"] == str(TARGET_ID)
    assert result["application"]["idempotent_replay"] is True
    assert result["application"]["operational_write_performed_this_call"] is False


@pytest.mark.asyncio
async def test_apply_replay_surfaces_target_drift_instead_of_claiming_exact_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer, proposal = _frozen()
    approved = approve_tournament_application(
        start_tournament_application(proposal),
        approved_by=_actor(),
        approved_case_version=3,
        approved_at=datetime.now(timezone.utc).isoformat(),
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
    )
    projection = _projection(proposal)
    applied = apply_tournament_application(
        approved,
        applied_by=_actor(),
        applied_case_version=4,
        applied_at=datetime.now(timezone.utc).isoformat(),
        target_tournament_id=str(TARGET_ID),
        expected_approval_receipt_hash=approved.approval_receipt.receipt_hash,
        expected_proposal_hash=proposal.proposal_hash,
        expected_draft_hash=proposal.payload["draft_hash"],
        verified_source_hash=proposal.payload["source_authority_hash"],
        effective_projection=projection.to_dict(),
    )
    case = _case(
        {**answer, APPLICATION_KEY: applied.to_dict()},
        version=4,
        status=CASE_STATUS_CLOSED,
    )
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, case=case, stored=case, captured=captured)
    changed = _tournament(projection)
    changed.description = "Changed after application"

    result = await apply_tournament_proposal(
        _Session([changed]),
        case_id=CASE_ID,
        expected_case_version=3,
        expected_proposal_hash=proposal.proposal_hash,
        expected_approval_hash=approved.approval_receipt.receipt_hash,
        current_employee_id=ADMIN_ID,
        current_conversation_id=CONVERSATION_ID,
    )

    verification = result["application"]["postcommit_verification"]
    assert verification["status"] == "drift_detected"
    assert verification["expected"]["description"] == "Base"
    assert verification["observed"]["description"] == "Changed after application"
