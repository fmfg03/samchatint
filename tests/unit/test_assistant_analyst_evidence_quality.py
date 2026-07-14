from datetime import date

from samchat.assistant.analyst_evidence_quality import (
    evaluate_evidence_quality,
)
from samchat.assistant.analyst_intent import AnalystIntent
from samchat.assistant.analyst_workbench import AnalystEvidence


def _intent(text="Resume conclusiones del presupuesto contra gasto"):
    return AnalystIntent(
        request_id="analyst_test",
        mode="analyst",
        analyst_intent="summarize",
        confidence=0.9,
        requires_operational_route=False,
        operational_route_hint=None,
        requires_provider=False,
        context_requirements=["uploaded_document"],
        missing_context=[],
        safety={"read_only": True, "writes_allowed": False},
        raw_text=text,
        conflict_resolution={
            "selected_route": "analyst",
            "reason": "analyst_intent_match",
            "operational_route_hint": None,
        },
    )


def _evidence(source_type, label, **metadata):
    return AnalystEvidence(
        source_type=source_type,
        label=label,
        summary=f"Evidencia {label}",
        source=f"test:{source_type}",
        source_id=str(metadata.pop("source_id", label)),
        date=str(metadata.pop("date", "2026-07-10")),
        freshness=str(metadata.pop("freshness", "current")),
        metadata=metadata,
    )


def test_quality_resolver_allows_sufficient_current_evidence():
    result = evaluate_evidence_quality(
        intent=_intent("Resume conclusiones del presupuesto contra gasto"),
        evidence=[
            _evidence("budget", "Presupuesto julio", budget_id="b-1"),
            _evidence("expense", "Gasto julio", expense_id="e-1"),
        ],
        coverage_level="high",
        coverage_reasons=["multi_source_high_relevance"],
        reference_date=date(2026, 7, 14),
    )

    assert result.evidence_quality_status == "sufficient"
    assert result.safe_to_conclude is True
    assert result.freshness_diagnostics == []
    assert result.conflict_diagnostics == []
    assert result.missing_critical_sources == []


def test_quality_resolver_flags_stale_evidence_without_blocking():
    result = evaluate_evidence_quality(
        intent=_intent("Resume este documento"),
        evidence=[
            _evidence(
                "document_evidence",
                "Contrato anterior",
                source_id="doc-1",
                date="2025-01-01",
            )
        ],
        coverage_level="medium",
        coverage_reasons=["supported_context"],
        reference_date=date(2026, 7, 14),
    )

    assert result.evidence_quality_status == "stale"
    assert result.safe_to_conclude is True
    assert result.freshness_diagnostics[0]["diagnostic_type"] == (
        "stale_evidence"
    )
    assert any("vieja" in caveat for caveat in result.caveats)


def test_quality_resolver_flags_partial_low_coverage():
    result = evaluate_evidence_quality(
        intent=_intent("Resume conclusiones del presupuesto contra gasto"),
        evidence=[
            _evidence("budget", "Presupuesto parcial", budget_id="b-2")
        ],
        coverage_level="low",
        coverage_reasons=["low_relevance"],
        reference_date=date(2026, 7, 14),
    )

    assert result.evidence_quality_status == "missing_critical_sources"
    assert result.safe_to_conclude is False
    assert any("parcial" in caveat for caveat in result.caveats)
    assert any(
        "fuente completa" in q
        for q in result.recommended_next_questions
    )


def test_quality_resolver_blocks_conclusion_on_amount_conflict():
    result = evaluate_evidence_quality(
        intent=_intent("Resume conclusiones del presupuesto contra gasto"),
        evidence=[
            _evidence(
                "budget",
                "Presupuesto A",
                source_id="budget-a",
                concept="obra-x",
                amount="1000.00",
            ),
            _evidence(
                "expense",
                "Gasto A",
                source_id="expense-a",
                concept="obra-x",
                amount="1250.00",
            ),
        ],
        coverage_level="high",
        coverage_reasons=["multi_source_high_relevance"],
        reference_date=date(2026, 7, 14),
    )

    assert result.evidence_quality_status == "conflicting"
    assert result.safe_to_conclude is False
    assert result.blocking_conflicts[0]["diagnostic_type"] == (
        "amount_conflict"
    )
    assert result.blocking_conflicts[0]["blocks_conclusion"] is True
    assert any(
        "fuente debe prevalecer" in q
        for q in result.recommended_next_questions
    )


def test_quality_resolver_blocks_conclusion_on_date_conflict():
    result = evaluate_evidence_quality(
        intent=_intent("Resume conclusiones del pago contra CFDI"),
        evidence=[
            _evidence(
                "cfdi_document",
                "CFDI A",
                source_id="cfdi-a",
                date="",
                cfdi_uuid="uuid-a",
                cfdi_date="2026-07-01",
            ),
            _evidence(
                "registered_payment",
                "Pago A",
                source_id="pay-a",
                date="",
                cfdi_uuid="uuid-a",
                payment_date="2026-07-08",
            ),
        ],
        coverage_level="high",
        coverage_reasons=["multi_source_high_relevance"],
        reference_date=date(2026, 7, 14),
    )

    assert result.safe_to_conclude is False
    assert result.blocking_conflicts[0]["diagnostic_type"] == "date_conflict"
    assert result.blocking_conflicts[0]["fields"]["dates"] == [
        "2026-07-01",
        "2026-07-08",
    ]


def test_quality_resolver_flags_duplicates_without_dropping_evidence():
    result = evaluate_evidence_quality(
        intent=_intent("Resume este documento"),
        evidence=[
            _evidence("document_evidence", "Documento A", source_id="doc-x"),
            _evidence(
                "document_evidence",
                "Documento A copia",
                source_id="doc-x",
            ),
        ],
        coverage_level="medium",
        coverage_reasons=["supported_context"],
        reference_date=date(2026, 7, 14),
    )

    duplicate = [
        item
        for item in result.conflict_diagnostics
        if item["diagnostic_type"] == "duplicate_evidence"
    ]
    assert duplicate
    assert duplicate[0]["blocks_conclusion"] is False


def test_quality_resolver_reports_missing_critical_sources_by_intent():
    result = evaluate_evidence_quality(
        intent=_intent("Resume conclusiones del presupuesto contra gasto"),
        evidence=[
            _evidence("budget", "Presupuesto julio", budget_id="b-3")
        ],
        coverage_level="medium",
        coverage_reasons=["supported_context"],
        reference_date=date(2026, 7, 14),
    )

    assert result.evidence_quality_status == "missing_critical_sources"
    assert result.safe_to_conclude is False
    assert result.missing_critical_sources == [
        {
            "source_type": "expense",
            "label": "registro de gasto",
            "reason": "intent_signal:expense",
            "blocks_conclusion": True,
        }
    ]
    assert result.recommended_next_questions == [
        "¿Cuál es el registro de gasto que debo revisar?"
    ]
