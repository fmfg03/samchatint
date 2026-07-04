import pytest

from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    extract_analyst_evidence_from_messages,
    run_analyst_workbench,
)


@pytest.mark.asyncio
async def test_analyst_with_insufficient_context_needs_context_without_provider():
    intent = detect_analyst_intent("Explícame esta balanza")

    result = await run_analyst_workbench(intent=intent, evidence=[])

    assert result.status == "needs_context"
    assert "subas, pegues o selecciones" in result.answer
    assert result.provider_called is False
    assert result.actions_executed == []


@pytest.mark.asyncio
async def test_analyst_with_mocked_context_returns_structured_answer():
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")
    evidence = [
        AnalystEvidence(
            source_type="uploaded_file",
            label="contrato.pdf",
            summary="Contrato con penalización por entrega tardía y anexos pendientes.",
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


def test_extracts_document_intake_evidence_from_message():
    message = (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        '{"detected_document_type":"accounting_balance","summary":"Balanza mayo 2026","missing_fields":["company"]}\n\n'
        "Archivo procesado."
    )

    evidence = extract_analyst_evidence_from_messages([message])

    assert len(evidence) == 1
    assert evidence[0].source_type == "document_intake"
    assert evidence[0].label == "accounting_balance"
    assert "Balanza mayo 2026" in evidence[0].summary
