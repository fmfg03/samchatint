"""Adversarial numeric claims, distinct from provider transport regressions."""

import pytest

from samchat.assistant.financial_claims import (
    is_safe_clarification,
    validate_financial_claims,
)
from samchat.assistant.conversation_context import (
    contextual_read_frame,
    contextual_route,
)


@pytest.mark.parametrize(
    "message",
    [
        "el total es 99 mxn",
        "total: 2026 mxn",
        "total: 999 mxn",
        "total: 84 mxn",
        "aumento 100%",
    ],
)
def test_reject_unbound_money_counts_dates_and_unverified_derivations(message):
    assert not validate_financial_claims(
        message,
        [
            (
                "finance_realtime_report",
                {
                    "totals": {"gasto_total": 42, "registros": 999, "moneda": "MXN"},
                    "period": {"year": 2026},
                    "reference": "SOL-999",
                },
            )
        ],
    )[0]


def test_canonical_context_is_never_a_numeric_source():
    assert not validate_financial_claims(
        "99 mxn",
        [
            (
                "assistant_canonical_query",
                {
                    "context": {"amount": 99},
                    "data": {"totals": {"gasto_total": 42}},
                },
            )
        ],
    )[0]
    valid, bindings = validate_financial_claims(
        "total: 42 mxn",
        [
            (
                "assistant_canonical_query",
                {
                    "data": {"totals": {"gasto_total": 42}},
                },
            )
        ],
    )
    assert valid
    assert (
        bindings[0]["source_path"]
        == "assistant_canonical_query.data.totals.gasto_total"
    )


def test_row_cannot_be_promoted_to_population_total():
    rows = [("finance_expense_search", {"monto_total": 42, "gastos": [{"monto": 10}]})]
    assert not validate_financial_claims("total: 10 mxn", rows)[0]
    assert validate_financial_claims("importe: 10 mxn", rows)[0]


@pytest.mark.parametrize(
    "text",
    [
        "¿septiembre 2025 o 2026?",
        "¿te refieres al folio sol-999?",
        "¿buscas documentos aprobados o pagos efectivamente realizados?",
    ],
)
def test_safe_clarification_references(text):
    assert is_safe_clarification(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿confirmas que se pagaron 99 mxn?",
        "¿quieres el total 99?",
        "¿buscas 99?",
        "se pagaron 99. ¿quieres confirmar?",
    ],
)
def test_question_does_not_launder_amount(text):
    assert not is_safe_clarification(text)


@pytest.mark.parametrize(
    "text",
    [
        "Redacta un correo",
        "Hola, ¿cómo estás?",
        "Y escribe una invitación",
        "Cuéntame un chiste",
    ],
)
def test_new_topic_clears_historic_evidence_requirement(text):
    history = [{"role": "user", "content": "Cuánto IVA se pagó en septiembre 2026"}]
    frame = contextual_read_frame(text, history)
    assert frame.domain == "unknown"
    assert not frame.answer_contract["require_current_read_evidence"]
    route = contextual_route(
        text,
        history,
        lambda value: {
            "domain": "finance" if "IVA" in value else "generic",
            "route": "lookup_sql",
        },
        current_domain="finance",
    )
    assert route == {"domain": "generic", "route": "lookup_sql"}


@pytest.mark.parametrize("text", ["¿y agosto?", "por proveedor", "¿por qué aumentó?"])
def test_elliptical_followup_retains_financial_contract_without_authority(text):
    frame = contextual_read_frame(
        text, [{"role": "user", "content": "Cuánto IVA se pagó en septiembre 2026"}]
    )
    assert frame.domain == "finance"
    assert frame.answer_contract["require_current_read_evidence"]
    assert frame.authority_boundary == "read_only"


def test_new_topic_inside_history_resets_old_domain():
    frame = contextual_read_frame(
        "continua",
        [
            {"role": "user", "content": "Cuánto IVA se pagó"},
            {"role": "user", "content": "Redacta un correo"},
        ],
    )
    assert frame.domain == "unknown"


def test_followup_of_new_topic_does_not_resurrect_financial_route():
    history = [
        {"role": "user", "content": "IVA septiembre"},
        {"role": "user", "content": "Redacta un correo"},
    ]
    route = contextual_route(
        "continua",
        history,
        lambda text: {
            "domain": "finance" if "IVA" in text else "generic",
            "route": "lookup_sql",
        },
        current_domain="finance",
    )
    assert route["domain"] == "generic"


@pytest.mark.parametrize(
    "message", ["se pagaron 99", "iva: 99", "total asciende a 99", "total fue de 99"]
)
def test_monetary_assertion_without_currency_still_requires_binding(message):
    assert not validate_financial_claims(
        message, [("finance_realtime_report", {"totals": {"gasto_total": 42}})]
    )[0]


@pytest.mark.parametrize("message", ["iva: 42 mxn", "noventa y nueve pesos pagados"])
def test_no_tax_relabeling_or_unverifiable_verbal_amounts(message):
    assert not validate_financial_claims(
        message, [("finance_realtime_report", {"totals": {"gasto_total": 42}})]
    )[0]


@pytest.mark.parametrize(
    "message", ["se abonaron 99", "son 99", "99", "noventa y nueve pesos"]
)
def test_unrecognized_numeric_claim_does_not_bypass_binding(message):
    assert not validate_financial_claims(
        message, [("finance_realtime_report", {"totals": {"gasto_total": 42}})]
    )[0]


def test_supported_count_is_not_confused_with_money():
    reads = [
        ("finance_realtime_report", {"totals": {"gasto_total": 42, "registros": 999}})
    ]
    assert validate_financial_claims("999 registros", reads)[0]
    assert not validate_financial_claims("42 registros", reads)[0]
    assert not validate_financial_claims("999 mxn", reads)[0]
