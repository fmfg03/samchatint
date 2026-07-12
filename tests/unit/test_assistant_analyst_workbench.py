import pytest

from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_response import build_analyst_trace
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    build_analyst_evidence_pack,
    extract_analyst_evidence_from_messages,
    extract_inline_analyst_evidence,
    rank_analyst_evidence,
    run_analyst_workbench,
)


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
    assert wiring["evidence_labels"] == ["contrato.pdf"]
    assert wiring["evidence_types"] == ["uploaded_file"]
    assert wiring["evidence_rank_scores"][0] > 0
    assert wiring["provider_called"] is False
    assert wiring["writes_attempted"] is False
    assert trace["result"]["answer_contract_status"] == "success"
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
    assert any("limitada o indirecta" in caveat for caveat in result.caveats)
    assert result.answer_contract["overclaim_guard_applied"] is True


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
