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
    assert decision.reason == "semantic_registry_candidate_matches_work_frame"


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


def test_semantic_registry_rejects_owner_readiness_for_finance_question():
    work_frame = build_work_frame("Que polizas no cuadran?")

    decision = adjudicate_tool_candidate(
        work_frame=work_frame,
        tool="assistant_owner_pack_readiness",
    )

    assert decision.accepted is False
    assert decision.reason == "semantic_registry_candidate_does_not_match_work_frame"


def test_work_turn_trace_is_appended_before_work_frame_for_qna():
    from types import SimpleNamespace

    from samchat.assistant.conversation_service import _with_work_frame_trace

    work_frame = build_work_frame("Que pagos estan pendientes esta semana?")
    response = SimpleNamespace(
        assistant_message="Pagos pendientes: hay 2 solicitudes pendientes.",
        tool_trace=[
            {
                "tool": "receipts.pending_payment_overview",
                "result": {"status": "success", "pending_count": 2},
            }
        ],
    )

    result = _with_work_frame_trace(response, work_frame)

    tools = [trace.get("tool") for trace in result.tool_trace]
    assert tools[0] == "receipts.pending_payment_overview"
    assert "assistant.tool_candidate_adjudicator" in tools
    assert "assistant.response_sufficiency_gate" in tools
    assert "assistant.work_turn_trace" in tools
    assert tools[-1] == "assistant.work_frame"
    assert "Frontera de autoridad" in result.assistant_message


def test_work_turn_renderer_preserves_document_intake_surface():
    from samchat.assistant.response_sufficiency import ResponseSufficiencyResult
    from samchat.assistant.work_turn_renderer import render_work_turn_answer

    work_frame = build_work_frame("Subi este CFDI")
    sufficiency = ResponseSufficiencyResult(
        ok=True,
        reason="controlled_deterministic_surface_already_rendered_safe_output",
        action="allow",
        tool="document_intake_live_wiring",
        diagnostics={},
    )

    message, rendered = render_work_turn_answer(
        current_message="Documento detectado: cfdi_invoice",
        work_frame=work_frame,
        sufficiency=sufficiency,
        tool_trace=[{"document_intake_live_wiring": {"stage": "upload_render"}}],
    )

    assert message == "Documento detectado: cfdi_invoice"
    assert rendered is False
