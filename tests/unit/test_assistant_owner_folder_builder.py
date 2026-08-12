from pathlib import Path

from samchat.assistant.owner_folder_builder import (
    ACTIVATION_REPORT_PROPOSAL,
    ENTITY_FOLDER_PROPOSAL,
    FOLDER_BUILD_PLAN_PROPOSAL,
    FOLDER_PROPOSAL_ONLY,
    MISSING_EVIDENCE_STATUS,
    NATIONAL_PHASE_FOLDER_PROPOSAL,
    build_owner_prompt_folder_proposal,
    evaluate_owner_folder_proposal_set,
    folder_proposal_contains_execution_claim,
)
from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


def _prompts():
    return parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))


def _prompt(prompt_id: str):
    for prompt in _prompts():
        if prompt.prompt_id == prompt_id:
            return prompt
    raise AssertionError(f"missing prompt {prompt_id}")


def _all_fields(proposal):
    return [field for section in proposal.sections for field in section.fields]


def _field(proposal, field_name: str):
    for field in _all_fields(proposal):
        if field.field == field_name:
            return field
    raise AssertionError(f"missing field {field_name}")


def _assert_proposal_safety(proposal) -> None:
    assert proposal.approval_required is True
    assert proposal.execution_status == "not_executed"
    assert proposal.writes_attempted == 0
    assert proposal.side_effects_detected == 0
    assert proposal.audit_language == FOLDER_PROPOSAL_ONLY
    assert folder_proposal_contains_execution_claim(proposal) is False


def test_ai_owner_001_builds_entity_folder_proposal() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-001"))

    assert proposal.folder_id.startswith("ofp_")
    assert proposal.folder_type == ENTITY_FOLDER_PROPOSAL
    assert proposal.target["entity"] == "Jalisco"
    assert proposal.target["tournament_hint"] == "beisbol"
    assert {section.section_id for section in proposal.sections} >= {
        "operations",
        "finance",
        "marketing_materiality",
    }
    expected_teams = _field(proposal, "expected_teams")
    assert expected_teams.status == MISSING_EVIDENCE_STATUS
    assert expected_teams.value is None
    assert "team" in proposal.missing_evidence
    _assert_proposal_safety(proposal)


def test_entity_folder_proposal_uses_owner_friendly_field_labels() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-001"))

    assert _field(proposal, "entity_name").label == "Nombre de la entidad"
    assert _field(proposal, "expected_teams").label == (
        "Equipos esperados por categoria/genero"
    )
    assert _field(proposal, "operator_payments").label == (
        "Ayudas y pagos sucesivos al operador"
    )


def test_national_phase_proposal_uses_owner_requested_finance_labels() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-013"))

    assert _field(proposal, "contracted_hotels_bed_nights").label == (
        "Hoteles y camas-noche contratadas"
    )
    assert _field(proposal, "provider_payments").label == (
        "Pagos a proveedores de finales"
    )
    assert _field(proposal, "medical_and_insurance_costs").label == (
        "Costos medicos y seguros"
    )


def test_ai_owner_013_builds_national_phase_folder_proposal() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-013"))

    assert proposal.folder_type == NATIONAL_PHASE_FOLDER_PROPOSAL
    assert proposal.target["folder_scope"] == "national_phase"
    assert {section.section_id for section in proposal.sections} >= {
        "operations",
        "finance",
        "marketing",
    }
    for field_name in (
        "contracted_hotels_bed_nights",
        "contracted_meals",
        "sports_venue_and_fields",
        "medical_services_description",
        "accidents_with_transfers",
    ):
        assert _field(proposal, field_name).status == MISSING_EVIDENCE_STATUS
    assert "marketing" in proposal.missing_evidence
    _assert_proposal_safety(proposal)


def test_ai_owner_025_builds_activation_report_proposal() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-025"))

    assert proposal.folder_type == ACTIVATION_REPORT_PROPOSAL
    assert proposal.target["report_type"] == "brand_activation"
    assert {section.section_id for section in proposal.sections} >= {
        "activation",
        "photographic_evidence",
    }
    assert _field(proposal, "photographic_evidence").status == (MISSING_EVIDENCE_STATUS)
    assert "media" in proposal.missing_evidence
    _assert_proposal_safety(proposal)


def test_ai_owner_028_update_remains_proposal_only() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-028"))

    assert proposal.folder_type == ENTITY_FOLDER_PROPOSAL
    assert proposal.target["entity"] == "Jalisco"
    assert _field(proposal, "real_teams").status == MISSING_EVIDENCE_STATUS
    assert "authority_preview" in proposal.missing_evidence
    assert proposal.audit_language == FOLDER_PROPOSAL_ONLY
    _assert_proposal_safety(proposal)


def test_ai_owner_018_medical_folder_proposal_does_not_invent_facts() -> None:
    proposal = build_owner_prompt_folder_proposal(_prompt("AI-OWNER-018"))

    assert proposal.folder_type == FOLDER_BUILD_PLAN_PROPOSAL
    assert "medical/event_incident" in proposal.missing_evidence
    assert proposal.evidence_summary["missing_field_count"] >= 1
    for field_name in (
        "medical_services_description",
        "accidents_with_transfers",
        "medical_and_insurance_costs",
    ):
        assert _field(proposal, field_name).status == MISSING_EVIDENCE_STATUS
    _assert_proposal_safety(proposal)


def test_folder_proposal_can_mark_supported_fields_without_executing() -> None:
    proposal = build_owner_prompt_folder_proposal(
        _prompt("AI-OWNER-001"),
        available_evidence={
            "fields": {
                "entity_name": "Jalisco",
                "expected_teams": 12,
            }
        },
    )

    assert _field(proposal, "entity_name").value == "Jalisco"
    assert _field(proposal, "entity_name").status == "supported"
    assert _field(proposal, "expected_teams").value == 12
    assert proposal.evidence_summary["supported_field_count"] == 2
    _assert_proposal_safety(proposal)


def test_full_owner_eval_set_builds_folder_proposals_without_writes() -> None:
    summary = evaluate_owner_folder_proposal_set(_prompts())

    assert summary["total"] == 30
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0
    assert summary["folder_type_counts"][ENTITY_FOLDER_PROPOSAL] >= 1
    assert summary["folder_type_counts"][NATIONAL_PHASE_FOLDER_PROPOSAL] >= 1
    assert summary["folder_type_counts"][ACTIVATION_REPORT_PROPOSAL] >= 1
    assert summary["folder_type_counts"][FOLDER_BUILD_PLAN_PROPOSAL] >= 1
