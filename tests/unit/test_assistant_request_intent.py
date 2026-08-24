from samchat.assistant.request_intent import (
    detect_request_intent,
    is_owner_ai_conceptual_request,
    is_owner_ai_context_request,
    is_owner_ai_readiness_request,
)
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


def test_owner_ai_folder_definition_is_not_deterministic_tournament_status():
    text = "Que debe contener una carpeta por entidad para cualquier torneo?"

    assert is_owner_ai_conceptual_request(text) is True
    intent = detect_request_intent(text)

    assert intent.domain == "unknown"


def test_owner_ai_specific_need_is_context_request():
    assert is_owner_ai_context_request(
        "Cuando y donde se entregan uniformes de fase estatal para Veracruz?"
    )
    assert is_owner_ai_context_request(
        "Dime hoteles contratados y camas-noche para la fase nacional de basquet."
    )


def test_owner_ai_owner_needs_brief_is_context_request_not_generic_executive():
    text = (
        "Cosas que necesito que me de la IA para todos y cada uno "
        "de los torneos: una carpeta por cada entidad."
    )

    assert is_owner_ai_context_request(text) is True
    assert is_owner_ai_conceptual_request(text) is True
    intent = detect_request_intent(text)

    assert intent.domain == "unknown"


def test_owner_ai_director_general_dashboard_is_context_request():
    text = "Prepara el tablero del Director General con carpetas por entidad."

    assert is_owner_ai_context_request(text) is True
    assert is_owner_ai_conceptual_request(text) is False


def test_plain_executive_summary_does_not_become_owner_pack():
    text = "Hazme un resumen para direccion"

    assert is_owner_ai_context_request(text) is False
    intent = detect_request_intent(text)

    assert intent.domain == "executive"


def test_owner_pack_readiness_detects_pack_del_dueno_question():
    text = "tenemos listo el pack del dueno?"

    assert is_owner_ai_context_request(text) is True
    assert is_owner_ai_readiness_request(text) is True
    intent = detect_request_intent(text)

    assert intent.domain == "unknown"


def test_owner_pack_readiness_detects_pack_del_dueno_without_accent():
    text = "ya esta preparado el pack del dueno?"

    assert is_owner_ai_context_request(text) is True
    assert is_owner_ai_readiness_request(text) is True


def test_owner_pack_readiness_detects_broad_owner_data_question():
    text = "ya tenemos datos para el dueño?"

    assert is_owner_ai_context_request(text) is True
    assert is_owner_ai_readiness_request(text) is True


def test_owner_dashboard_readiness_phrase_stays_on_dashboard_route():
    text = "Ya estan preparados los tableros para el dueno, pero falta informacion?"

    assert is_owner_ai_context_request(text) is True
    assert is_owner_ai_readiness_request(text) is False
