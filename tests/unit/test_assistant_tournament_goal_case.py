from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from devnous.copa_telmex.models import Base
from samchat.assistant.analyst_case_models import (
    AnalystCaseRecord,
    AnalystCaseVersionRecord,
)
from samchat.assistant.analyst_case_store import AnalystCaseStore
from samchat.assistant.tournament_goal_case import (
    TournamentGoalCaseError,
    TournamentGoalCaseForbiddenError,
    build_tournament_goal_shadow,
)
from samchat.assistant.tournament_goal_source import (
    LocalTournamentOperationsAggregate,
    LocalTournamentProject,
    TournamentSourceSnapshot,
)


class AsyncStoreAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def run_sync(self, operation: Callable[[Session], Any]) -> Any:
        return operation(self.session)

    def begin_nested(self) -> "AsyncNestedAdapter":
        return AsyncNestedAdapter()


class AsyncNestedAdapter:
    async def __aenter__(self) -> "AsyncNestedAdapter":
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AnalystCaseRecord.__table__,
            AnalystCaseVersionRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    current = factory()
    try:
        yield current
    finally:
        current.close()
        engine.dispose()


def source_snapshot() -> TournamentSourceSnapshot:
    tournament_id = uuid.UUID("00000000-0000-0000-0000-000000000052")
    return TournamentSourceSnapshot(
        project=LocalTournamentProject(
            id=tournament_id,
            name="Torneo 2026",
            description="Base local",
            active=True,
            display_order=2,
            etapas=["Estatal", "Nacional"],
            categorias=["2012", "2013"],
            form_visibility_departments=["operaciones"],
            updated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        ),
        observed_operations=LocalTournamentOperationsAggregate(),
        unavailable_components=["matches_and_schedule"],
        source_hash="sha256:" + ("a" * 64),
    )


@pytest.fixture()
def isolated_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inspect(*_args: Any, **_kwargs: Any) -> TournamentSourceSnapshot:
        return source_snapshot()

    async def no_duplicate(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def no_pointer(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "samchat.assistant.tournament_goal_case.inspect_tournament_source",
        fake_inspect,
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_goal_case._target_name_finding",
        no_duplicate,
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_goal_case.set_active_tournament_case_pointer",
        no_pointer,
    )


@pytest.mark.asyncio
async def test_case_is_persistent_resumable_and_idempotent(
    session: Session,
    isolated_source: None,
) -> None:
    adapter = AsyncStoreAdapter(session)
    arguments = {
        "session": adapter,
        "goal": "Crear torneo 2027 desde 2026",
        "source_tournament_id": "00000000-0000-0000-0000-000000000052",
        "target_name": "Torneo 2027",
        "current_employee_id": "employee-052",
        "current_role": "admin",
        "current_conversation_id": "conversation-052",
    }

    created = await build_tournament_goal_shadow(**arguments)
    resumed = await build_tournament_goal_shadow(**arguments)

    assert resumed == created
    assert created["case_version"] == 1
    assert created["operational_writes"] is False
    assert created["draft"]["execution_status"] == "not_executed"
    assert created["validation"]["valid"] is True
    assert created["source"]["authority"]["source_hash"] == (
        created["diff"]["base_snapshot_hash"]
    )
    assert created["source"]["bound_snapshot"]["snapshot_hash"] == (
        created["diff"]["base_snapshot_hash"]
    )
    soul_payload = created["soul_wizard_payload"]
    assert soul_payload["contract"]["contract_id"] == "soul_wizard_contract_v1"
    assert soul_payload["draft"]["tournament_name"] == "Torneo 2027"
    assert soul_payload["draft"]["source_tournament_id"] == (
        created["source"]["bound_snapshot"]["tournament_id"]
    )
    assert soul_payload["draft"]["source_snapshot_id"] == (
        created["diff"]["base_snapshot_hash"]
    )
    assert soul_payload["draft"]["categories"] == ["2012", "2013"]
    assert soul_payload["preview"]["mode"] == "clone_diff"
    assert soul_payload["preview"]["activation_allowed"] is False
    assert soul_payload["preview"]["operational_writes_allowed"] is False
    assert "missing_phase_start_date" in {
        issue["code"] for issue in soul_payload["readiness"]["issues"]
    }
    assert created["next_questions"]
    assert created["missing_information"] == ["source_component:matches_and_schedule"]
    stored = AnalystCaseStore(session).get_case(created["case_id"])
    assert stored is not None
    assert len(stored.versions) == 1
    assert stored.writes_policy["operational_writes_allowed"] is False


@pytest.mark.asyncio
async def test_resume_with_changed_draft_creates_immutable_version(
    session: Session,
    isolated_source: None,
) -> None:
    adapter = AsyncStoreAdapter(session)
    base = {
        "session": adapter,
        "goal": "Crear torneo 2027 desde 2026",
        "source_tournament_id": "00000000-0000-0000-0000-000000000052",
        "target_name": "Torneo 2027",
        "current_employee_id": "employee-052",
        "current_role": "admin",
        "current_conversation_id": "conversation-052",
    }
    first = await build_tournament_goal_shadow(**base)
    revised = await build_tournament_goal_shadow(
        **{
            **base,
            "case_id": first["case_id"],
            "expected_case_version": 1,
            "description": "Edición revisada",
        }
    )

    assert revised["case_version"] == 2
    stored = AnalystCaseStore(session).get_case(first["case_id"])
    assert stored is not None
    assert stored.versions[0].answer_contract["draft"]["description"] == "Base local"
    assert (
        stored.versions[1].answer_contract["draft"]["description"] == "Edición revisada"
    )
    assert "answer_contract" in stored.versions[1].changed_fields


@pytest.mark.asyncio
async def test_case_resume_is_owner_scoped(
    session: Session,
    isolated_source: None,
) -> None:
    adapter = AsyncStoreAdapter(session)
    created = await build_tournament_goal_shadow(
        adapter,
        goal="Crear torneo 2027 desde 2026",
        source_tournament_id="00000000-0000-0000-0000-000000000052",
        target_name="Torneo 2027",
        current_employee_id="owner",
        current_role="admin",
        current_conversation_id="conversation-052",
    )

    with pytest.raises(TournamentGoalCaseForbiddenError):
        await build_tournament_goal_shadow(
            adapter,
            goal="Reanudar",
            source_tournament_id="00000000-0000-0000-0000-000000000052",
            target_name="Torneo 2027",
            case_id=created["case_id"],
            current_employee_id="intruder",
            current_role="admin",
            current_conversation_id="conversation-other",
        )


@pytest.mark.asyncio
async def test_distinct_goals_create_distinct_automatic_cases(
    session: Session,
    isolated_source: None,
) -> None:
    adapter = AsyncStoreAdapter(session)
    common = {
        "session": adapter,
        "source_tournament_id": "00000000-0000-0000-0000-000000000052",
        "current_employee_id": "employee-052",
        "current_role": "admin",
        "current_conversation_id": "conversation-052",
    }

    first = await build_tournament_goal_shadow(
        **common,
        goal="Crear torneo 2027",
        target_name="Torneo 2027",
    )
    second = await build_tournament_goal_shadow(
        **common,
        goal="Crear torneo 2028",
        target_name="Torneo 2028",
    )

    assert first["case_id"] != second["case_id"]


@pytest.mark.asyncio
async def test_explicit_case_id_must_match_the_runtime_contract(
    session: Session,
    isolated_source: None,
) -> None:
    with pytest.raises(TournamentGoalCaseError, match="Invalid.*case_id"):
        await build_tournament_goal_shadow(
            AsyncStoreAdapter(session),
            goal="Reanudar torneo",
            source_tournament_id="00000000-0000-0000-0000-000000000052",
            target_name="Torneo 2027",
            case_id="analyst_case_not-hex",
            current_employee_id="employee-052",
            current_role="admin",
            current_conversation_id="conversation-052",
        )
