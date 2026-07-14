import pytest

from samchat.assistant.analyst_intent import (
    AnalystIntent,
    detect_analyst_intent,
)
from samchat.assistant.analyst_response import build_analyst_trace
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    build_analyst_evidence_pack,
    context_sufficiency_for_evidence,
    evidence_diagnostics_for_context,
    extract_analyst_evidence_from_messages,
    extract_inline_analyst_evidence,
    next_questions_for_context,
    rank_analyst_evidence,
    run_analyst_workbench,
    suggested_routes_for_context,
)


def _route_ids(routes):
    return [route["route_id"] for route in routes]


@pytest.mark.asyncio
async def test_insufficient_context_needs_context_without_provider():
    intent = detect_analyst_intent("Explícame esta balanza")

    result = await run_analyst_workbench(intent=intent, evidence=[])

    assert result.status == "needs_context"
    assert "subas, pegues o selecciones" in result.answer
    assert result.provider_called is False
    assert result.actions_executed == []
    assert result.coverage_level == "none"
    assert result.answer_contract["status"] == "needs_context"
    assert result.answer_contract["coverage_reasons"] == ["no_evidence"]
    assert result.answer_contract["next_question_count"] == 2
    assert result.answer_contract["suggested_route_count"] == 1
    assert _route_ids(result.suggested_routes) == ["evidence.collect_context"]
    assert result.suggested_routes[0]["execution_status"] == "not_executed"
    assert result.suggested_routes[0]["writes_enabled"] is False
    assert result.answer_contract["evidence_diagnostics"][0][
        "missing_evidence_reason"
    ] == "no_evidence"
    assert result.next_questions == [
        "¿Qué documento, reporte o texto debo usar como base?",
        "¿Quieres que el análisis sea para dirección, operación o cliente?",
    ]


@pytest.mark.asyncio
async def test_analyst_with_mocked_context_returns_structured_answer():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="contrato.pdf",
            summary=(
                "Contrato con penalización por entrega tardía "
                "y anexos pendientes."
            ),
        )
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.status == "success"
    assert "Riesgos visibles" in result.answer
    assert result.evidence[0]["label"] == "contrato.pdf"
    assert result.caveats
    assert result.next_questions
    assert result.provider_called is False
    assert result.actions_executed == []
    assert result.coverage_level in {"medium", "high"}
    assert result.answer_contract["version"] == "analyst_answer_contract_v1"
    assert result.answer_contract["external_validation_claimed"] is False


@pytest.mark.asyncio
async def test_analyst_trace_contract_exposes_routing_and_evidence_labels():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="contrato.pdf",
            summary=(
                "Contrato con penalizacion, responsable faltante "
                "y anexos pendientes."
            ),
        )
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)
    trace = build_analyst_trace(intent=intent, result=result)[0]
    wiring = trace["analyst_workbench_live_wiring"]

    assert wiring["selected_route"] == "analyst"
    assert wiring["conflict_reason"] == "document_context_analysis"
    assert wiring["answer_contract_status"] == "success"
    assert wiring["answer_contract_version"] == "analyst_answer_contract_v1"
    assert wiring["coverage_reasons"] == ["supported_context"]
    assert wiring["next_question_count"] == 2
    assert wiring["suggested_route_count"] == 0
    assert wiring["evidence_labels"] == ["contrato.pdf"]
    assert wiring["evidence_types"] == ["uploaded_file"]
    assert wiring["evidence_rank_scores"][0] > 0
    assert wiring["evidence_diagnostic_count"] == 1
    assert wiring["evidence_diagnostics"] == [
        {
            "source_type": "uploaded_file",
            "label": "contrato.pdf",
            "rank_score": wiring["evidence_rank_scores"][0],
            "rank_reasons": wiring["evidence_rank_reasons"][0],
            "coverage_contribution": "primary",
            "clipped": False,
            "low_relevance": False,
            "missing_evidence_reason": None,
            "trace_safe_summary": (
                "uploaded_file evidence ranked with score "
                f"{wiring['evidence_rank_scores'][0]}."
            ),
        }
    ]
    assert wiring["provider_called"] is False
    assert wiring["writes_attempted"] is False
    assert trace["result"]["answer_contract_status"] == "success"
    assert trace["result"]["coverage_reasons"] == ["supported_context"]
    assert trace["result"]["next_question_count"] == 2
    assert trace["result"]["suggested_route_count"] == 0
    assert trace["result"]["evidence_diagnostic_count"] == 1
    assert trace["result"]["evidence_labels"] == ["contrato.pdf"]
    assert trace["result"]["exportable"] is False


@pytest.mark.asyncio
async def test_compare_with_one_context_is_caveated():
    intent = detect_analyst_intent("Compara estos dos documentos")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="propuesta.pdf",
            summary="Propuesta con alcance y costo.",
        )
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.status == "success"
    assert "Comparación preliminar" in result.answer
    assert any("incompleta" in caveat for caveat in result.caveats)
    assert result.answer_contract["overclaim_guard_applied"] is True
    assert any("confirmación humana" in caveat for caveat in result.caveats)
    assert result.answer_contract["next_question_count"] == 3
    assert result.next_questions == [
        "¿Puedes compartir la fuente completa o confirmar estos hallazgos?",
        "¿Cuál es el documento base?",
        "¿Cuál es el documento contraparte a comparar?",
    ]


@pytest.mark.asyncio
async def test_workbench_contract_blocks_conclusion_on_material_conflict():
    intent = detect_analyst_intent(
        "Resume conclusiones del presupuesto contra gasto"
    )
    evidence = [
        AnalystEvidence(
            source_type="budget",
            label="Presupuesto obra norte",
            summary="Presupuesto aprobado para obra norte.",
            source_id="budget-obra-norte",
            date="2026-07-10",
            freshness="current",
            metadata={"concept": "obra-norte", "amount": "1000.00"},
        ),
        AnalystEvidence(
            source_type="expense",
            label="Gasto obra norte",
            summary="Gasto registrado para obra norte.",
            source_id="expense-obra-norte",
            date="2026-07-10",
            freshness="current",
            metadata={"concept": "obra-norte", "amount": "1250.00"},
        ),
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.answer_contract["evidence_quality_status"] == "conflicting"
    assert result.answer_contract["safe_to_conclude"] is False
    assert result.answer_contract["blocking_conflicts"][0][
        "diagnostic_type"
    ] == "amount_conflict"
    assert result.answer_contract["overclaim_guard_applied"] is True
    assert any("contradictoria" in caveat for caveat in result.caveats)
    assert any("fuente debe prevalecer" in q for q in result.next_questions)
    assert result.provider_called is False
    assert result.actions_executed == []


@pytest.mark.asyncio
async def test_workbench_contract_reports_missing_critical_source():
    intent = detect_analyst_intent(
        "Resume conclusiones del presupuesto contra gasto"
    )
    evidence = [
        AnalystEvidence(
            source_type="budget",
            label="Presupuesto obra norte",
            summary="Presupuesto aprobado para obra norte.",
            source_id="budget-obra-norte",
            date="2026-07-10",
            freshness="current",
            metadata={"budget_id": "budget-obra-norte"},
        )
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.answer_contract["evidence_quality_status"] == (
        "missing_critical_sources"
    )
    assert result.answer_contract["safe_to_conclude"] is False
    assert result.answer_contract["missing_critical_sources"] == [
        {
            "source_type": "expense",
            "label": "registro de gasto",
            "reason": "intent_signal:expense",
            "blocks_conclusion": True,
        }
    ]
    assert any("registro de gasto" in q for q in result.next_questions)
    assert result.provider_called is False
    assert result.actions_executed == []


def test_extracts_document_intake_evidence_from_message():
    message = (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        '{"detected_document_type":"accounting_balance",'
        '"summary":"Balanza mayo 2026","missing_fields":["company"]}\n\n'
        "Archivo procesado."
    )

    evidence = extract_analyst_evidence_from_messages([message])

    assert len(evidence) == 1
    assert evidence[0].source_type == "document_intake"
    assert evidence[0].label == "accounting_balance"
    assert "Balanza mayo 2026" in evidence[0].summary


def test_extracts_inline_context_evidence_from_current_message():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    message = (
        "Qué riesgos ves en este contrato: "
        "El proveedor entrega fuera de plazo, no define responsable, "
        "mantiene penalizaciones abiertas y no adjunta anexo tecnico."
    )

    evidence = extract_inline_analyst_evidence(message, intent)

    assert len(evidence) == 1
    assert evidence[0].source_type == "inline_context"
    assert evidence[0].label == "contexto inline"
    assert "proveedor entrega fuera de plazo" in evidence[0].summary


def test_short_inline_prompt_does_not_create_evidence():
    intent = detect_analyst_intent("Explícame esto")

    evidence = extract_inline_analyst_evidence("Explícame esto: corto", intent)

    assert evidence == []


@pytest.mark.asyncio
async def test_long_inline_context_is_clipped_and_caveated():
    intent = detect_analyst_intent("Resume este texto")
    evidence = extract_inline_analyst_evidence(
        "Resume este texto: " + ("obligacion contractual " * 80),
        intent,
    )

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.status == "success"
    assert result.evidence[0]["summary"].endswith("...")
    assert any("recortada" in caveat for caveat in result.caveats)
    assert result.coverage_level == "low"
    assert result.answer_contract["coverage_reasons"] == ["clipped_evidence"]
    assert result.answer_contract["overclaim_guard_applied"] is True


def test_evidence_pack_orders_inline_first_dedupes_and_limits():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    inline = [
        AnalystEvidence(
            source_type="inline_context",
            label="contexto inline",
            summary="A" * 50,
        )
    ]
    history = inline + [
        AnalystEvidence(
            source_type="conversation",
            label=f"contexto {index}",
            summary=f"historial {index}",
        )
        for index in range(10)
    ]

    packed = build_analyst_evidence_pack(
        inline_evidence=inline,
        history_evidence=history,
        intent=intent,
    )

    assert len(packed) == 6
    assert packed[0].source_type == "inline_context"
    assert sum(
        1 for item in packed if item.source_type == "inline_context"
    ) == 1
    assert packed[0].rank_score > 0
    assert packed[0].rank_reasons


def test_document_intake_ranks_above_conversation_for_explain():
    intent = detect_analyst_intent("Explícame esta balanza")
    evidence = [
        AnalystEvidence(
            source_type="conversation",
            label="contexto de conversación",
            summary="Conversación amplia con detalles generales del cierre.",
        ),
        AnalystEvidence(
            source_type="document_intake",
            label="accounting_balance",
            summary="Balanza mayo 2026 con cuentas y saldos.",
        ),
    ]

    ranked = rank_analyst_evidence(intent, evidence)

    assert ranked[0].source_type == "document_intake"
    assert "direct_document_or_report" in ranked[0].rank_reasons


def test_report_result_ranks_above_conversation_for_summary():
    intent = detect_analyst_intent("Resume conclusiones")
    evidence = [
        AnalystEvidence(
            source_type="conversation",
            label="contexto de conversación",
            summary="Mensaje largo de conversación con contexto general.",
        ),
        AnalystEvidence(
            source_type="report_result",
            label="reporte previo",
            summary="Comparacion | concepto | monto | variacion relevante.",
        ),
    ]

    ranked = rank_analyst_evidence(intent, evidence)

    assert ranked[0].source_type == "report_result"


def test_evidence_pack_dedupes_normalized_text():
    intent = detect_analyst_intent("Resume este texto")
    evidence = [
        AnalystEvidence(
            source_type="conversation",
            label="Contexto",
            summary="  MISMO   texto con espacios  ",
        ),
        AnalystEvidence(
            source_type="conversation",
            label="contexto",
            summary="mismo texto con espacios",
        ),
    ]

    packed = build_analyst_evidence_pack(
        inline_evidence=[],
        history_evidence=evidence,
        intent=intent,
    )

    assert len(packed) == 1


def test_rank_reasons_are_serialized_to_dict():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = rank_analyst_evidence(
        intent,
        [
            AnalystEvidence(
                source_type="inline_context",
                label="contrato.pdf",
                summary="Contrato con penalizacion y responsable faltante.",
            )
        ],
    )

    payload = evidence[0].to_dict()

    assert payload["rank_score"] > 0
    assert "rank_reasons" in payload
    assert "risk_review_terms" in payload["rank_reasons"]


def test_evidence_diagnostics_contract_tracks_source_score_and_reasons():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = rank_analyst_evidence(
        intent,
        [
            AnalystEvidence(
                source_type="uploaded_file",
                label="contrato.pdf",
                summary="Contrato con penalizacion y responsable faltante.",
            )
        ],
    )

    diagnostics = evidence_diagnostics_for_context(
        evidence=evidence,
        coverage_level="medium",
        coverage_reasons=["supported_context"],
    )

    assert diagnostics == [
        {
            "source_type": "uploaded_file",
            "label": "contrato.pdf",
            "rank_score": evidence[0].rank_score,
            "rank_reasons": evidence[0].rank_reasons,
            "coverage_contribution": "primary",
            "clipped": False,
            "low_relevance": False,
            "missing_evidence_reason": None,
            "trace_safe_summary": (
                "uploaded_file evidence ranked with score "
                f"{evidence[0].rank_score}."
            ),
        }
    ]


def test_evidence_diagnostics_flags_clipped_and_low_relevance():
    clipped = [
        AnalystEvidence(
            source_type="inline_context",
            label="contexto inline",
            summary="Contrato con obligaciones...",
            rank_score=90,
            rank_reasons=["source:inline_context", "clipped_summary"],
        )
    ]
    low = [
        AnalystEvidence(
            source_type="conversation",
            label="contexto",
            summary="Tema general.",
            rank_score=40,
            rank_reasons=["source:conversation", "short_summary"],
        )
    ]

    clipped_diagnostics = evidence_diagnostics_for_context(
        evidence=clipped,
        coverage_level="low",
        coverage_reasons=["clipped_evidence"],
    )
    low_diagnostics = evidence_diagnostics_for_context(
        evidence=low,
        coverage_level="low",
        coverage_reasons=["low_relevance"],
    )

    assert clipped_diagnostics[0]["clipped"] is True
    assert clipped_diagnostics[0]["coverage_contribution"] == "clipped"
    assert clipped_diagnostics[0][
        "missing_evidence_reason"
    ] == "clipped_evidence"
    assert clipped_diagnostics[0]["low_relevance"] is False
    assert low_diagnostics[0]["clipped"] is False
    assert low_diagnostics[0]["low_relevance"] is True
    assert low_diagnostics[0]["coverage_contribution"] == "limited"
    assert low_diagnostics[0]["missing_evidence_reason"] == "low_relevance"


def test_evidence_diagnostics_represent_missing_evidence_safely():
    diagnostics = evidence_diagnostics_for_context(
        evidence=[],
        coverage_level="none",
        coverage_reasons=["no_evidence"],
    )

    assert diagnostics == [
        {
            "source_type": "missing_context",
            "label": "contexto requerido",
            "rank_score": 0,
            "rank_reasons": ["no_evidence"],
            "coverage_contribution": "missing",
            "clipped": False,
            "low_relevance": True,
            "missing_evidence_reason": "no_evidence",
            "trace_safe_summary": (
                "No hay evidencia disponible para sostener el análisis."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_low_relevance_evidence_adds_conservative_caveat():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = rank_analyst_evidence(
        intent,
        [
            AnalystEvidence(
                source_type="conversation",
                label="contexto de conversación",
                summary="Tema general sin datos concretos suficientes.",
            )
        ],
    )

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.status == "success"
    assert result.coverage_level == "low"
    assert result.answer_contract["coverage_reasons"] == ["low_relevance"]
    assert result.answer_contract["evidence_diagnostic_count"] == 1
    assert result.answer_contract["evidence_diagnostics"][0][
        "low_relevance"
    ] is True
    assert any("limitada o indirecta" in caveat for caveat in result.caveats)
    assert result.answer_contract["overclaim_guard_applied"] is True
    assert result.answer_contract["next_question_count"] == 3


@pytest.mark.asyncio
async def test_high_coverage_clear_intent_has_no_followup_questions():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="inline_context",
            label="contrato.pdf",
            summary="Contrato con penalizacion y responsable faltante.",
        ),
        AnalystEvidence(
            source_type="uploaded_file",
            label="anexo.pdf",
            summary="Anexo con obligaciones, fechas y responsables.",
        ),
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.coverage_level == "high"
    assert result.next_questions == []
    assert result.answer_contract["next_question_count"] == 0


def test_context_sufficiency_matrix_covers_core_reasons():
    risk_intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    compare_intent = detect_analyst_intent("Compara estos dos documentos")

    assert context_sufficiency_for_evidence([]).coverage_reasons == [
        "no_evidence"
    ]

    clipped = rank_analyst_evidence(
        risk_intent,
        [
            AnalystEvidence(
                source_type="inline_context",
                label="contrato.pdf",
                summary="Contrato con obligaciones...",
            )
        ],
    )
    assert context_sufficiency_for_evidence(
        clipped,
        risk_intent,
    ).coverage_reasons == ["clipped_evidence"]

    low = rank_analyst_evidence(
        risk_intent,
        [
            AnalystEvidence(
                source_type="conversation",
                label="contexto",
                summary="Tema general sin datos concretos suficientes.",
            )
        ],
    )
    assert context_sufficiency_for_evidence(
        low,
        risk_intent,
    ).coverage_reasons == ["low_relevance"]

    incomplete_compare = rank_analyst_evidence(
        compare_intent,
        [
            AnalystEvidence(
                source_type="uploaded_file",
                label="propuesta.pdf",
                summary="Propuesta con alcance y costo.",
            )
        ],
    )
    assert context_sufficiency_for_evidence(
        incomplete_compare,
        compare_intent,
    ).coverage_reasons == ["incomplete_comparison"]

    high = rank_analyst_evidence(
        risk_intent,
        [
            AnalystEvidence(
                source_type="inline_context",
                label="contrato.pdf",
                summary="Contrato con penalizacion y responsable faltante.",
            ),
            AnalystEvidence(
                source_type="uploaded_file",
                label="anexo.pdf",
                summary="Anexo con obligaciones, fechas y responsables.",
            ),
        ],
    )
    high_sufficiency = context_sufficiency_for_evidence(high, risk_intent)
    assert high_sufficiency.coverage_level == "high"
    assert high_sufficiency.coverage_reasons == [
        "multi_source_high_relevance"
    ]


def test_next_questions_contract_dedupes_and_tracks_context_needs():
    risk_intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    compare_intent = detect_analyst_intent("Compara estos dos documentos")
    ambiguous_intent = AnalystIntent(
        request_id="analyst_unknown",
        mode="analyst",
        analyst_intent="unknown",
        confidence=0.0,
        requires_operational_route=False,
        operational_route_hint=None,
        requires_provider=False,
        context_requirements=[],
        missing_context=[],
        safety={"read_only": True, "writes_allowed": False},
        raw_text="ayúdame con esto",
        conflict_resolution={
            "selected_route": "analyst",
            "reason": "ambiguous",
            "operational_route_hint": None,
        },
    )

    no_context = next_questions_for_context(
        intent=risk_intent,
        coverage_level="none",
        coverage_reasons=["no_evidence"],
        evidence=[],
    )
    assert no_context == [
        "¿Qué documento, reporte o texto debo usar como base?",
        "¿Quieres que el análisis sea para dirección, operación o cliente?",
    ]

    incomplete_compare = next_questions_for_context(
        intent=compare_intent,
        coverage_level="low",
        coverage_reasons=["incomplete_comparison"],
        evidence=[
            AnalystEvidence(
                source_type="uploaded_file",
                label="propuesta.pdf",
                summary="Propuesta con alcance y costo.",
            )
        ],
    )
    assert incomplete_compare == [
        "¿Puedes compartir la fuente completa o confirmar estos hallazgos?",
        "¿Cuál es el documento base?",
        "¿Cuál es el documento contraparte a comparar?",
    ]

    high_coverage = next_questions_for_context(
        intent=risk_intent,
        coverage_level="high",
        coverage_reasons=["multi_source_high_relevance"],
        evidence=[
            AnalystEvidence(
                source_type="uploaded_file",
                label="contrato.pdf",
                summary="Contrato con obligaciones y responsables.",
            ),
            AnalystEvidence(
                source_type="document_intake",
                label="anexo.pdf",
                summary="Anexo con fechas y aceptación.",
            ),
        ],
    )
    assert high_coverage == []

    ambiguous = next_questions_for_context(
        intent=ambiguous_intent,
        coverage_level="none",
        coverage_reasons=["no_evidence"],
        evidence=[],
    )
    assert ambiguous == [
        "¿Quieres que analice riesgos, resumen, comparación o próximos pasos?"
    ]

    write_like = next_questions_for_context(
        intent=detect_analyst_intent("crea un resumen de este contrato"),
        coverage_level="none",
        coverage_reasons=["operational_route"],
        evidence=[],
    )
    assert write_like == [
        "¿Confirmas que solo debo sugerir la ruta y no ejecutarla?"
    ]


def test_suggested_routes_contract_derives_read_only_routes():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="reporte financiero.pdf",
            summary=(
                "Factura CFDI pendiente, pago por saldar y presupuesto "
                "de gastos por confirmar."
            ),
        )
    ]

    routes = suggested_routes_for_context(
        intent=intent,
        coverage_level="medium",
        coverage_reasons=["supported_context"],
        evidence=evidence,
    )

    assert _route_ids(routes) == [
        "cfdi.list_pending",
        "payments.list_pending",
        "finance.breakdown",
    ]
    for route in routes:
        assert route["execution_status"] == "not_executed"
        assert route["writes_enabled"] is False
        assert "route_execution" in route["blocked_capabilities"]


def test_suggested_routes_contract_preserves_operational_hint():
    intent = detect_analyst_intent("Qué CFDIs están pendientes")

    routes = suggested_routes_for_context(
        intent=intent,
        coverage_level="none",
        coverage_reasons=["operational_route"],
        evidence=[],
    )

    assert _route_ids(routes) == ["cfdi.list_pending"]
    assert routes[0]["reason"] == "operational_route_detected_not_executed"


def test_suggested_routes_contract_is_empty_without_signal():
    intent = detect_analyst_intent("Resume este texto")

    routes = suggested_routes_for_context(
        intent=intent,
        coverage_level="medium",
        coverage_reasons=["supported_context"],
        evidence=[
            AnalystEvidence(
                source_type="uploaded_file",
                label="minuta.pdf",
                summary="Minuta con acuerdos internos y responsables.",
            )
        ],
    )

    assert routes == []


def test_insufficient_evidence_suggests_inert_collection_route():
    intent = detect_analyst_intent("Explícame esta balanza")

    routes = suggested_routes_for_context(
        intent=intent,
        coverage_level="none",
        coverage_reasons=["no_evidence"],
        evidence=[],
    )

    assert _route_ids(routes) == ["evidence.collect_context"]
    assert routes[0]["required_context"] == ["uploaded_document"]
    assert routes[0]["execution_status"] == "not_executed"
    assert routes[0]["writes_enabled"] is False


@pytest.mark.asyncio
async def test_suggested_routes_are_recommendations_not_actions():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="riesgos-cfdi.pdf",
            summary=(
                "Contrato con factura CFDI pendiente y pagos por saldar."
            ),
        )
    ]

    result = await run_analyst_workbench(intent=intent, evidence=evidence)

    assert result.status == "success"
    assert _route_ids(result.suggested_routes) == [
        "cfdi.list_pending",
        "payments.list_pending",
    ]
    assert result.suggested_routes[0]["label"] == "Revisar CFDI pendientes"
    assert result.suggested_routes[0]["execution_status"] == "not_executed"
    assert result.suggested_routes[0]["writes_enabled"] is False
    assert result.actions_executed == []
    assert result.answer_contract["writes_allowed"] is False
    assert result.answer_contract["suggested_route_count"] == 2
    assert result.answer_contract["suggested_routes"] == (
        result.suggested_routes
    )


@pytest.mark.asyncio
async def test_operational_route_suggestion_still_executes_nothing():
    intent = detect_analyst_intent("Qué CFDIs están pendientes")

    result = await run_analyst_workbench(intent=intent, evidence=[])

    assert result.status == "routed_to_operational"
    assert _route_ids(result.suggested_routes) == ["cfdi.list_pending"]
    assert result.actions_executed == []
    assert result.provider_called is False
    assert result.answer_contract["suggested_route_count"] == 1


@pytest.mark.asyncio
async def test_provider_failure_keeps_answer_contract():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = rank_analyst_evidence(
        intent,
        [
            AnalystEvidence(
                source_type="inline_context",
                label="contrato.pdf",
                summary="Contrato con penalizacion y responsable faltante.",
            )
        ],
    )

    async def provider_raises(_intent, _evidence):
        raise RuntimeError("provider unavailable")

    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        provider_allowed=True,
        provider_fn=provider_raises,
    )

    assert result.status == "provider_unavailable"
    assert result.answer_contract["version"] == "analyst_answer_contract_v1"
    assert result.answer_contract["overclaim_guard_applied"] is True
    assert result.coverage_level in {"medium", "high"}
    assert result.answer_contract["next_question_count"] == 1


@pytest.mark.asyncio
async def test_provider_success_applies_overclaim_guard_for_low_coverage():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = rank_analyst_evidence(
        intent,
        [
            AnalystEvidence(
                source_type="conversation",
                label="contexto de conversación",
                summary="Tema general sin datos concretos suficientes.",
            )
        ],
    )

    async def provider_answer(_intent, _evidence):
        return "Respuesta con el contexto disponible: hay riesgo relevante."

    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        provider_allowed=True,
        provider_fn=provider_answer,
    )

    assert result.status == "success"
    assert result.provider_called is True
    assert result.actions_executed == []
    assert result.coverage_level == "low"
    assert "preliminar con el contexto disponible" in result.answer
    assert result.answer_contract["overclaim_guard_applied"] is True
    assert result.answer_contract["writes_allowed"] is False
    assert result.answer_contract["external_validation_claimed"] is False
    assert result.answer_contract["next_question_count"] == 0
    assert any("confirmación humana" in caveat for caveat in result.caveats)


@pytest.mark.asyncio
async def test_provider_success_caveats_incomplete_comparison():
    intent = detect_analyst_intent("Compara estos dos documentos")
    evidence = rank_analyst_evidence(
        intent,
        [
            AnalystEvidence(
                source_type="uploaded_file",
                label="propuesta.pdf",
                summary="Propuesta con alcance y costo.",
            )
        ],
    )

    async def provider_answer(_intent, _evidence):
        return "Comparación con el contexto disponible: hay diferencias."

    result = await run_analyst_workbench(
        intent=intent,
        evidence=evidence,
        provider_allowed=True,
        provider_fn=provider_answer,
    )

    assert result.status == "success"
    assert result.provider_called is True
    assert result.actions_executed == []
    assert "preliminar con el contexto disponible" in result.answer
    assert result.answer_contract["overclaim_guard_applied"] is True
    assert result.answer_contract["writes_allowed"] is False
    assert result.answer_contract["external_validation_claimed"] is False
    assert result.answer_contract["next_question_count"] == 0
    assert any("confirmación humana" in caveat for caveat in result.caveats)
