from dataclasses import FrozenInstanceError

import pytest

from samchat.assistant.tournament_draft_workbench import (
    ABANDONED,
    DRAFTING,
    FROZEN,
    TournamentDraftPatch,
    TournamentDraftWorkbench,
    TournamentDraftWorkbenchError,
    TournamentProposalTamperedError,
    TournamentReviewInputs,
    abandon_tournament_workbench,
    freeze_tournament_proposal,
    revise_tournament_draft,
    verify_frozen_proposal,
)
from samchat.assistant.tournament_goal_shadow import (
    TournamentSnapshot,
    build_tournament_goal_shadow,
)


CASE_ID = "analyst_case_" + "a" * 32


def _authority_binding():
    return {
        "verified_owner": {
            "id": "00000000-0000-0000-0000-000000000053",
            "nombre": "Alicia Operaciones",
            "activo": True,
        },
        "verified_source_hash": "sha256:" + "1" * 64,
    }


def _shadow(*, unavailable=()):
    source = TournamentSnapshot.from_mapping(
        {
            "id": "tournament-2026",
            "name": "Copa 2026",
            "description": "Edición base",
            "active": True,
            "display_order": 2,
            "etapas": ["Estatal", "Nacional"],
            "categorias": ["Juvenil", "Libre"],
            "form_visibility_areas": ["Operaciones"],
            "source_authority_hash": "sha256:" + "1" * 64,
            "unavailable_components": list(unavailable),
        }
    )
    return build_tournament_goal_shadow(
        source,
        requested_name="Copa 2027",
        goal="Crear Copa 2027 desde Copa 2026",
    )


def _drafting(*, unavailable=()):
    return TournamentDraftWorkbench(shadow=_shadow(unavailable=unavailable))


def test_patch_distinguishes_unset_from_explicit_null():
    current = _drafting()
    revised = revise_tournament_draft(
        current,
        patch=TournamentDraftPatch.from_mapping({"description": None}),
    )

    assert revised.shadow.draft.description is None
    assert revised.shadow.draft.accounting_account is None
    assert revised.shadow.draft.categories == current.shadow.draft.categories
    assert TournamentDraftPatch.from_mapping({}).changed_fields == ()


def test_partial_patch_preserves_omitted_current_revision_fields():
    current = revise_tournament_draft(
        _drafting(),
        patch=TournamentDraftPatch.from_mapping(
            {"description": "Revisada", "categories": ["Juvenil"]}
        ),
    )
    revised = revise_tournament_draft(
        current,
        patch=TournamentDraftPatch.from_mapping({"display_order": 7}),
    )

    assert revised.shadow.draft.description == "Revisada"
    assert revised.shadow.draft.categories == ("Juvenil",)
    assert revised.shadow.draft.display_order == 7


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"id": "forged"}, "Unsupported"),
        ({"name": None}, "cannot be null"),
        ({"name": 2027}, "must be text"),
        ({"active": "true"}, "must be boolean"),
        ({"display_order": True}, "must be an integer"),
        ({"categories": "Juvenil"}, "must be an array"),
        ({"categories": ["Juvenil", 2027]}, "only text"),
    ],
)
def test_patch_rejects_unknown_or_invalid_field_shapes(payload, message):
    with pytest.raises(TournamentDraftWorkbenchError, match=message):
        TournamentDraftPatch.from_mapping(payload)


def test_review_inputs_are_normalized_sorted_and_hash_stable():
    first = TournamentReviewInputs.from_mapping(
        {
            "source_component:media": "  Se cargará después  ",
            "source_component:communications": {"strategy": "reuse"},
        }
    )
    second = TournamentReviewInputs.from_mapping(
        {
            "source_component:communications": {"strategy": "reuse"},
            "source_component:media": "Se cargará después",
        }
    )

    assert first == second
    assert first.keys() == (
        "source_component:communications",
        "source_component:media",
    )
    assert first.to_dict() == second.to_dict()


def test_review_input_null_removes_an_existing_answer():
    inputs = TournamentReviewInputs.from_mapping({"source_component:media": "Después"})

    assert inputs.updated({"source_component:media": None}).items == ()


@pytest.mark.parametrize(
    "value, message",
    [
        ({"start_date": "2027/01/01", "end_date": "2027-02-01"}, "YYYY-MM-DD"),
        ({"start_date": "2027-02-01", "end_date": "2027-01-01"}, "earlier"),
        ({"start_date": "2027-01-01"}, "require end_date"),
        (
            {
                "start_date": "2027-01-01",
                "end_date": "2027-02-01",
                "timezone": "UTC",
            },
            "Unsupported tournament date fields",
        ),
    ],
)
def test_tournament_date_review_input_fails_closed(value, message):
    with pytest.raises(TournamentDraftWorkbenchError, match=message):
        TournamentReviewInputs.from_mapping(
            {"source_component:rich_tournament_dates": value}
        )


def test_valid_dates_are_canonical_and_resolve_missing_input():
    current = _drafting(unavailable=("rich_tournament_dates",))
    revised = revise_tournament_draft(
        current,
        patch=TournamentDraftPatch.from_mapping({}),
        review_input_changes={
            "source_component:rich_tournament_dates": {
                "start_date": "2027-01-01",
                "end_date": "2027-02-01",
            }
        },
    )

    assert revised.review_inputs.to_dict() == {
        "source_component:rich_tournament_dates": {
            "end_date": "2027-02-01",
            "start_date": "2027-01-01",
        }
    }
    assert revised.shadow.missing_information == ()
    assert revised.shadow.plan.steps[-1].status == "pending"


def test_freeze_requires_valid_complete_draft_and_exact_case_binding():
    incomplete = _drafting(unavailable=("media",))
    with pytest.raises(TournamentDraftWorkbenchError, match="missing information"):
        freeze_tournament_proposal(
            incomplete, case_id=CASE_ID, draft_case_version=2, **_authority_binding()
        )

    invalid = revise_tournament_draft(
        _drafting(),
        patch=TournamentDraftPatch.from_mapping({"display_order": -1}),
    )
    with pytest.raises(TournamentDraftWorkbenchError, match="validation errors"):
        freeze_tournament_proposal(
            invalid, case_id=CASE_ID, draft_case_version=2, **_authority_binding()
        )

    with pytest.raises(TournamentDraftWorkbenchError, match="AnalystCase id"):
        freeze_tournament_proposal(
            _drafting(),
            case_id="case-forged",
            draft_case_version=2,
            **_authority_binding(),
        )


def test_freeze_payload_and_hash_are_stable_and_internally_bound():
    first = freeze_tournament_proposal(
        _drafting(), case_id=CASE_ID, draft_case_version=3, **_authority_binding()
    )
    second = freeze_tournament_proposal(
        _drafting(), case_id=CASE_ID, draft_case_version=3, **_authority_binding()
    )

    assert first.state == FROZEN
    assert first.frozen_proposal == second.frozen_proposal
    payload = verify_frozen_proposal(first.frozen_proposal)
    assert payload["case_id"] == CASE_ID
    assert payload["draft_case_version"] == 3
    assert payload["source_authority_hash"] == "sha256:" + "1" * 64
    assert payload["draft"]["base_snapshot_hash"] == payload["source_authority_hash"]
    assert payload["business_diff"]["draft_hash"] == payload["draft_hash"]
    assert payload["operational_writes_allowed"] is False
    assert payload["verified_authority"]["owner"]["activo"] is True
    assert (
        payload["verified_authority"]["source_hash"] == payload["source_authority_hash"]
    )
    assert payload["verified_authority"]["owner"]["id"] == payload["owner_employee_id"]
    assert first.shadow.plan.steps[-1].status == "frozen"


def test_verify_frozen_proposal_detects_payload_hash_and_binding_tamper():
    frozen = freeze_tournament_proposal(
        _drafting(), case_id=CASE_ID, draft_case_version=1, **_authority_binding()
    )
    serialized = frozen.frozen_proposal.to_dict()
    serialized["payload"]["draft"]["name"] = "Manipulado"
    with pytest.raises(TournamentProposalTamperedError, match="hash mismatch"):
        verify_frozen_proposal(serialized)

    serialized = frozen.frozen_proposal.to_dict()
    serialized["payload"]["draft"]["base_snapshot_hash"] = "sha256:" + "2" * 64
    from samchat.assistant.tournament_goal_shadow import canonical_sha256

    serialized["proposal_hash"] = "sha256:" + canonical_sha256(serialized["payload"])
    with pytest.raises(TournamentProposalTamperedError, match="inconsistent"):
        verify_frozen_proposal(serialized)

    serialized = frozen.frozen_proposal.to_dict()
    serialized["payload"]["verified_authority"]["owner"]["id"] = "forged"
    serialized["proposal_hash"] = "sha256:" + canonical_sha256(serialized["payload"])
    with pytest.raises(TournamentProposalTamperedError, match="inconsistent"):
        verify_frozen_proposal(serialized)

    serialized = frozen.frozen_proposal.to_dict()
    serialized["payload"]["verified_authority"]["owner"]["activo"] = False
    serialized["proposal_hash"] = "sha256:" + canonical_sha256(serialized["payload"])
    with pytest.raises(TournamentProposalTamperedError, match="inconsistent"):
        verify_frozen_proposal(serialized)


def test_frozen_and_abandoned_workbenches_cannot_be_revised():
    frozen = freeze_tournament_proposal(
        _drafting(), case_id=CASE_ID, draft_case_version=1, **_authority_binding()
    )
    abandoned = abandon_tournament_workbench(frozen, reason="Ya no aplica")

    assert abandoned.state == ABANDONED
    assert abandoned.frozen_proposal == frozen.frozen_proposal
    assert abandoned.shadow.plan.steps[-1].status == "abandoned"
    for workbench in (frozen, abandoned):
        with pytest.raises(TournamentDraftWorkbenchError, match="can be revised"):
            revise_tournament_draft(
                workbench,
                patch=TournamentDraftPatch.from_mapping({"name": "Otro"}),
            )


def test_abandon_is_idempotent_only_for_identical_reason_and_never_executes():
    current = _drafting()
    abandoned = abandon_tournament_workbench(current, reason="  Duplicado  ")

    assert abandoned.state == ABANDONED
    assert abandoned.abandoned_reason == "Duplicado"
    assert abandoned.execution_status == "not_executed"
    assert abandoned.operational_writes_allowed is False
    assert abandon_tournament_workbench(abandoned, reason="Duplicado") is abandoned
    with pytest.raises(TournamentDraftWorkbenchError, match="already abandoned"):
        abandon_tournament_workbench(abandoned, reason="Otro motivo")


def test_workbench_contract_is_frozen():
    workbench = _drafting()
    assert workbench.state == DRAFTING
    with pytest.raises(FrozenInstanceError):
        workbench.state = FROZEN
