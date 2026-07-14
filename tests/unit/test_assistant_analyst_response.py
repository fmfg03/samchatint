from samchat.assistant.analyst_response import (
    build_analyst_trace,
    render_analyst_result,
)
from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import AnalystWorkbenchResult


def _result(**overrides):
    base = {
        "status": "success",
        "title": "Analyst Workbench",
        "answer": "Respuesta base.",
        "evidence": [],
        "caveats": [],
        "next_questions": [],
        "suggested_routes": [],
        "actions_executed": [],
        "provider_called": False,
        "coverage_level": "medium",
        "answer_contract": {
            "version": "analyst_answer_contract_v1",
            "status": "success",
            "coverage_reasons": ["supported_context"],
            "next_question_count": 0,
            "suggested_route_count": 0,
            "suggested_routes": [],
            "evidence_diagnostic_count": 0,
            "evidence_diagnostics": [],
            "overclaim_guard_applied": False,
            "writes_allowed": False,
        },
    }
    base.update(overrides)
    return AnalystWorkbenchResult(**base)


def test_render_contract_omits_empty_sections_and_bullets():
    rendered = render_analyst_result(
        _result(
            evidence=[
                {"label": "  ", "summary": ""},
                {"label": "contrato.pdf", "summary": "  "},
            ],
            caveats=[" ", ""],
            next_questions=[""],
            suggested_routes=[],
        )
    )

    assert "Respuesta:" in rendered
    assert "Soporte en evidencia:" not in rendered
    assert "Límites:" not in rendered
    assert "Siguientes preguntas:" not in rendered
    assert "Ruta sugerida:" not in rendered
    assert "- :" not in rendered


def test_render_contract_keeps_stable_section_order_when_present():
    route = {
        "route_id": "cfdi.list_pending",
        "label": "Revisar CFDI pendientes",
        "reason": "evidence_signal:cfdi.list_pending",
        "required_context": ["CFDI o factura relacionada"],
        "blocked_capabilities": ["writes", "route_execution"],
        "execution_status": "not_executed",
        "writes_enabled": False,
    }

    rendered = render_analyst_result(
        _result(
            evidence=[
                {
                    "source_type": "uploaded_file",
                    "label": "contrato.pdf",
                    "summary": "Factura pendiente.",
                }
            ],
            caveats=["No revisé datos vivos."],
            next_questions=["¿Compartes el CFDI?"],
            suggested_routes=[route],
        )
    )

    assert rendered.index("Respuesta:") < rendered.index(
        "Soporte en evidencia:"
    )
    assert rendered.index("Soporte en evidencia:") < rendered.index(
        "Límites:"
    )
    assert rendered.index("Límites:") < rendered.index(
        "Siguientes preguntas:"
    )
    assert rendered.index("Siguientes preguntas:") < rendered.index(
        "Ruta sugerida:"
    )
    assert (
        "- Revisar CFDI pendientes "
        "(cfdi.list_pending, not_executed)"
    ) in rendered
    assert "ejecutada" not in rendered.lower()


def test_trace_preserves_read_only_render_contract_fields():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    route = {
        "route_id": "finance.breakdown",
        "label": "Conciliar presupuesto y gastos",
        "reason": "evidence_signal:finance.breakdown",
        "required_context": ["Presupuesto, gasto o reporte financiero"],
        "blocked_capabilities": ["writes", "route_execution"],
        "execution_status": "not_executed",
        "writes_enabled": False,
    }
    result = _result(
        suggested_routes=[route],
        answer_contract={
            "version": "analyst_answer_contract_v1",
            "status": "success",
            "coverage_reasons": ["supported_context"],
            "next_question_count": 0,
            "suggested_route_count": 1,
            "suggested_routes": [route],
            "evidence_diagnostic_count": 0,
            "evidence_diagnostics": [],
            "overclaim_guard_applied": False,
            "writes_allowed": False,
        },
    )

    trace = build_analyst_trace(intent=intent, result=result)[0]
    wiring = trace["analyst_workbench_live_wiring"]

    assert trace["result"]["exportable"] is False
    assert wiring["writes_attempted"] is False
    assert wiring["provider_called"] is False
    assert wiring["actions_executed"] == []
    assert wiring["suggested_routes"][0]["execution_status"] == "not_executed"
    assert wiring["suggested_routes"][0]["writes_enabled"] is False


def test_trace_exposes_evidence_quality_fields():
    intent = detect_analyst_intent("Resume conclusiones del presupuesto")
    result = _result(
        caveats=["Hay evidencia contradictoria."],
        next_questions=["¿Qué fuente debe prevalecer?"],
        answer_contract={
            "version": "analyst_answer_contract_v1",
            "status": "success",
            "coverage_reasons": ["supported_context"],
            "next_question_count": 1,
            "suggested_route_count": 0,
            "suggested_routes": [],
            "evidence_diagnostic_count": 0,
            "evidence_diagnostics": [],
            "overclaim_guard_applied": True,
            "writes_allowed": False,
            "evidence_quality_status": "conflicting",
            "safe_to_conclude": False,
            "freshness_diagnostics": [],
            "conflict_diagnostics": [
                {
                    "diagnostic_type": "amount_conflict",
                    "reason": "same_concept_has_incompatible_amounts",
                    "blocks_conclusion": True,
                }
            ],
            "blocking_conflicts": [
                {
                    "diagnostic_type": "amount_conflict",
                    "reason": "same_concept_has_incompatible_amounts",
                    "blocks_conclusion": True,
                }
            ],
            "missing_critical_sources": [],
        },
    )

    rendered = render_analyst_result(result)
    trace = build_analyst_trace(intent=intent, result=result)[0]
    wiring = trace["analyst_workbench_live_wiring"]

    assert "Límites:" in rendered
    assert "Siguientes preguntas:" in rendered
    assert wiring["evidence_quality_status"] == "conflicting"
    assert wiring["safe_to_conclude"] is False
    assert wiring["blocking_conflicts"][0]["diagnostic_type"] == (
        "amount_conflict"
    )
    assert trace["result"]["evidence_quality_status"] == "conflicting"
    assert trace["result"]["safe_to_conclude"] is False
    assert trace["result"]["exportable"] is False
