from pathlib import Path

from samchat.assistant.business_diff_preview import (
    APPROVAL_REQUIRED,
    CREATE_ENTITY_FOLDER,
    CREATE_NATIONAL_PHASE_FOLDER,
    GENERATE_ACTIVATION_REPORT,
    MISSING_EVIDENCE,
    NOT_EXECUTED,
    PREVIEW_ONLY,
    SUPPORTED,
    UPDATE_ENTITY_FOLDER,
    create_owner_prompt_business_diff_preview,
    evaluate_preview_set,
    preview_contains_execution_claim,
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


def _assert_preview_safety(preview) -> None:
    assert preview.approval_required is True
    assert preview.blocked_reason == APPROVAL_REQUIRED
    assert preview.execution_status == NOT_EXECUTED
    assert preview.writes_attempted == 0
    assert preview.side_effects_detected == 0
    assert preview.audit_language == PREVIEW_ONLY
    assert preview_contains_execution_claim(preview) is False


def test_ai_owner_001_builds_entity_folder_create_preview() -> None:
    prompt = _prompt("AI-OWNER-001")
    preview = create_owner_prompt_business_diff_preview(prompt)

    assert preview.operation_type == CREATE_ENTITY_FOLDER
    assert preview.target["entity"] == "Jalisco"
    assert preview.target["folder_scope"] == "entity"
    assert preview.target["tournament_hint"] == "beisbol"
    assert any(
        change.field == "expected_teams"
        and change.status == MISSING_EVIDENCE
        for change in preview.proposed_changes
    )
    assert "team" in preview.missing_evidence
    _assert_preview_safety(preview)


def test_ai_owner_013_builds_national_phase_create_preview() -> None:
    prompt = _prompt("AI-OWNER-013")
    preview = create_owner_prompt_business_diff_preview(prompt)

    assert preview.operation_type == CREATE_NATIONAL_PHASE_FOLDER
    assert preview.target["folder_scope"] == "national_phase"
    assert any(
        change.field == "contracted_hotels_bed_nights"
        for change in preview.proposed_changes
    )
    assert "marketing" in preview.missing_evidence
    _assert_preview_safety(preview)


def test_ai_owner_025_builds_activation_report_preview() -> None:
    prompt = _prompt("AI-OWNER-025")
    preview = create_owner_prompt_business_diff_preview(prompt)

    assert preview.operation_type == GENERATE_ACTIVATION_REPORT
    assert preview.target["report_type"] == "brand_activation"
    assert any(
        change.field == "photographic_evidence"
        and change.status == MISSING_EVIDENCE
        for change in preview.proposed_changes
    )
    assert "media" in preview.missing_evidence
    _assert_preview_safety(preview)


def test_ai_owner_028_builds_entity_folder_update_preview() -> None:
    prompt = _prompt("AI-OWNER-028")
    preview = create_owner_prompt_business_diff_preview(prompt)

    assert preview.operation_type == UPDATE_ENTITY_FOLDER
    assert preview.target["entity"] == "Jalisco"
    assert any(
        change.field == "real_teams"
        and change.status == MISSING_EVIDENCE
        for change in preview.proposed_changes
    )
    assert "authority_preview" in preview.missing_evidence
    _assert_preview_safety(preview)


def test_ai_owner_018_medical_preview_does_not_invent_facts() -> None:
    prompt = _prompt("AI-OWNER-018")
    preview = create_owner_prompt_business_diff_preview(prompt)

    assert preview.execution_status == NOT_EXECUTED
    assert "medical/event_incident" in preview.missing_evidence
    assert any(
        change.field == "medical_services_description"
        and change.status == MISSING_EVIDENCE
        for change in preview.proposed_changes
    )
    assert any(
        change.field == "accidents_with_transfers"
        and change.status == MISSING_EVIDENCE
        for change in preview.proposed_changes
    )
    _assert_preview_safety(preview)


def test_preview_can_mark_available_field_without_executing() -> None:
    preview = create_owner_prompt_business_diff_preview(
        _prompt("AI-OWNER-001"),
        available_evidence={
            "fields": {
                "entity_name": "Jalisco",
                "expected_teams": 12,
            }
        },
    )

    supported = {
        change.field: change
        for change in preview.proposed_changes
        if change.status == SUPPORTED
    }
    assert supported["entity_name"].proposed_value == "Jalisco"
    assert supported["expected_teams"].proposed_value == 12
    assert "entity_name" in preview.found_evidence
    _assert_preview_safety(preview)


def test_preview_set_covers_full_owner_eval_without_execution_claims() -> None:
    summary = evaluate_preview_set(_prompts())

    assert summary["total"] == 30
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["execution_claims_detected"] == 0
    assert summary["operation_counts"][CREATE_ENTITY_FOLDER] >= 1
    assert summary["operation_counts"][CREATE_NATIONAL_PHASE_FOLDER] >= 1
    assert summary["operation_counts"][GENERATE_ACTIVATION_REPORT] >= 1
    assert summary["operation_counts"][UPDATE_ENTITY_FOLDER] >= 1
