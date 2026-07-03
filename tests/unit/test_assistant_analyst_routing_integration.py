from __future__ import annotations

from samchat.assistant.router import _assistant_classify_request, _assistant_route_system_prompt


def test_analyst_does_not_steal_operational_write_requests():
    route = _assistant_classify_request(
        "Crea una solicitud de pago para balones del torneo sub 17."
    )

    assert route["route"] == "agentic_write"
    assert route["domain"] in {"finance", "mixed"}
    assert route["has_write_intent"] is True


def test_analyst_handles_contract_risk_requests():
    route = _assistant_classify_request("Qué riesgos ves en este contrato")

    assert route["route"] == "reporting"
    assert route["domain"] == "generic"
    prompt = _assistant_route_system_prompt(route)
    assert "Consulta analitica" in prompt
    assert "riesgos" in prompt
