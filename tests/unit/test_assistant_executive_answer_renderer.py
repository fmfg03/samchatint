from __future__ import annotations

from samchat.assistant.executive_answer_renderer import render_executive_tool_result
from samchat.assistant.router import _assistant_deterministic_tool_answer


def test_executive_renderer_uses_canonical_conversation_answer() -> None:
    result = {
        "status": "partial",
        "conversation_answer": {
            "rendered_text": "Respuesta ejecutiva del Owner Pack.\nFrontera de autoridad: lectura.",
        },
    }

    answer = render_executive_tool_result("assistant_owner_pack_readiness", result)

    assert answer == "Respuesta ejecutiva del Owner Pack.\nFrontera de autoridad: lectura."
    assert '{"name"' not in answer
    assert "assistant_owner_pack_readiness" not in answer


def test_router_deterministic_answer_renders_owner_variable_tool_result() -> None:
    result = {
        "variable": "equipos reales participantes",
        "conversation_answer": {
            "rendered_text": "En este momento tenemos evidencia parcial de equipos participantes."
        },
    }

    answer = _assistant_deterministic_tool_answer("assistant_owner_variable_query", result)

    assert answer == "En este momento tenemos evidencia parcial de equipos participantes."
    assert '{"name"' not in answer


def test_executive_renderer_synthesizes_structured_report_without_raw_json() -> None:
    result = {
        "headline": "Estado de carpeta de entidad",
        "status": "needs_evidence",
        "summary": "No esta lista para presentarse al dueno.",
        "evidence_found": ["Existe expediente base del torneo"],
        "missing_evidence": ["Faltan equipos reales por categoria", "Faltan pagos del operador"],
        "next_actions": ["Cargar datos operativos reales antes de prometer tablero completo"],
    }

    answer = render_executive_tool_result("assistant_owner_pack_status", result)

    assert answer is not None
    assert "Estado de carpeta de entidad" in answer
    assert "Respuesta corta: No esta lista" in answer
    assert "Lo que todav" in answer
    assert "Frontera de autoridad" in answer
    assert '{"name"' not in answer


def test_executive_renderer_ignores_raw_tool_call_text() -> None:
    result = {"message": '{"name":"assistant_owner_pack_readiness","arguments":{"scope":"all"}}'}

    assert render_executive_tool_result("assistant_owner_pack_readiness", result) is None
