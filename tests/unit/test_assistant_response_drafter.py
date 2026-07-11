from __future__ import annotations

from samchat.assistant.cognitive_pipeline import (
    CognitiveRuntimeInput,
    run_cognitive_pipeline,
)
from samchat.assistant.response_drafter import draft_response_from_cognitive_envelope
from samchat.assistant.response_strategy import CREATE_INERT_PROPOSAL
from samchat.assistant.self_critique import BLOCKED, SAFE_TO_ANSWER


def _envelope(message: str, *, role: str = "admin", evidence: bool = True):
    tool_traces = [{"assistant_route": {"route": "finance_read"}}] if evidence else []
    return run_cognitive_pipeline(
        CognitiveRuntimeInput(
            user_message=message,
            role=role,
            employee_id="EMP-1",
            tool_traces=tool_traces,
        )
    )


def test_response_drafter_safe_answer_passes_critique() -> None:
    response = draft_response_from_cognitive_envelope(
        envelope=_envelope("Muestra el resumen de gastos", role="finanzas")
    )

    assert response.release_decision == SAFE_TO_ANSWER
    assert response.final_text is not None
    assert "read-only evidence" in response.final_text


def test_response_drafter_returns_clarification_question() -> None:
    response = draft_response_from_cognitive_envelope(
        envelope=_envelope("Puedes revisar esto y arreglarlo?", evidence=False)
    )

    assert response.final_text is not None
    assert response.final_text.startswith("Please clarify:")


def test_response_drafter_returns_inert_proposal_text() -> None:
    envelope = _envelope("Crea una aclaración para este gasto")
    response = draft_response_from_cognitive_envelope(envelope=envelope)

    assert envelope.final_response_mode == CREATE_INERT_PROPOSAL
    assert response.proposal is not None
    assert response.proposal["execution_status"] == "not_executed"
    assert response.final_text is not None
    assert "inert proposal" in response.final_text


def test_response_drafter_blocked_write_request_has_safe_boundary() -> None:
    response = draft_response_from_cognitive_envelope(
        envelope=_envelope("Paga la nómina hoy", role="superadmin", evidence=False)
    )

    assert response.final_text is not None
    assert "human review" in response.final_text


def test_response_drafter_unsafe_draft_is_not_released() -> None:
    response = draft_response_from_cognitive_envelope(
        envelope=_envelope("Muestra el resumen de gastos", role="finanzas"),
        unsafe_draft_override="I executed payment for the invoice.",
    )

    assert response.release_decision == BLOCKED
    assert response.final_text is None
    assert response.required_edits
