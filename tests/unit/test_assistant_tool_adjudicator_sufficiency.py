from samchat.assistant.response_sufficiency import (
    evaluate_response_sufficiency,
    render_sufficiency_gap_answer,
)
from samchat.assistant.tool_adjudicator import adjudicate_tool_candidate
from samchat.assistant.work_frame import build_work_frame


def test_payment_evidence_rejects_pending_payment_tool():
    work_frame = build_work_frame("Que evidencia tenemos de pagos hechos en agosto?")

    decision = adjudicate_tool_candidate(
        work_frame=work_frame,
        tool="receipts.pending_payment_overview",
    )

    assert decision.accepted is False
    assert decision.reason == "pending_payment_queue_cannot_answer_historical_payment_evidence"
    assert "payment_receipts" in decision.required_evidence


def test_pending_payment_question_accepts_pending_payment_tool():
    work_frame = build_work_frame("Que pagos estan pendientes esta semana?")

    decision = adjudicate_tool_candidate(
        work_frame=work_frame,
        tool="receipts.pending_payment_overview",
    )

    assert decision.accepted is True
    assert decision.reason == "finance_candidate_matches_work_frame"


def test_sufficiency_blocks_zero_pending_as_payment_evidence():
    work_frame = build_work_frame("Que evidencia tenemos de pagos hechos en agosto?")

    result = evaluate_response_sufficiency(
        work_frame=work_frame,
        assistant_message="Pagos pendientes\nHay 0 solicitudes pendientes por $0.00.",
        tool_trace=[
            {
                "tool": "receipts.pending_payment_overview",
                "result": {"status": "success", "pending_count": 0},
            }
        ],
    )

    assert result.ok is False
    assert result.action == "replace_with_gap_answer"
    assert "pending_payment" in result.reason
    fallback = render_sufficiency_gap_answer(work_frame=work_frame, result=result)
    assert "No tengo evidencia suficiente" in fallback
    assert "pending_payment_queue" in fallback


def test_sufficiency_allows_supported_owner_gap_answer():
    work_frame = build_work_frame("Que evidencia tenemos de pagos o apoyos?")

    result = evaluate_response_sufficiency(
        work_frame=work_frame,
        assistant_message=(
            "No hay dato soportado para Ayudas y pagos sucesivos al operador. "
            "No hay evidencia viva suficiente. No ejecute cambios."
        ),
        tool_trace=[
            {
                "tool": "assistant_owner_variable_query",
                "result": {"status": "missing"},
            }
        ],
    )

    assert result.ok is True


def test_owner_readiness_requires_readiness_or_gaps():
    work_frame = build_work_frame("ya tenemos datos para el dueno?")

    result = evaluate_response_sufficiency(
        work_frame=work_frame,
        assistant_message="Tenemos datos cargados.",
        tool_trace=[
            {
                "tool": "assistant_owner_pack_readiness",
                "result": {"status": "ok"},
            }
        ],
    )

    assert result.ok is False
    assert result.reason == "owner_readiness_answer_missing_readiness_or_gaps"
