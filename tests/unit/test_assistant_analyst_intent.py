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
