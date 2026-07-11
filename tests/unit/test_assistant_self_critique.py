from __future__ import annotations

from samchat.assistant.self_critique import (
    BLOCKED,
    NEEDS_REVISION,
    SAFE_TO_ANSWER,
    critique_assistant_draft,
)


def test_self_critique_safe_readonly_answer_passes() -> None:
    result = critique_assistant_draft(
        draft="From the available read-only evidence, the report is missing one CFDI.",
        evidence=[{"kind": "assistant_route"}],
    )

    assert result.passed is True
    assert result.final_release_decision == SAFE_TO_ANSWER


def test_self_critique_payment_execution_claim_fails() -> None:
    result = critique_assistant_draft(draft="I executed payment for the invoice.")

    assert result.passed is False
    assert result.final_release_decision == BLOCKED
    assert result.issues[0].code == "unsupported_execution_claim"


def test_self_critique_writes_enabled_claim_fails() -> None:
    result = critique_assistant_draft(draft="Writes are enabled, so I can update it.")

    assert result.final_release_decision == BLOCKED
    assert any(issue.code == "writes_or_runtime_implied" for issue in result.issues)


def test_self_critique_all_providers_isolated_overclaim_fails_revision() -> None:
    result = critique_assistant_draft(draft="All providers isolated for this path.")

    assert result.final_release_decision == NEEDS_REVISION
    assert any(issue.code == "provider_isolation_overclaim" for issue in result.issues)


def test_self_critique_soak_rerun_fails_when_only_artifact_evidence_exists() -> None:
    result = critique_assistant_draft(
        draft="The soak " + "rerun passed.",
        evidence=[{"kind": "artifact"}],
    )

    assert result.final_release_decision == BLOCKED
    assert any(issue.code == "soak_rerun_overclaim" for issue in result.issues)


def test_self_critique_proposed_action_wording_passes_only_when_inert() -> None:
    result = critique_assistant_draft(
        draft="I prepared an inert proposal for human review; it was not executed.",
        proposal_boundary={
            "status": "proposed",
            "receipt_status": "not_executed",
            "handler_invoked": False,
        },
    )

    assert result.final_release_decision == SAFE_TO_ANSWER


def test_self_critique_missing_evidence_triggers_revision() -> None:
    result = critique_assistant_draft(
        draft="The adjustment appears ready based on available context.",
        missing_evidence=["production_evidence"],
    )

    assert result.final_release_decision == NEEDS_REVISION
    assert any(issue.code == "missing_evidence" for issue in result.issues)
