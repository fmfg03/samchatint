from __future__ import annotations

from samchat.assistant.proposed_actions import (
    APPROVAL_REQUIRED,
    create_proposed_action,
    proposal_execution_attempt_trace,
)


def test_create_proposed_action_is_inert_and_traceable() -> None:
    proposal = create_proposed_action(
        action_type="finance_review",
        title="Revisar gastos sin CFDI",
        payload={"report_id": "rep-1"},
        source_trace_ref="run-1:tool-2",
    )

    trace = proposal.to_trace()
    assert trace["status"] == "proposed"
    assert trace["approval_boundary"] == APPROVAL_REQUIRED
    assert trace["source_trace_ref"] == "run-1:tool-2"
    assert trace["execution_claimed"] is False
    assert trace["handler_invoked"] is False
    assert trace["external_notification_enqueued"] is False
    assert trace["side_effects_detected"] == 0
    assert "executed" not in trace["status"]


def test_proposal_execution_attempt_fails_closed_when_writes_disabled() -> None:
    proposal = create_proposed_action(
        action_type="cfdi_follow_up",
        title="Pedir aclaración CFDI",
        payload={"documento_id": "doc-1"},
        source_trace_ref="run-2:tool-1",
    )

    trace = proposal_execution_attempt_trace(
        proposal=proposal,
        writes_enabled=False,
    )

    assert trace["decision"] == "deny"
    assert trace["reason"] == "writes_disabled"
    assert trace["handler_invoked"] is False
    assert trace["external_notification_enqueued"] is False
    assert trace["side_effects_detected"] == 0
    assert trace["audit_language"] == "prepared"


def test_proposal_with_writes_enabled_still_requires_approval_shell() -> None:
    proposal = create_proposed_action(
        action_type="report_export_request",
        title="Preparar export",
        payload={"format": "xlsx"},
    )

    trace = proposal_execution_attempt_trace(
        proposal=proposal,
        writes_enabled=True,
    )

    assert trace["decision"] == "pending"
    assert trace["reason"] == "approval_required"
    assert trace["handler_invoked"] is False
    assert trace["side_effects_detected"] == 0
    assert trace["audit_language"] == "proposed"
