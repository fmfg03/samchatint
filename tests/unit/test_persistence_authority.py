import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from devnous.copa_telmex.database import CopaTelmexDB
from devnous.copa_telmex.models import Team
from devnous.copa_telmex.persistence_authority import (
    PersistenceAuthorityDenied,
    issue_registration_persistence_capability,
    semantic_event_hash,
)

TENANT = "samchat-prod"
DRAFT = "draft-regs13"
TEAM_ID = uuid4()


def governance_result(*, slots=(1, 2), team_id=TEAM_ID):
    roster = {
        "event_type": "samchat_registration_roster_decision_v1",
        "tenant_id": TENANT,
        "draft_id": DRAFT,
        "draft_version": 7,
        "team_id": str(team_id),
        "decision_id": "sha256:" + "1" * 64,
        "roster_draft_binding": "hmac-sha256:" + "2" * 64,
        "ordered_player_slots": list(slots),
        "decision": "AUTHORIZE_PENDING_MATERIALIZATION",
    }
    receipt = {
        "receipt_type": "EvidenceReceipt.v1",
        "receipt_id": "sha256:" + "3" * 64,
        "event_hash": semantic_event_hash(roster),
        "event_type": roster["event_type"],
        "tenant_id": TENANT,
        "verified": True,
    }
    return {
        "authorized": True,
        "roster_decision": roster,
        "preauthorization_receipt": receipt,
    }


def capability(*, slots=(1, 2), team_id=TEAM_ID):
    return issue_registration_persistence_capability(
        governance_result(slots=slots, team_id=team_id),
        tenant_id=TENANT,
        draft_id=DRAFT,
        draft_version=7,
        team_id=str(team_id),
    )


class FakeAsyncSession:
    def __init__(self):
        self.added = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.existing = None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1

    async def get(self, _model, _identifier):
        return self.existing

    async def delete(self, _value):
        raise AssertionError("delete must not execute without deletion authority")


def test_create_team_fails_closed_without_transaction_capability():
    db = CopaTelmexDB(FakeAsyncSession())
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        asyncio.run(
            db.create_team(name="Opaque Team", telegram_chat_id=1, team_id=TEAM_ID)
        )
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_REQUIRED"


@pytest.mark.parametrize(
    "operation",
    [
        lambda db: db.update_team(TEAM_ID, name="Changed"),
        lambda db: db.update_player(uuid4(), first_name="Changed"),
        lambda db: db.delete_player(uuid4()),
    ],
)
def test_update_and_delete_primitives_fail_before_database_access(operation):
    db = CopaTelmexDB(FakeAsyncSession())
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        asyncio.run(operation(db))
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_REQUIRED"


def test_capability_is_scoped_to_team_draft_receipt_and_player_slot():
    session = FakeAsyncSession()
    db = CopaTelmexDB(session)
    db.bind_persistence_authority(capability(slots=(1,)))
    team = asyncio.run(
        db.create_team(name="Opaque Team", telegram_chat_id=1, team_id=TEAM_ID)
    )
    assert team.id == TEAM_ID

    player = asyncio.run(
        db.create_player(
            team_id=TEAM_ID,
            first_name="Opaque",
            last_name="Candidate",
            roster_index=1,
            governance_state="PENDING_FINALITY",
            governance_draft_id=DRAFT,
            governance_draft_version=7,
            governance_decision_id="sha256:" + "1" * 64,
            roster_draft_binding="hmac-sha256:" + "2" * 64,
            preauthorization_receipt_id="sha256:" + "3" * 64,
        )
    )
    assert player.roster_index == 1

    with pytest.raises(PersistenceAuthorityDenied) as exc:
        asyncio.run(
            db.create_player(
                team_id=TEAM_ID,
                first_name="Out",
                last_name="Of Scope",
                roster_index=9,
                governance_state="PENDING_FINALITY",
                governance_draft_id=DRAFT,
                governance_draft_version=7,
                governance_decision_id="sha256:" + "1" * 64,
                roster_draft_binding="hmac-sha256:" + "2" * 64,
                preauthorization_receipt_id="sha256:" + "3" * 64,
            )
        )
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH"


def test_capability_cannot_bind_to_two_transactions_or_survive_commit():
    authority = capability()
    first = CopaTelmexDB(FakeAsyncSession())
    second = CopaTelmexDB(FakeAsyncSession())
    first.bind_persistence_authority(authority)
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        second.bind_persistence_authority(authority)
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_ALREADY_BOUND"

    asyncio.run(first.commit())
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        first.bind_persistence_authority(authority)
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_ALREADY_CONSUMED"


def test_tampered_preauthorization_receipt_cannot_issue_capability():
    result = governance_result()
    result["preauthorization_receipt"]["event_hash"] = "sha256:" + "f" * 64
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        issue_registration_persistence_capability(
            result,
            tenant_id=TENANT,
            draft_id=DRAFT,
            draft_version=7,
            team_id=str(TEAM_ID),
        )
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_INVALID"


def test_player_activation_requires_exact_post_execution_receipt():
    db = CopaTelmexDB(FakeAsyncSession())
    db.bind_persistence_authority(capability(slots=(1,)))
    player = asyncio.run(
        db.create_player(
            team_id=TEAM_ID,
            first_name="Opaque",
            last_name="Candidate",
            roster_index=1,
            governance_state="PENDING_FINALITY",
            governance_draft_id=DRAFT,
            governance_draft_version=7,
            governance_decision_id="sha256:" + "1" * 64,
            roster_draft_binding="hmac-sha256:" + "2" * 64,
            preauthorization_receipt_id="sha256:" + "3" * 64,
        )
    )
    player.id = uuid4()
    attestation = {
        "event_type": "samchat_registration_finality_attestation_v1",
        "tenant_id": TENANT,
        "draft_id": DRAFT,
        "draft_version": 7,
        "player_id": str(player.id),
        "player_slot": 1,
        "roster_draft_binding": "hmac-sha256:" + "2" * 64,
        "preauthorization_receipt_id": "sha256:" + "3" * 64,
        "decision": "ACTIVATE_PLAYER",
    }
    finality = {
        "activate": True,
        "attestation": attestation,
        "finality_receipt": {
            "receipt_type": "EvidenceReceipt.v1",
            "receipt_id": "sha256:" + "4" * 64,
            "event_hash": semantic_event_hash(attestation),
            "event_type": attestation["event_type"],
            "tenant_id": TENANT,
            "verified": True,
        },
    }
    bad_finality = deepcopy(finality)
    bad_finality["finality_receipt"]["event_hash"] = "sha256:" + "f" * 64
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        db.record_player_finality(player, bad_finality)
    assert exc.value.reason_code == "FINALITY_RECEIPT_REQUIRED"

    assert db.record_player_finality(player, finality) == "sha256:" + "4" * 64

    tampered = governance_result()
    tampered["authorized"] = False
    with pytest.raises(PersistenceAuthorityDenied):
        issue_registration_persistence_capability(
            tampered,
            tenant_id=TENANT,
            draft_id=DRAFT,
            draft_version=7,
            team_id=str(TEAM_ID),
        )


def test_registration_capability_never_authorizes_identity_edit_or_delete():
    session = FakeAsyncSession()
    db = CopaTelmexDB(session)
    db.bind_persistence_authority(capability(slots=(1,)))
    player = asyncio.run(
        db.create_player(
            team_id=TEAM_ID,
            first_name="Opaque",
            last_name="Candidate",
            roster_index=1,
            governance_state="PENDING_FINALITY",
            governance_draft_id=DRAFT,
            governance_draft_version=7,
            governance_decision_id="sha256:" + "1" * 64,
            roster_draft_binding="hmac-sha256:" + "2" * 64,
            preauthorization_receipt_id="sha256:" + "3" * 64,
        )
    )
    player.id = uuid4()
    session.existing = player

    with pytest.raises(PersistenceAuthorityDenied) as exc:
        asyncio.run(db.update_player(player.id, first_name="Replacement"))
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH"

    with pytest.raises(PersistenceAuthorityDenied) as exc:
        asyncio.run(db.delete_player(player.id))
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH"


def test_session_flush_guard_catches_direct_orm_team_write():
    sync_session = Session()
    async_like = SimpleNamespace(sync_session=sync_session)
    CopaTelmexDB(async_like)
    sync_session.add(Team(id=TEAM_ID, name="Direct ORM", telegram_chat_id=1))
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        sync_session.flush()
    assert exc.value.reason_code == "PERSISTENCE_AUTHORITY_REQUIRED"
    sync_session.rollback()
