from __future__ import annotations

from samchat.assistant.decision_rubric import evaluate_response_options
from samchat.assistant.hypothesis_engine import generate_hypotheses
from samchat.assistant.response_strategy import (
    ANSWER_NOW,
    ASK_CLARIFYING_QUESTION,
    CREATE_INERT_PROPOSAL,
    ESCALATE_TO_HUMAN_REVIEW,
    REFUSE_OR_BLOCK,
    select_response_strategy,
)
from samchat.assistant.situation_model import build_situation_model


def _strategy(message: str, *, role: str = "admin", employee_id: str = "EMP-1"):
    model = build_situation_model(
        user_message=message,
        role=role,
        employee_id=employee_id,
        tool_traces=[{"assistant_route": {"route": "finance_read"}}],
    )
    hypotheses = generate_hypotheses(model)
    rubric = evaluate_response_options(model=model, hypotheses=hypotheses)
    return select_response_strategy(
        model=model,
        hypotheses=hypotheses,
        rubric=rubric,
    )


def test_response_strategy_readonly_answer() -> None:
    strategy = _strategy("Muestra el resumen de gastos", role="finanzas")

    assert strategy.strategy == ANSWER_NOW
    assert "finance.ops.read" in strategy.allowed_capabilities
    assert "write_execution" in strategy.denied_capabilities


def test_response_strategy_ambiguous_clarification() -> None:
    strategy = _strategy("Puedes revisar esto y arreglarlo?")

    assert strategy.strategy == ASK_CLARIFYING_QUESTION
    assert "read-only answer" in strategy.reason


def test_response_strategy_action_request_creates_inert_proposal_path() -> None:
    strategy = _strategy("Crea una aclaración para este gasto")

    assert strategy.strategy == CREATE_INERT_PROPOSAL
    assert strategy.proposal_boundary is not None
    assert strategy.proposal_boundary["status"] == "proposed"
    assert strategy.proposal_boundary["handler_invoked"] is False
    assert strategy.proposal_boundary["receipt_status"] == "not_executed"
    assert "direct_action_execution" in strategy.denied_capabilities


def test_response_strategy_write_request_uses_blocked_or_proposal_boundary() -> None:
    strategy = _strategy("Actualiza el gasto REF-1", role="admin")

    assert strategy.strategy in {CREATE_INERT_PROPOSAL, REFUSE_OR_BLOCK}
    assert "write_execution" in strategy.denied_capabilities
    assert "do_not_claim_write_execution" in strategy.wording_constraints


def test_response_strategy_unknown_role_escalates_to_human_review() -> None:
    strategy = _strategy("Muestra el resumen de gastos", role="externo")

    assert strategy.strategy == ESCALATE_TO_HUMAN_REVIEW
    assert strategy.reason == "unknown_role_requires_human_review"
    assert "unknown_role_read_capabilities" in strategy.denied_capabilities


def test_response_strategy_high_risk_claim_escalates_or_clarifies() -> None:
    strategy = _strategy("Confirma al cliente que ya quedó el pago", role="superadmin")

    assert strategy.strategy in {ESCALATE_TO_HUMAN_REVIEW, ASK_CLARIFYING_QUESTION}
    assert "avoid_operational_instructions_for_high_risk_action" in strategy.wording_constraints
