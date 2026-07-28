from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from devnous.copa_telmex.models import Base
from samchat.assistant.analyst_case import (
    CASE_STATUS_ANALYZED,
    CASE_STATUS_CLOSED,
    CASE_WRITE_POLICY,
    AnalystCase,
    AnalystCaseVersion,
)
from samchat.assistant.analyst_case_models import (
    AnalystCaseRecord,
    AnalystCaseVersionRecord,
)
from samchat.assistant.analyst_case_store import AnalystCaseStore, version_id_for
from samchat.assistant.tournament_draft_case import (
    PUBLIC_KEYS,
    TournamentDraftCaseConflictError,
    TournamentDraftCaseError,
    TournamentDraftCaseForbiddenError,
    run_tournament_draft_workbench,
)
from samchat.assistant.tournament_draft_authority import (
    TournamentDraftOwnerInactiveError,
)
from samchat.assistant.tournament_goal_shadow import (
    TournamentSnapshot,
    build_tournament_goal_shadow,
)


OWNER_ID = "00000000-0000-0000-0000-000000000053"
CONVERSATION_ID = "00000000-0000-0000-0000-000000000054"
CASE_ID = "analyst_case_" + ("5" * 32)


class AsyncStoreAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def run_sync(self, operation: Callable[[Session], Any]) -> Any:
        return operation(self.session)


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            AnalystCaseRecord.__table__,
            AnalystCaseVersionRecord.__table__,
        ],
    )
    current = sessionmaker(bind=engine)()
    try:
        yield current
    finally:
        current.close()
        engine.dispose()


def _shadow():
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
            "source_authority_hash": "sha256:" + ("a" * 64),
        }
    )
    return build_tournament_goal_shadow(
        source,
        requested_name="Torneo 2027",
        goal="Crear el torneo siguiente",
    )


def _create_case(
    session: Session,
    *,
    owner: str = OWNER_ID,
    kind: str = "tournament_goal_shadow",
) -> AnalystCase:
    shadow = _shadow()
    contract = {
        "schema_version": "goal_to_outcome_v1",
        "kind": "tournament_goal_shadow",
        "source_authority": {
            "source_hash": shadow.source.snapshot_hash,
            "project": {"id": shadow.source.tournament_id},
        },
        **shadow.to_dict(),
        "operational_writes": False,
    }
    version = AnalystCaseVersion(
        version_id=version_id_for(CASE_ID, 1),
        version_number=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=owner,
        status=CASE_STATUS_ANALYZED,
        answer="Borrador",
        evidence=[],
        next_questions=[],
        suggested_routes=[],
        caveats=[],
        answer_contract=contract,
    )
    case = AnalystCase(
        case_id=CASE_ID,
        user_id=owner,
        role="admin",
        question="Crear torneo",
        analyst_intent={
            "kind": kind,
            "source_tournament_id": shadow.source.tournament_id,
        },
        status=CASE_STATUS_ANALYZED,
        evidence=[],
        current_answer="Borrador",
        next_questions=[],
        suggested_routes=[],
        caveats=[],
        versions=[version],
        writes_policy=dict(CASE_WRITE_POLICY),
    )
    return AnalystCaseStore(session).create_case(case)


@pytest.fixture(autouse=True)
def isolated_pointer(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    pointer: dict[str, Any] = {
        "case_id": CASE_ID,
        "case_version": 1,
        "status": "drafting",
    }

    async def get_pointer(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(pointer)

    async def set_pointer(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        pointer.update(
            case_id=kwargs["case_id"],
            case_version=kwargs["case_version"],
            status=kwargs["status"],
        )
        return dict(pointer)

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_case.get_active_tournament_case_pointer",
        get_pointer,
    )
    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_case.set_active_tournament_case_pointer",
        set_pointer,
    )
    return pointer


def _arguments(adapter: AsyncStoreAdapter) -> dict[str, Any]:
    return {
        "session": adapter,
        "current_role": "admin",
        "current_employee_id": OWNER_ID,
        "current_conversation_id": CONVERSATION_ID,
    }


@pytest.mark.asyncio
async def test_inspect_resolves_active_pointer_and_exact_public_contract(
    db_session: Session,
) -> None:
    _create_case(db_session)
    response = await run_tournament_draft_workbench(
        **_arguments(AsyncStoreAdapter(db_session)), action="inspect"
    )

    assert set(response) == PUBLIC_KEYS
    assert response["case_id"] == CASE_ID
    assert response["case_version"] == 1
    assert response["workbench_status"] == "draft"
    assert response["operational_writes"] is False
    assert response["diff"]["from_previous_version"] == {
        "change_count": 0,
        "entries": [],
    }


@pytest.mark.asyncio
async def test_revise_appends_version_and_preserves_omitted_fields(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create_case(db_session)
    original_name = created.versions[-1].answer_contract["draft"]["name"]
    owners = []

    async def active_owner(session: Any, owner_id: str) -> object:
        owners.append((session, owner_id))
        return object()

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_case.inspect_active_tournament_owner",
        active_owner,
    )
    adapter = AsyncStoreAdapter(db_session)

    response = await run_tournament_draft_workbench(
        **_arguments(adapter),
        action="revise",
        case_id=CASE_ID,
        expected_case_version=1,
        changes={"description": None},
    )

    assert response["case_version"] == 2
    assert owners == [(adapter, OWNER_ID)]
    assert response["draft"]["name"] == original_name
    assert response["draft"]["description"] is None
    stored = AnalystCaseStore(db_session).get_case(CASE_ID)
    assert stored is not None
    assert len(stored.versions) == 2
    assert stored.versions[0].answer_contract["draft"]["description"] == "Base"


@pytest.mark.asyncio
async def test_stale_version_rejected_without_append(db_session: Session) -> None:
    _create_case(db_session)
    with pytest.raises(TournamentDraftCaseConflictError, match="Stale"):
        await run_tournament_draft_workbench(
            **_arguments(AsyncStoreAdapter(db_session)),
            action="revise",
            expected_case_version=2,
            changes={"description": "No"},
        )
    stored = AnalystCaseStore(db_session).get_case(CASE_ID)
    assert stored is not None and len(stored.versions) == 1


@pytest.mark.asyncio
async def test_revise_rejects_inactive_owner_before_append(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_case(db_session)

    async def inactive(*_args: Any, **_kwargs: Any) -> object:
        raise TournamentDraftOwnerInactiveError("owner inactive")

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_case.inspect_active_tournament_owner",
        inactive,
    )
    with pytest.raises(TournamentDraftOwnerInactiveError, match="inactive"):
        await run_tournament_draft_workbench(
            **_arguments(AsyncStoreAdapter(db_session)),
            action="revise",
            expected_case_version=1,
            changes={"description": "No debe guardarse"},
        )
    stored = AnalystCaseStore(db_session).get_case(CASE_ID)
    assert stored is not None and len(stored.versions) == 1


@pytest.mark.asyncio
async def test_freeze_revalidates_owner_and_source_and_binds_hash(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create_case(db_session)
    draft_hash = created.versions[-1].answer_contract["business_diff"]["draft_hash"]
    observed: dict[str, Any] = {}

    class Owner:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {"id": OWNER_ID, "nombre": "Alicia", "activo": True}

    class Source:
        source_hash = "sha256:" + ("a" * 64)

    async def authority(session: Any, **kwargs: Any) -> Any:
        observed.update(session=session, **kwargs)
        return type(
            "Authority",
            (),
            {"owner": Owner(), "source": Source()},
        )()

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_case.inspect_tournament_draft_authority",
        authority,
    )
    adapter = AsyncStoreAdapter(db_session)
    response = await run_tournament_draft_workbench(
        **_arguments(adapter),
        action="freeze",
        expected_case_version=1,
        expected_draft_hash=draft_hash,
    )

    assert observed["session"] is adapter
    assert observed["owner_employee_id"] == OWNER_ID
    assert observed["expected_source_hash"] == "sha256:" + ("a" * 64)
    assert response["workbench_status"] == "frozen"
    assert response["proposal"]["proposal_hash"].startswith("sha256:")
    assert response["operational_writes"] is False


@pytest.mark.asyncio
async def test_cancel_is_append_only_closed_and_cannot_be_revised(
    db_session: Session,
) -> None:
    _create_case(db_session)
    adapter = AsyncStoreAdapter(db_session)
    cancelled = await run_tournament_draft_workbench(
        **_arguments(adapter),
        action="cancel",
        expected_case_version=1,
        reason="Ya no se requiere",
    )

    assert cancelled["workbench_status"] == "abandoned"
    assert cancelled["allowed_next_actions"] == []
    stored = AnalystCaseStore(db_session).get_case(CASE_ID)
    assert stored is not None
    assert stored.status == CASE_STATUS_CLOSED
    assert len(stored.versions) == 2
    with pytest.raises(TournamentDraftCaseConflictError, match="closed"):
        await run_tournament_draft_workbench(
            **_arguments(adapter),
            action="revise",
            expected_case_version=2,
            changes={"description": "Resucitar"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "kind"),
    [(str(uuid4()), "tournament_goal_shadow"), (OWNER_ID, "financial_case")],
)
async def test_owner_and_kind_are_isolated(
    db_session: Session,
    owner: str,
    kind: str,
) -> None:
    _create_case(db_session, owner=owner, kind=kind)
    with pytest.raises(TournamentDraftCaseForbiddenError):
        await run_tournament_draft_workbench(
            **_arguments(AsyncStoreAdapter(db_session)),
            action="inspect",
            case_id=CASE_ID,
        )


@pytest.mark.asyncio
async def test_service_rejects_non_admin_role_before_case_access() -> None:
    with pytest.raises(TournamentDraftCaseError, match="Trusted assistant identity"):
        await run_tournament_draft_workbench(
            object(),
            action="inspect",
            current_role="empleado",
            current_employee_id=OWNER_ID,
            current_conversation_id=CONVERSATION_ID,
        )
