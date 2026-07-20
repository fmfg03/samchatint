from pathlib import Path
from uuid import UUID, uuid4

import pytest

from devnous.copa_telmex.models import (
    Player,
    RegistrationPostcommitMutationDecision,
    RegistrationPostcommitMutationProposal,
    Team,
)
from devnous.copa_telmex.persistence_authority import (
    PersistenceAuthorityDenied,
    semantic_event_hash,
)
from devnous.copa_telmex.postcommit_governance import (
    canonical_bytes,
    changed_fields,
    issue_postcommit_persistence_capability,
    player_snapshot,
    sha256_binding,
    team_snapshot,
)


def receipt(event, receipt_id):
    return {
        "receipt_type": "EvidenceReceipt.v1",
        "receipt_id": receipt_id,
        "event_hash": semantic_event_hash(event),
        "event_type": event["event_type"],
        "tenant_id": event["tenant_id"],
        "verified": True,
        "alg": "Ed25519",
    }


def governed_objects():
    proposal_id = uuid4()
    player_id = uuid4()
    team_id = uuid4()
    changes = [
        {
            "field_path": "first_name",
            "previous_value": "Anterior",
            "proposed_value": "Corregido",
        }
    ]
    proposal = RegistrationPostcommitMutationProposal(
        id=proposal_id,
        mutation_request_id=uuid4(),
        entity_type="PLAYER",
        entity_id=player_id,
        team_id=team_id,
        mutation_type="EDIT_PLAYER",
        base_revision=2,
        proposed_revision=3,
        base_snapshot={},
        base_snapshot_hash="sha256:" + "1" * 64,
        proposed_snapshot={},
        proposed_snapshot_hash="sha256:" + "2" * 64,
        field_changes=changes,
        field_change_set_hash="sha256:" + "3" * 64,
        mutation_reason="Corrección registral",
        mutation_reason_binding="sha256:" + "4" * 64,
        source_evidence_binding="hmac-sha256:" + "5" * 64,
        proposer_principal_id="employee-1",
        proposer_role="admin",
        role_assignment_id="sha256:" + "6" * 64,
        authorization_epoch="sha256:" + "7" * 64,
        authentication_method="internal_session",
        authentication_assurance_level=1,
        auth_context_id="sha256:" + "8" * 64,
    )
    decision_event = {
        "event_type": "samchat_registration_postcommit_mutation_decision_v1",
        "tenant_id": "samchat-prod",
        "proposal_id": str(proposal_id),
        "base_snapshot_hash": proposal.base_snapshot_hash,
        "proposed_snapshot_hash": proposal.proposed_snapshot_hash,
        "decision": "AUTHORIZE_POSTCOMMIT_MUTATION",
    }
    decision = RegistrationPostcommitMutationDecision(
        id=uuid4(),
        proposal_id=proposal_id,
        decision_id="sha256:" + "9" * 64,
        policy_hash="sha256:" + "a" * 64,
        decision="AUTHORIZE_POSTCOMMIT_MUTATION",
        reason_codes=["EXACT_POSTCOMMIT_SUCCESSOR_VALID"],
        receipt_id="receipt-pre",
        receipt_alg="Ed25519",
        event_hash="sha256:" + "b" * 64,
        decision_document=decision_event,
        receipt_document=receipt(decision_event, "receipt-pre"),
    )
    attestation = {
        "event_type": "samchat_registration_postcommit_attestation_v1",
        "tenant_id": "samchat-prod",
        "proposal_id": str(proposal_id),
        "actual_projection_hash": proposal.proposed_snapshot_hash,
        "decision": "ATTEST_POSTCOMMIT_MUTATION",
    }
    finality = {
        "attested": True,
        "postcommit_mutation_attestation": attestation,
        "postcommit_finality_receipt": receipt(attestation, "receipt-final"),
    }
    return proposal, decision, finality, player_id, team_id


def test_snapshots_bind_committed_identity_and_evidence_without_timestamps():
    team = Team(id=uuid4(), name="Academicos", telegram_chat_id=7)
    player = Player(
        id=uuid4(),
        team_id=team.id,
        first_name="Nombre",
        last_name="Jugador",
        governance_state="ACTIVE",
        photo_data="opaque-photo",
    )
    team_state = team_snapshot(team)
    player_state = player_snapshot(player)
    assert team_state["entity_type"] == "TEAM"
    assert player_state["entity_type"] == "PLAYER"
    assert player_state["photo_data_hash"].startswith("sha256:")
    assert "photo_data" not in player_state
    assert "updated_at" not in team_state
    assert "updated_at" not in player_state


def test_python_and_sql_snapshot_hashes_share_one_canonical_contract():
    snapshot = {
        "nested": {"z": [1, 2], "aa": True},
        "id": "00000000-0000-4000-8000-000000000001",
        "entity_type": "PLAYER",
    }

    assert canonical_bytes(snapshot).decode("utf-8") == (
        '{"entity_type":"PLAYER",'
        '"id":"00000000-0000-4000-8000-000000000001",'
        '"nested":{"aa":true,"z":[1,2]}}'
    )
    assert sha256_binding(snapshot).startswith("sha256:")

    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "20260716_regs07_postcommit_authority.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION regs07_canonical_jsonb" in migration
    assert "convert_to(regs07_canonical_jsonb(snapshot), 'UTF8')" in migration
    assert "convert_to(snapshot::text, 'UTF8')" not in migration


def test_changed_fields_preserves_previous_and_proposed_values():
    changes = changed_fields(
        {"entity_type": "PLAYER", "id": "1", "first_name": "Anterior"},
        {"entity_type": "PLAYER", "id": "1", "first_name": "Corregido"},
    )
    assert changes == [
        {
            "field_path": "first_name",
            "previous_value": "Anterior",
            "proposed_value": "Corregido",
        }
    ]


def test_double_receipt_capability_is_exact_and_never_authorizes_delete():
    proposal, decision, finality, player_id, team_id = governed_objects()
    capability = issue_postcommit_persistence_capability(
        proposal=proposal,
        decision=decision,
        finality_response=finality,
        execution_id=uuid4(),
    )
    token = object()
    capability.bind(token)
    player = Player(
        id=player_id,
        team_id=team_id,
        first_name="Corregido",
        last_name="Jugador",
        governance_state="ACTIVE",
        postcommit_revision=3,
        postcommit_snapshot_hash=proposal.proposed_snapshot_hash,
    )
    capability.authorize_player_update(
        player,
        {"first_name", "postcommit_revision", "postcommit_snapshot_hash"},
        token=token,
    )
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        capability.deny_delete(player, token=token)
    assert exc.value.reason_code == "POSTCOMMIT_DELETE_DENIED"

    player.postcommit_revision = 4
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        capability.authorize_player_update(
            player,
            {"first_name", "postcommit_revision", "postcommit_snapshot_hash"},
            token=token,
        )
    assert exc.value.reason_code == "POSTCOMMIT_AUTHORITY_SCOPE_MISMATCH"


def test_tampered_finality_cannot_issue_postcommit_authority():
    proposal, decision, finality, _, _ = governed_objects()
    finality["postcommit_mutation_attestation"][
        "actual_projection_hash"
    ] = "sha256:" + "f" * 64
    with pytest.raises(PersistenceAuthorityDenied) as exc:
        issue_postcommit_persistence_capability(
            proposal=proposal,
            decision=decision,
            finality_response=finality,
            execution_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        )
    assert exc.value.reason_code == "POSTCOMMIT_AUTHORITY_INVALID"
