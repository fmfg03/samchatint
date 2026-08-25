from __future__ import annotations

from samchat.assistant.executive_regression_suite import (
    evaluate_executive_regression_case,
    executive_regression_cases,
)
from samchat.assistant.multi_candidate_readonly import (
    ReadOnlyCandidateResponse,
    evaluate_readonly_candidates,
)
from samchat.assistant.work_frame import build_work_frame


def _case(case_id: str):
    for item in executive_regression_cases():
        if item.case_id == case_id:
            return item
    raise AssertionError(f"missing case {case_id}")


def test_executive_regression_suite_covers_owner_and_finance_questions() -> None:
    cases = executive_regression_cases()
    ids = {case.case_id for case in cases}

    assert len(cases) >= 7
    assert "OWNER-READINESS-001" in ids
    assert "OWNER-PAYMENT-EVIDENCE-001" in ids
    assert "FIN-ACCOUNTING-LOADED-001" in ids
    assert "FIN-PAYMENT-RUN-001" in ids
    assert "FIN-CLOSE-BLOCKERS-001" in ids
    assert "FIN-CFDI-GAPS-001" in ids
    assert all(case.expected_tools for case in cases)


def test_owner_payment_evidence_regression_rejects_pending_payment_shortcut() -> None:
    case = _case("OWNER-PAYMENT-EVIDENCE-001")

    verdict = evaluate_executive_regression_case(
        case=case,
        assistant_message="Pagos pendientes\nHay 0 solicitudes pendientes por $0.00.",
        tool_trace=[
            {
                "tool": "receipts.pending_payment_overview",
                "result": {"status": "success", "pending_count": 0},
            }
        ],
    )

    assert verdict.ok is False
    assert "forbidden_tool:receipts.pending_payment_overview" in verdict.failures
    assert any(item.startswith("forbidden_term:") for item in verdict.failures)
    assert any(item.startswith("sufficiency:") for item in verdict.failures)


def test_owner_payment_evidence_regression_allows_explicit_supported_gap() -> None:
    case = _case("OWNER-PAYMENT-EVIDENCE-001")

    verdict = evaluate_executive_regression_case(
        case=case,
        assistant_message=(
            "No hay dato soportado para pagos hechos en agosto. "
            "No hay evidencia viva suficiente. No ejecuté cambios."
        ),
        tool_trace=[
            {
                "tool": "assistant_owner_variable_query",
                "result": {"status": "missing", "question_type": "operator_payments"},
            }
        ],
    )

    assert verdict.ok is True


def test_finance_accounting_loaded_regression_rejects_unicode_loop() -> None:
    case = _case("FIN-ACCOUNTING-LOADED-001")
    bad = (
        "ᴍᴇɴᴛ ᴛʜᴇ ᴛᴇɴᴏᴜɴ ᴛʜᴇ ᴄᴏɴᴛᴀʙʟɪᴛʏ ᴘᴀᴄᴋ ᴘʟᴀʏᴇᴅ ᴛᴏ ᴛʜᴇ "
        "ᴛᴏʀɴᴇᴇ. ᴛʜᴇ ᴛᴏʀɴᴇᴇ ᴛʜᴇɴ ᴛʜᴇ ᴄᴏɴᴛᴀʙʟɪᴛʏ ᴘʟᴀʏᴇᴅ ᴛᴏ ᴛʜᴇ "
        "ᴛᴏʀɴᴇᴇ. ᴛʜᴇ ᴛᴏʀɴᴇᴇ ᴛʜᴇɴ ᴛʜᴇ ᴄᴏɴᴛᴀʙʟɪᴛʏ ᴘʟᴀʏᴇᴅ ᴛᴏ ᴛʜᴇ "
        "ᴛᴏʀɴᴇᴇ."
    )

    verdict = evaluate_executive_regression_case(
        case=case,
        assistant_message=bad,
        tool_trace=[{"tool": "provider", "result": {"ok": True}}],
    )

    assert verdict.ok is False
    assert any(item.startswith("quality:") for item in verdict.failures)
    assert "missing_tool:assistant_finance_accounting_qa" in verdict.failures


def test_finance_accounting_loaded_regression_accepts_snapshot_answer() -> None:
    case = _case("FIN-ACCOUNTING-LOADED-001")

    verdict = evaluate_executive_regression_case(
        case=case,
        assistant_message=(
            "Sí hay información financiera/contable cargada en SamChat.\n"
            "Resumen: 3 documentos, 2 gastos y 1 pólizas.\n\n"
            "Fuentes y rutas:\n"
            "- Fuente: finance.platform_snapshot read-only.\n"
            "- Ruta sugerida para revisar: /admin/finanzas\n\n"
            "No ejecuté cambios; esta respuesta es sólo lectura."
        ),
        tool_trace=[
            {
                "tool": "assistant_finance_accounting_qa",
                "result": {"question_type": "accounting_loaded", "ok": True},
            }
        ],
    )

    assert verdict.ok is True


def test_multi_candidate_selection_output_passes_owner_payment_regression() -> None:
    case = _case("OWNER-PAYMENT-EVIDENCE-001")
    frame = build_work_frame(case.question)
    selection = evaluate_readonly_candidates(
        work_frame=frame,
        candidates=[
            ReadOnlyCandidateResponse(
                tool="receipts.pending_payment_overview",
                label="Wrong shortcut",
                assistant_message="Pagos pendientes\nHay 0 solicitudes pendientes por $0.00.",
                tool_trace=[{"tool": "receipts.pending_payment_overview", "result": {"status": "success"}}],
            ),
            ReadOnlyCandidateResponse(
                tool="assistant_owner_variable_query",
                label="Owner variable gap",
                assistant_message=(
                    "No hay dato soportado para pagos hechos en agosto. "
                    "No hay evidencia viva suficiente. No ejecuté cambios."
                ),
                tool_trace=[{"tool": "assistant_owner_variable_query", "result": {"status": "missing"}}],
            ),
        ],
    )

    verdict = evaluate_executive_regression_case(
        case=case,
        assistant_message=selection.rendered_message,
        tool_trace=selection.selected.tool_trace if selection.selected else [],
        work_frame=frame,
    )

    assert selection.selected is not None
    assert selection.selected.tool == "assistant_owner_variable_query"
    assert verdict.ok is True


def test_expected_work_frame_contracts_are_stable_for_suite_questions() -> None:
    for case in executive_regression_cases():
        frame = build_work_frame(case.question)
        assert frame.domain == case.expected_domain, case.case_id
        assert frame.task_kind == case.expected_task_kind, case.case_id
        assert frame.authority_boundary == "read_only", case.case_id
