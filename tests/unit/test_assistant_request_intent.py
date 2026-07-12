from samchat.assistant.request_intent import detect_request_intent
from samchat.assistant.request_router import route_request


def test_detects_finance_comparison_request() -> None:
    intent = detect_request_intent("Compara gasto 2026 vs 2025 por concepto")
    route = route_request(intent)

    assert intent.domain == "finance"
    assert intent.intent == "compare"
    assert intent.slots["metric"] == "gasto"
    assert intent.slots["years"] == [2026, 2025]
    assert intent.slots["group_by"] == "concepto"
    assert route.requires_provider is False


def test_detects_pending_cfdi_request() -> None:
    intent = detect_request_intent("Qué CFDIs están pendientes")

    assert intent.domain == "cfdi"
    assert intent.intent == "list_pending"


def test_detects_due_payments_request() -> None:
    intent = detect_request_intent("Qué pagos vencen esta semana")

    assert intent.domain == "payments"
    assert intent.intent == "due_soon"
    assert intent.slots["period"] == "this_week"


def test_detects_incomplete_team_documents_request() -> None:
    intent = detect_request_intent("Qué equipos tienen documentos incompletos")

    assert intent.domain == "tournament"
    assert intent.intent == "list_pending"
    assert intent.slots["metric"] == "team_documents"


def test_detects_executive_summary_request() -> None:
    intent = detect_request_intent("Hazme un resumen para dirección")

    assert intent.domain == "executive"
    assert intent.intent == "summarize"


def test_unsupported_ambiguous_request_needs_clarification_contract() -> None:
    intent = detect_request_intent("Haz lo de ayer")
    route = route_request(intent)

    assert intent.domain == "unknown"
    assert route.type == "clarification"
    assert route.requires_provider is False


def test_contract_risk_review_is_not_executive_request() -> None:
    intent = detect_request_intent("Qué riesgos ves en este contrato")

    assert intent.domain == "unknown"


def test_report_payment_risk_request_stays_operational() -> None:
    intent = detect_request_intent("riesgos del reporte de pagos esta semana")

    assert intent.domain == "payments"
    assert intent.intent == "list_pending"
