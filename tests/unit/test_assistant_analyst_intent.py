from samchat.assistant.analyst_intent import detect_analyst_intent
from samchat.assistant.request_intent import detect_request_intent


def test_explain_balance_routes_to_analyst() -> None:
    intent = detect_analyst_intent("Explícame esta balanza")

    assert intent is not None
    assert intent.analyst_intent == "explain"
    assert intent.requires_operational_route is False
    assert "uploaded_document" in intent.context_requirements


def test_contract_risk_review_routes_to_analyst_not_finance_report() -> None:
    operational = detect_request_intent("Qué riesgos ves en este contrato")
    intent = detect_analyst_intent("Qué riesgos ves en este contrato")

    assert operational.domain == "unknown"
    assert intent is not None
    assert intent.analyst_intent == "risk_review"
    assert intent.requires_operational_route is False
    assert (
        intent.conflict_resolution["reason"]
        in {"document_context_analysis", "analyst_intent_match"}
    )


def test_document_comparison_routes_to_analyst() -> None:
    intent = detect_analyst_intent("Compara estos dos documentos")

    assert intent is not None
    assert intent.analyst_intent == "compare"
    assert intent.missing_context == ["uploaded_document"]


def test_operational_finance_request_does_not_route_to_analyst() -> None:
    intent = detect_analyst_intent("Compara gasto 2026 vs 2025 por concepto")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "finance.compare"


def test_cfdi_pending_request_does_not_route_to_analyst() -> None:
    intent = detect_analyst_intent("Qué CFDIs están pendientes")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "cfdi.list_pending"


def test_payment_due_request_does_not_route_to_analyst() -> None:
    intent = detect_analyst_intent("Qué pagos vencen esta semana")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "payments.due_soon"


def test_document_confirmation_command_does_not_route_to_analyst() -> None:
    intent = detect_analyst_intent("CONFIRMAR accion abc123")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "document_confirmation"
    assert (
        intent.conflict_resolution["reason"]
        == "document_confirmation_command"
    )


def test_report_payment_risks_route_to_operational_request() -> None:
    intent = detect_analyst_intent("riesgos del reporte de pagos esta semana")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "payments.list_pending"
    assert intent.conflict_resolution["reason"] == "operational_domain"


def test_finance_compare_with_conclusions_stays_operational() -> None:
    intent = detect_analyst_intent(
        "compara gasto 2026 vs 2025 y dame conclusiones"
    )

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "finance.compare"
    assert intent.conflict_resolution["reason"] == "operational_domain"


def test_cfdi_pending_with_risks_stays_operational() -> None:
    intent = detect_analyst_intent("CFDIs pendientes con riesgos")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "cfdi.list_pending"


def test_document_summary_for_direction_routes_to_analyst() -> None:
    intent = detect_analyst_intent("resume este documento para dirección")

    assert intent is not None
    assert intent.analyst_intent == "summarize"
    assert intent.requires_operational_route is False
    assert intent.conflict_resolution["reason"] == "document_context_analysis"


def test_sow_proposal_comparison_routes_to_analyst() -> None:
    intent = detect_analyst_intent("compara este SOW contra esta propuesta")

    assert intent is not None
    assert intent.analyst_intent == "compare"
    assert intent.requires_operational_route is False
    assert intent.conflict_resolution["reason"] == "document_context_analysis"


def test_create_contract_summary_is_write_like_not_analyst() -> None:
    intent = detect_analyst_intent("crea un resumen de este contrato")

    assert intent is not None
    assert intent.requires_operational_route is True
    assert intent.operational_route_hint == "write_like_action"
    assert intent.conflict_resolution["reason"] == "write_like_action"
