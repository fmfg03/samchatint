import pytest

from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.analyst_workbench import (
    AnalystEvidence,
    build_analyst_evidence_pack,
    extract_analyst_evidence_from_messages,
    extract_inline_analyst_evidence,
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


def test_evidence_pack_orders_inline_first_dedupes_and_limits():
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
    )

    assert len(packed) == 6
    assert packed[0].source_type == "inline_context"
    assert packed.count(inline[0]) == 1
