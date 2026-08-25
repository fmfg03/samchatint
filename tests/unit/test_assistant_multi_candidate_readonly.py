from samchat.assistant.multi_candidate_readonly import (
    ReadOnlyCandidateResponse,
    evaluate_readonly_candidates,
)
from samchat.assistant.work_frame import build_work_frame


def test_multi_candidate_rejects_wrong_pending_queue_and_selects_supported_owner_gap():
    work_frame = build_work_frame("Que evidencia tenemos de pagos hechos en agosto?")

    selection = evaluate_readonly_candidates(
        work_frame=work_frame,
        candidates=[
            ReadOnlyCandidateResponse(
                tool="receipts.pending_payment_overview",
                label="Pending payments",
                assistant_message="Pagos pendientes\nHay 0 solicitudes pendientes por $0.00.",
                tool_trace=[
                    {
                        "tool": "receipts.pending_payment_overview",
                        "result": {"status": "success", "pending_count": 0},
                    }
                ],
            ),
            ReadOnlyCandidateResponse(
                tool="assistant_owner_variable_query",
                label="Owner variable Q&A",
                assistant_message=(
                    "No hay dato soportado para pagos hechos en agosto. "
                    "No hay evidencia viva suficiente. No ejecute cambios."
                ),
                tool_trace=[
                    {
                        "tool": "assistant_owner_variable_query",
                        "result": {"status": "missing"},
                    }
                ],
            ),
        ],
    )

    assert selection.selected is not None
    assert selection.selected.tool == "assistant_owner_variable_query"
    assert selection.reason == "selected_highest_scoring_sufficient_read_only_candidate"
    assert len(selection.evaluations) == 2
    assert selection.evaluations[0].sufficiency.ok is False
    assert selection.evaluations[1].sufficiency.ok is True
    assert "No hay dato soportado" in selection.rendered_message

    trace = selection.trace()
    assert trace["tool"] == "assistant.multi_candidate_readonly"
    assert trace["result"]["candidate_count"] == 2
    assert trace["result"]["selected_tool"] == "assistant_owner_variable_query"
    assert trace["result"]["read_only"] is True
    assert trace["result"]["writes_attempted"] is False


def test_multi_candidate_fails_closed_when_no_candidate_is_sufficient():
    work_frame = build_work_frame("ya tenemos datos para el dueno?")

    selection = evaluate_readonly_candidates(
        work_frame=work_frame,
        candidates=[
            ReadOnlyCandidateResponse(
                tool="assistant_owner_pack_readiness",
                label="Owner Pack readiness",
                assistant_message="Tenemos datos cargados.",
                tool_trace=[
                    {
                        "tool": "assistant_owner_pack_readiness",
                        "result": {"status": "ok"},
                    }
                ],
            )
        ],
    )

    assert selection.selected is None
    assert selection.reason == "no_candidate_sufficient"
    assert "No tengo evidencia suficiente" in selection.rendered_message
    assert "owner_pack_inventory" in selection.rendered_message
