"""Staff writes fail closed until staff-specific authority exists."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from devnous.copa_telmex.database import CopaTelmexDB
from devnous.copa_telmex.models import Team, TeamStaff
from devnous.copa_telmex.persistence_authority import (
    PersistenceAuthorityDenied,
    issue_registration_persistence_capability,
    semantic_event_hash,
)


def player_only_capability(team_id):
    roster = {
        "event_type": "samchat_registration_roster_decision_v1",
        "tenant_id": "synthetic",
        "draft_id": "draft-1",
        "draft_version": 1,
        "team_id": str(team_id),
        "decision_id": "sha256:" + "1" * 64,
        "roster_draft_binding": "hmac-sha256:" + "2" * 64,
        "ordered_player_slots": [1],
        "decision": "AUTHORIZE_PENDING_MATERIALIZATION",
    }
    receipt = {
        "receipt_type": "EvidenceReceipt.v1",
        "receipt_id": "sha256:" + "3" * 64,
        "event_hash": semantic_event_hash(roster),
        "event_type": roster["event_type"],
        "tenant_id": "synthetic",
        "verified": True,
    }
    return issue_registration_persistence_capability(
        {
            "authorized": True,
            "roster_decision": roster,
            "preauthorization_receipt": receipt,
        },
        tenant_id="synthetic",
        draft_id="draft-1",
        draft_version=1,
        team_id=str(team_id),
    )


def staff_values(team_id, staff_id):
    return {
        "id": staff_id,
        "team_id": team_id,
        "staff_slot": 1,
        "role": "director_tecnico",
        "first_name": "Synthetic",
        "evidence": {},
        "governance_state": "PENDING_FINALITY",
        "governance_draft_id": "draft-1",
        "governance_draft_version": 1,
        "governance_decision_id": "sha256:" + "1" * 64,
        "roster_draft_binding": "hmac-sha256:" + "2" * 64,
        "preauthorization_receipt_id": "sha256:" + "3" * 64,
    }


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Team.__table__.create(engine)
    TeamStaff.__table__.create(engine)
    team_id, staff_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(Team.__table__.insert().values(id=team_id, name="Synthetic"))
        connection.execute(
            TeamStaff.__table__.insert().values(**staff_values(team_id, staff_id))
        )
    yield engine, team_id, staff_id
    engine.dispose()


@pytest.mark.parametrize("with_player_capability", [False, True])
@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_staff_orm_mutation_fails_without_staff_authority(
    database, with_player_capability, operation
):
    engine, team_id, staff_id = database
    with Session(engine) as session:
        db = CopaTelmexDB(SimpleNamespace(sync_session=session))
        if with_player_capability:
            db.bind_persistence_authority(player_only_capability(team_id))
        if operation == "insert":
            session.add(
                TeamStaff(
                    **{
                        **staff_values(team_id, uuid4()),
                        "staff_slot": 2,
                        "role": "auxiliar",
                    }
                )
            )
        else:
            staff = session.get(TeamStaff, staff_id)
            if operation == "update":
                staff.first_name = "Changed"
            else:
                session.delete(staff)
        with pytest.raises(PersistenceAuthorityDenied) as exc:
            session.flush()
        expected = (
            "STAFF_GOVERNANCE_CONTRACT_REQUIRED"
            if with_player_capability
            else "PERSISTENCE_AUTHORITY_REQUIRED"
        )
        assert exc.value.reason_code == expected
        session.rollback()
    with Session(engine) as session:
        rows = session.scalars(select(TeamStaff)).all()
        assert len(rows) == 1
        assert rows[0].id == staff_id
        assert rows[0].first_name == "Synthetic"


def test_staff_added_via_team_relationship_is_not_exempt(database):
    engine, team_id, staff_id = database
    with Session(engine) as session:
        db = CopaTelmexDB(SimpleNamespace(sync_session=session))
        db.bind_persistence_authority(player_only_capability(team_id))
        team = session.get(Team, team_id)
        team.staff.append(
            TeamStaff(
                **{
                    **staff_values(team_id, uuid4()),
                    "staff_slot": 2,
                    "role": "auxiliar",
                }
            )
        )
        with pytest.raises(PersistenceAuthorityDenied) as exc:
            session.flush()
        assert exc.value.reason_code == "STAFF_GOVERNANCE_CONTRACT_REQUIRED"
        session.rollback()
    with Session(engine) as session:
        assert session.scalars(select(TeamStaff.id)).all() == [staff_id]


@pytest.mark.parametrize("with_player_capability", [False, True])
def test_removing_staff_relationship_cannot_delete_orphan(
    database, with_player_capability
):
    engine, team_id, staff_id = database
    with Session(engine) as session:
        db = CopaTelmexDB(SimpleNamespace(sync_session=session))
        if with_player_capability:
            db.bind_persistence_authority(player_only_capability(team_id))
        team = session.get(Team, team_id)
        staff = session.get(TeamStaff, staff_id)
        team.staff.remove(staff)
        with pytest.raises(PersistenceAuthorityDenied) as exc:
            session.flush()
        expected = (
            "STAFF_GOVERNANCE_CONTRACT_REQUIRED"
            if with_player_capability
            else "PERSISTENCE_AUTHORITY_REQUIRED"
        )
        assert exc.value.reason_code == expected
        session.rollback()
    with Session(engine) as session:
        assert session.get(TeamStaff, staff_id).team_id == team_id
        assert session.scalars(select(TeamStaff.id)).all() == [staff_id]


@pytest.mark.parametrize("with_player_capability", [False, True])
def test_staff_team_relationship_cannot_be_reassigned(database, with_player_capability):
    engine, team_id, staff_id = database
    other_team_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            Team.__table__.insert().values(id=other_team_id, name="Other synthetic")
        )
    with Session(engine) as session:
        db = CopaTelmexDB(SimpleNamespace(sync_session=session))
        if with_player_capability:
            db.bind_persistence_authority(player_only_capability(team_id))
        staff = session.get(TeamStaff, staff_id)
        staff.team = session.get(Team, other_team_id)
        with pytest.raises(PersistenceAuthorityDenied) as exc:
            session.flush()
        expected = (
            "STAFF_GOVERNANCE_CONTRACT_REQUIRED"
            if with_player_capability
            else "PERSISTENCE_AUTHORITY_REQUIRED"
        )
        assert exc.value.reason_code == expected
        session.rollback()
    with Session(engine) as session:
        assert session.get(TeamStaff, staff_id).team_id == team_id
        assert session.scalars(select(TeamStaff.id)).all() == [staff_id]
