from pathlib import Path

from samchat.assistant.owner_needs_eval import (
    EVIDENCE_DATA_MISSING,
    EXPECTED_LIMITATION,
    GAP_TYPES,
    PASS,
    PASS_WITH_CLASSIFIED_GAPS,
    assess_owner_needs_prompt,
    build_owner_evidence_gap_response,
    evaluate_owner_needs_prompts,
    parse_owner_needs_eval_set,
)


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


def _prompts():
    return parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))


def _prompt(prompt_id: str):
    for prompt in _prompts():
        if prompt.prompt_id == prompt_id:
            return prompt
    raise AssertionError(f"missing prompt {prompt_id}")


def test_owner_needs_eval_set_executes_all_30_prompts() -> None:
    prompts = _prompts()
    summary = evaluate_owner_needs_prompts(prompts)

    assert len(prompts) == 30
    assert summary["total"] == 30
    assert summary["final_decision"] == PASS_WITH_CLASSIFIED_GAPS
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0
    assert summary["gap_counts"][EVIDENCE_DATA_MISSING] >= 1


def test_owner_needs_gap_records_use_approved_categories() -> None:
    prompts = _prompts()
    summary = evaluate_owner_needs_prompts(prompts)

    for assessment in summary["assessments"]:
        for gap in assessment["gaps"]:
            assert gap["gap_type"] in GAP_TYPES
            assert gap["prompt_id"]
            assert gap["summary"]
            assert gap["current_result"]
            assert gap["probable_cause"]
            assert gap["requires"]
            assert gap["decision"]


def test_ai_owner_018_declares_missing_medical_evidence() -> None:
    assessment = assess_owner_needs_prompt(_prompt("AI-OWNER-018"))
    response = build_owner_evidence_gap_response(assessment)

    assert assessment.status == PASS_WITH_CLASSIFIED_GAPS
    assert EVIDENCE_DATA_MISSING in {
        gap.gap_type for gap in assessment.gaps
    }
    assert "medical/event_incident" in assessment.evidence_missing
    assert "document" in assessment.evidence_missing
    assert "No tengo evidencia concreta cargada" in response["answer"]
    assert "medical/event_incident" in response["answer"]
    assert response["writes_attempted"] == 0
    assert response["side_effects_detected"] == 0
    assert response["audit_language"] == "proposed_or_missing_evidence_only"


def test_owner_needs_conceptual_prompt_can_pass_from_canon_only() -> None:
    assessment = assess_owner_needs_prompt(_prompt("AI-OWNER-002"))
    response = build_owner_evidence_gap_response(assessment)

    assert assessment.status == PASS
    assert assessment.evidence_missing == []
    assert response["evidence_missing"] == []
    assert "canon versionado" in response["answer"]
    assert response["writes_attempted"] == 0


def test_owner_needs_create_update_prompts_require_preview_boundary() -> None:
    create_assessment = assess_owner_needs_prompt(_prompt("AI-OWNER-001"))
    update_assessment = assess_owner_needs_prompt(_prompt("AI-OWNER-028"))

    for assessment in (create_assessment, update_assessment):
        gap_types = {gap.gap_type for gap in assessment.gaps}
        assert EXPECTED_LIMITATION in gap_types
        assert assessment.status == PASS_WITH_CLASSIFIED_GAPS
        assert "preview" in assessment.recommended_next_action.lower()
        response = build_owner_evidence_gap_response(assessment)
        assert "ejecut" not in str(response).lower()
        assert response["writes_attempted"] == 0


def test_operational_live_evidence_reduces_team_prompt_gaps() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-003"),
        available_evidence_by_prompt={
            "AI-OWNER-003": {"tournament", "team"},
        },
    )

    assert assessment.status == PASS_WITH_CLASSIFIED_GAPS
    assert "tournament" in assessment.evidence_found
    assert "team" in assessment.evidence_found
    assert "memory" in assessment.evidence_missing
    assert "tournament" not in assessment.evidence_missing
    assert "team" not in assessment.evidence_missing
    assert assessment.writes_attempted == 0
    assert assessment.side_effects_detected == 0


def test_operational_live_evidence_reduces_player_prompt_gaps() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-004"),
        available_evidence_sources={"player", "team"},
    )
    response = build_owner_evidence_gap_response(assessment)

    assert assessment.status == PASS
    assert assessment.evidence_missing == []
    assert {"player", "team"} <= set(assessment.evidence_found)
    assert "canon versionado" in response["answer"]
    assert response["writes_attempted"] == 0


def test_partial_operational_evidence_keeps_remaining_round_gaps() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-005"),
        available_evidence_by_prompt={
            "AI-OWNER-005": {"team"},
        },
    )

    assert assessment.status == PASS_WITH_CLASSIFIED_GAPS
    assert "team" in assessment.evidence_found
    assert "tournament" in assessment.evidence_missing
    assert "team" not in assessment.evidence_missing
    assert assessment.gaps


def test_create_prompt_still_requires_preview_with_live_operational_evidence() -> None:
    prompt = _prompt("AI-OWNER-001")
    assessment = assess_owner_needs_prompt(
        prompt,
        available_evidence_by_prompt={
            prompt.prompt_id: {
                "tournament",
                "entity",
                "team",
                "player",
                "finance",
                "memory",
            },
        },
    )
    response = build_owner_evidence_gap_response(assessment)

    assert assessment.status == PASS_WITH_CLASSIFIED_GAPS
    assert assessment.evidence_missing == []
    assert EXPECTED_LIMITATION in {gap.gap_type for gap in assessment.gaps}
    assert "preview/diff" in response["answer"]
    assert response["writes_attempted"] == 0
    assert response["side_effects_detected"] == 0


def test_evaluate_owner_needs_prompts_accepts_operational_evidence_map() -> None:
    prompts = [
        _prompt("AI-OWNER-003"),
        _prompt("AI-OWNER-004"),
        _prompt("AI-OWNER-009"),
    ]
    summary = evaluate_owner_needs_prompts(
        prompts,
        available_evidence_by_prompt={
            "AI-OWNER-003": {"tournament", "team", "memory"},
            "AI-OWNER-004": {"player", "team"},
            "AI-OWNER-009": {"tournament", "team"},
        },
    )

    assert summary["total"] == 3
    assert summary["final_decision"] == PASS
    assert summary["status_counts"][PASS] == 3
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0


def test_financial_live_evidence_can_satisfy_operator_payment_prompt() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-010"),
        available_evidence_by_prompt={
            "AI-OWNER-010": {"finance", "document", "sql"},
        },
    )

    assert assessment.status == PASS
    assert assessment.evidence_missing == []
    assert {"finance", "document", "sql"} <= set(assessment.evidence_found)
    assert assessment.writes_attempted == 0
    assert assessment.side_effects_detected == 0


def test_financial_live_evidence_can_satisfy_equipment_cost_prompt() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-011"),
        available_evidence_by_prompt={
            "AI-OWNER-011": {"finance", "document", "inventory/equipment"},
        },
    )

    assert assessment.status == PASS
    assert assessment.evidence_missing == []
    assert "inventory/equipment" in assessment.evidence_found
    assert "finance" in assessment.evidence_found
    assert "document" in assessment.evidence_found


def test_partial_financial_live_evidence_keeps_media_or_memory_gaps() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-012"),
        available_evidence_by_prompt={
            "AI-OWNER-012": {"finance", "document"},
        },
    )

    assert assessment.status == PASS_WITH_CLASSIFIED_GAPS
    assert "finance" in assessment.evidence_found
    assert "document" in assessment.evidence_found
    assert "media" in assessment.evidence_missing
    assert "memory" in assessment.evidence_missing
    assert "finance" not in assessment.evidence_missing
    assert "document" not in assessment.evidence_missing


def test_financial_live_evidence_can_satisfy_hotel_prompt() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-015"),
        available_evidence_by_prompt={
            "AI-OWNER-015": {"document", "finance"},
        },
    )
    response = build_owner_evidence_gap_response(assessment)

    assert assessment.status == PASS
    assert assessment.evidence_missing == []
    assert {"document", "finance"} <= set(assessment.evidence_found)
    assert "canon versionado" in response["answer"]
    assert response["side_effects_detected"] == 0


def test_financial_live_evidence_can_satisfy_provider_payment_prompts() -> None:
    prompts = [_prompt("AI-OWNER-020"), _prompt("AI-OWNER-022")]
    summary = evaluate_owner_needs_prompts(
        prompts,
        available_evidence_by_prompt={
            "AI-OWNER-020": {"finance", "document", "provider"},
            "AI-OWNER-022": {"finance", "document", "provider"},
        },
    )

    assert summary["total"] == 2
    assert summary["final_decision"] == PASS
    assert summary["status_counts"][PASS] == 2
    assert summary["writes_attempted"] == 0
    assert summary["side_effects_detected"] == 0


def test_mixed_owner_comparison_can_pass_with_operational_and_finance_evidence() -> None:
    assessment = assess_owner_needs_prompt(
        _prompt("AI-OWNER-029"),
        available_evidence_by_prompt={
            "AI-OWNER-029": {"tournament", "team", "player", "finance"},
        },
    )

    assert assessment.status == PASS
    assert assessment.evidence_missing == []
    assert {"tournament", "team", "player", "finance"} <= set(
        assessment.evidence_found
    )
