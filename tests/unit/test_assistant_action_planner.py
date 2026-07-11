from __future__ import annotations

from samchat.assistant.action_planner import plan_inert_action
from samchat.assistant.cognitive_pipeline import (
    CognitiveRuntimeInput,
    run_cognitive_pipeline,
)
from samchat.assistant.response_strategy import (
    ASK_CLARIFYING_QUESTION,
    CREATE_INERT_PROPOSAL,
)


def test_action_planner_finance_followup_creates_inert_proposal() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Crea una aclaración para este gasto sin CFDI",
            role="admin",
            employee_id="EMP-1",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    plan = plan_inert_action(envelope=envelope)

    assert plan.decision == CREATE_INERT_PROPOSAL
    assert plan.execution_status == "not_executed"
    assert plan.writes_required is True
    assert plan.writes_enabled is False
    assert plan.handler_invoked is False


def test_action_planner_missing_evidence_asks_clarification() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Puedes revisar esto y arreglarlo?",
            role="admin",
            employee_id="EMP-2",
        )
    )

    plan = plan_inert_action(envelope=envelope)

    assert plan.decision == ASK_CLARIFYING_QUESTION
    assert plan.next_human_step == "provide_missing_context_or_choose_readonly_answer"


def test_action_planner_marks_approval_required_explicitly() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Crea seguimiento para este gasto",
            role="admin",
            employee_id="EMP-3",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    plan = plan_inert_action(envelope=envelope)

    assert plan.approval_boundary == "human_approval_required"
    assert plan.missing_approvals == ["human_approval_required"]


def test_action_planner_writes_disabled_blocks_execution() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Actualiza el gasto REF-1",
            role="admin",
            employee_id="EMP-4",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    plan = plan_inert_action(envelope=envelope)

    assert plan.writes_enabled is False
    assert plan.execution_status == "not_executed"
    assert "write_execution" in plan.blocked_capabilities


def test_action_planner_never_enqueues_external_notification() -> None:
    envelope = run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message="Crea una aclaración para este gasto",
            role="admin",
            employee_id="EMP-5",
            tool_traces=[{"assistant_route": {"route": "finance_read"}}],
        )
    )

    plan = plan_inert_action(envelope=envelope)
    trace = plan.to_trace()

    assert trace["external_notification_enqueued"] is False
    assert trace["side_effects_detected"] == 0
    assert ("executed " + "successfully") not in repr(trace).lower()
