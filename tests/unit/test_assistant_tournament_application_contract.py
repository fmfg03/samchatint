from copy import deepcopy

import pytest

from samchat.assistant.tournament_application_contract import (
    APPLIED,
    APPROVED,
    FROZEN,
    NO_CLAIMS,
    TournamentApplicationContract,
    TournamentApplicationContractError,
    TournamentApplicationTamperedError,
    apply_tournament_application,
    approve_tournament_application,
    start_tournament_application,
    verify_application_receipt,
    verify_approval_receipt,
    verify_tournament_application_contract,
)
from samchat.assistant.tournament_draft_workbench import (
    TournamentDraftWorkbench,
    freeze_tournament_proposal,
)
from samchat.assistant.tournament_goal_shadow import (
    TournamentSnapshot,
    build_tournament_goal_shadow,
    canonical_sha256,
)


CASE_ID = "analyst_case_" + ("5" * 32)
SOURCE_HASH = "sha256:" + ("a" * 64)
OWNER_ID = "00000000-0000-0000-0000-000000000053"
APPROVER_ID = "00000000-0000-0000-0000-000000000054"
APPLIER_ID = "00000000-0000-0000-0000-000000000055"
TARGET_ID = "00000000-0000-0000-0000-000000000056"


def _actor(actor_id: str, name: str = "Operador"):
    return {
        "id": actor_id,
        "nombre": name,
        "rol": "admin",
        "activo": True,
    }


def _frozen():
    source = TournamentSnapshot.from_mapping(
        {
            "id": "00000000-0000-0000-0000-000000000052",
            "name": "Torneo 2026",
            "description": "Base local",
            "active": True,
            "display_order": 3,
            "accounting_account": "4-100",
            "stages": ["Estatal", "Nacional"],
            "categories": ["2012", "2013"],
            "visibility_areas": ["Operaciones"],
            "source_authority_hash": SOURCE_HASH,
        }
    )
    shadow = build_tournament_goal_shadow(
        source,
        requested_name="Torneo 2027",
        goal="Crear torneo 2027 desde 2026",
    )
    return freeze_tournament_proposal(
        TournamentDraftWorkbench(shadow=shadow),
        case_id=CASE_ID,
        draft_case_version=4,
        verified_owner=_actor(OWNER_ID, "Solicitante"),
        verified_source_hash=SOURCE_HASH,
    ).frozen_proposal


def _expected(contract):
    payload = contract.frozen_proposal.payload
    return {
        "expected_proposal_hash": contract.frozen_proposal.proposal_hash,
        "expected_draft_hash": payload["draft_hash"],
        "verified_source_hash": payload["source_authority_hash"],
    }


def _approved():
    contract = start_tournament_application(_frozen())
    return approve_tournament_application(
        contract,
        approved_by=_actor(APPROVER_ID, "Aprobador"),
        approved_case_version=5,
        approved_at="2026-07-28T12:00:00-06:00",
        note="Aprobado para crear únicamente el proyecto local.",
        **_expected(contract),
    )


def _applied():
    contract = _approved()
    return apply_tournament_application(
        contract,
        applied_by=_actor(APPLIER_ID, "Aplicador"),
        applied_case_version=6,
        applied_at="2026-07-28T18:05:00Z",
        target_tournament_id=TARGET_ID,
        expected_approval_receipt_hash=contract.approval_receipt.receipt_hash,
        **_expected(contract),
    )


def test_frozen_contract_round_trip_is_stable_and_inert():
    contract = start_tournament_application(_frozen())

    recovered = TournamentApplicationContract.from_mapping(contract.to_dict())

    assert recovered == contract
    assert recovered.state == FROZEN
    assert recovered.approval_receipt is None
    assert recovered.application_receipt is None
    assert recovered.to_dict() == contract.to_dict()


def test_state_machine_rejects_skipped_and_repeated_transitions():
    frozen = start_tournament_application(_frozen())
    with pytest.raises(TournamentApplicationContractError, match="approved"):
        apply_tournament_application(
            frozen,
            applied_by=_actor(APPLIER_ID),
            applied_case_version=5,
            applied_at="2026-07-28T18:00:00Z",
            target_tournament_id=TARGET_ID,
            expected_approval_receipt_hash="sha256:" + ("1" * 64),
            **_expected(frozen),
        )

    approved = _approved()
    with pytest.raises(TournamentApplicationContractError, match="frozen"):
        approve_tournament_application(
            approved,
            approved_by=_actor(APPROVER_ID),
            approved_case_version=6,
            approved_at="2026-07-28T19:00:00Z",
            **_expected(approved),
        )


def test_approval_receipt_binds_exact_proposal_actor_source_and_draft():
    contract = _approved()
    payload = verify_approval_receipt(contract.approval_receipt)

    assert contract.state == APPROVED
    assert payload["case_id"] == CASE_ID
    assert payload["proposal_hash"] == contract.frozen_proposal.proposal_hash
    assert payload["draft_hash"] == contract.frozen_proposal.payload["draft_hash"]
    assert payload["source_hash"] == SOURCE_HASH
    assert payload["owner_employee_id"] == OWNER_ID
    assert payload["approved_by"]["id"] == APPROVER_ID
    assert payload["approved_at"] == "2026-07-28T18:00:00+00:00"
    assert payload["replay_key"].startswith("sha256:")
    assert payload["operational_writes_performed"] is False


def test_owner_cannot_approve_or_apply_and_actor_must_be_active():
    frozen = start_tournament_application(_frozen())
    with pytest.raises(TournamentApplicationContractError, match="owner"):
        approve_tournament_application(
            frozen,
            approved_by=_actor(OWNER_ID),
            approved_case_version=5,
            approved_at="2026-07-28T18:00:00Z",
            **_expected(frozen),
        )
    inactive = _actor(APPROVER_ID)
    inactive["activo"] = False
    with pytest.raises(TournamentApplicationContractError, match="active"):
        approve_tournament_application(
            frozen,
            approved_by=inactive,
            approved_case_version=5,
            approved_at="2026-07-28T18:00:00Z",
            **_expected(frozen),
        )
    approved = _approved()
    with pytest.raises(TournamentApplicationContractError, match="owner"):
        apply_tournament_application(
            approved,
            applied_by=_actor(OWNER_ID),
            applied_case_version=6,
            applied_at="2026-07-28T18:05:00Z",
            target_tournament_id=TARGET_ID,
            expected_approval_receipt_hash=approved.approval_receipt.receipt_hash,
            **_expected(approved),
        )


@pytest.mark.parametrize(
    "changed",
    ["expected_proposal_hash", "expected_draft_hash", "verified_source_hash"],
)
def test_approval_rejects_every_stale_binding(changed):
    frozen = start_tournament_application(_frozen())
    expected = _expected(frozen)
    expected[changed] = "sha256:" + ("f" * 64)

    with pytest.raises(TournamentApplicationContractError, match="stale"):
        approve_tournament_application(
            frozen,
            approved_by=_actor(APPROVER_ID),
            approved_case_version=5,
            approved_at="2026-07-28T18:00:00Z",
            **expected,
        )


def test_application_receipt_has_one_exact_write_set_and_explicit_non_claims():
    contract = _applied()
    payload = verify_application_receipt(contract.application_receipt)
    write_set = payload["write_set"]

    assert contract.state == APPLIED
    assert write_set == {
        "operation": "insert",
        "table": "tournaments",
        "record": {
            "id": TARGET_ID,
            "name": "Torneo 2027",
            "description": "Base local",
            "active": True,
            "display_order": 3,
            "cuenta_contable_relacionada": "4-100",
            "etapas": ["Estatal", "Nacional"],
            "categorias": ["2012", "2013"],
            "form_visibility_areas": ["Operaciones"],
        },
        "row_count": 1,
    }
    assert payload["no_claims"] == list(NO_CLAIMS)
    assert payload["external_notifications_enqueued"] is False
    assert payload["replay_key"].startswith("sha256:")
    assert payload["approval_receipt_hash"] == (contract.approval_receipt.receipt_hash)


def test_application_receipt_binds_effective_persisted_projection_to_approved_draft():
    approved = _approved()
    effective = {
        key: approved.frozen_proposal.payload["draft"][key]
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
    applied = apply_tournament_application(
        approved,
        applied_by=_actor(APPLIER_ID),
        applied_case_version=6,
        applied_at="2026-07-28T18:05:00Z",
        target_tournament_id=TARGET_ID,
        expected_approval_receipt_hash=approved.approval_receipt.receipt_hash,
        effective_projection=effective,
        **_expected(approved),
    )

    payload = applied.application_receipt.payload
    assert payload["effective_projection"] == effective
    assert payload["write_set"]["record"]["etapas"] == ["Estatal", "Nacional"]
    assert payload["write_set"]["record"]["form_visibility_areas"] == ["Operaciones"]
    assert payload["persistence_verification"] == {
        "precommit_readback_matches_effective_projection": True,
        "operations_link_created_by_transaction": False,
    }


def test_application_rejects_effective_projection_that_differs_from_approved_draft():
    approved = _approved()
    effective = {
        key: approved.frozen_proposal.payload["draft"][key]
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
    effective["name"] = "Otro torneo"

    with pytest.raises(TournamentApplicationTamperedError, match="differs"):
        apply_tournament_application(
            approved,
            applied_by=_actor(APPLIER_ID),
            applied_case_version=6,
            applied_at="2026-07-28T18:05:00Z",
            target_tournament_id=TARGET_ID,
            expected_approval_receipt_hash=approved.approval_receipt.receipt_hash,
            effective_projection=effective,
            **_expected(approved),
        )


def test_applied_serialization_is_replay_stable_and_fully_verified():
    applied = _applied()
    serialized = applied.to_dict()

    first = TournamentApplicationContract.from_mapping(serialized)
    second = TournamentApplicationContract.from_mapping(first.to_dict())

    assert first == applied
    assert second == first
    assert second.to_dict() == serialized
    assert verify_tournament_application_contract(second) == serialized
    assert second.approval_receipt.payload["replay_key"] == (
        applied.approval_receipt.payload["replay_key"]
    )
    assert second.application_receipt.payload["replay_key"] == (
        applied.application_receipt.payload["replay_key"]
    )


def test_receipt_payload_tamper_without_rehash_fails_closed():
    applied = _applied().to_dict()
    applied["application_receipt"]["payload"][
        "target_tournament_id"
    ] = "00000000-0000-0000-0000-000000000099"

    with pytest.raises(TournamentApplicationTamperedError):
        TournamentApplicationContract.from_mapping(applied)


def test_rehashed_write_set_expansion_and_owner_substitution_fail_closed():
    applied = _applied().to_dict()
    receipt = applied["application_receipt"]
    receipt["payload"]["write_set"]["record"]["schedule"] = []
    receipt["receipt_hash"] = "sha256:" + canonical_sha256(receipt["payload"])
    with pytest.raises(TournamentApplicationTamperedError, match="inconsistent"):
        TournamentApplicationContract.from_mapping(applied)

    approved = _approved().to_dict()
    receipt = approved["approval_receipt"]
    receipt["payload"]["approved_by"]["id"] = OWNER_ID
    receipt["receipt_hash"] = "sha256:" + canonical_sha256(receipt["payload"])
    with pytest.raises(TournamentApplicationTamperedError, match="inconsistent"):
        TournamentApplicationContract.from_mapping(approved)


def test_cross_receipt_and_frozen_proposal_substitution_fail_closed():
    serialized = _applied().to_dict()
    serialized["application_receipt"]["payload"]["approval_receipt_hash"] = (
        "sha256:" + ("9" * 64)
    )
    receipt = serialized["application_receipt"]
    receipt["receipt_hash"] = "sha256:" + canonical_sha256(receipt["payload"])
    with pytest.raises(TournamentApplicationTamperedError):
        TournamentApplicationContract.from_mapping(serialized)

    serialized = _approved().to_dict()
    serialized["frozen_proposal"]["payload"]["draft"]["name"] = "Forjado"
    with pytest.raises(TournamentApplicationTamperedError):
        TournamentApplicationContract.from_mapping(serialized)


@pytest.mark.parametrize(
    ("version", "timestamp"),
    [(0, "2026-07-28T18:00:00Z"), (5, "2026-07-28 18:00:00")],
)
def test_approval_requires_positive_version_and_timezone(version, timestamp):
    frozen = start_tournament_application(_frozen())
    with pytest.raises(TournamentApplicationContractError):
        approve_tournament_application(
            frozen,
            approved_by=_actor(APPROVER_ID),
            approved_case_version=version,
            approved_at=timestamp,
            **_expected(frozen),
        )


def test_application_requires_exact_approval_hash_and_uuid_target():
    approved = _approved()
    with pytest.raises(TournamentApplicationContractError, match="approval"):
        apply_tournament_application(
            approved,
            applied_by=_actor(APPLIER_ID),
            applied_case_version=6,
            applied_at="2026-07-28T18:05:00Z",
            target_tournament_id=TARGET_ID,
            expected_approval_receipt_hash="sha256:" + ("0" * 64),
            **_expected(approved),
        )
    with pytest.raises(TournamentApplicationContractError, match="UUID"):
        apply_tournament_application(
            approved,
            applied_by=_actor(APPLIER_ID),
            applied_case_version=6,
            applied_at="2026-07-28T18:05:00Z",
            target_tournament_id="not-a-uuid",
            expected_approval_receipt_hash=approved.approval_receipt.receipt_hash,
            **_expected(approved),
        )
