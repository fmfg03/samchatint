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



def test_executive_renderer_renders_finance_realtime_report_without_provider_roundtrip() -> None:
    result = {
        "title": "Proyección de cierre 2026",
        "period": {"from": "2026-01-01", "to": "2026-12-31"},
        "totals": {"gasto_total": 12500.5, "registros": 7, "moneda": "MXN"},
        "budget": {
            "budget_total": 20000.0,
            "variance_amount": 7499.5,
            "variance_pct": -37.5,
            "source": "solicitudes",
        },
        "projection": {
            "mode": "run_rate",
            "run_rate_daily": 250.01,
            "projected_total": 91003.64,
        },
        "breakdown": {
            "group_by": "departamento",
            "items": [
                {"departamento": "Operaciones", "registros": 4, "monto": 10000.0},
                {"departamento": "Finanzas", "registros": 3, "monto": 2500.5},
            ],
        },
        "comparison_yoy": [
            {
                "period": {"from": "2025-01-01", "to": "2025-12-31"},
                "total": 9000.0,
                "delta_vs_current_amount": 3500.5,
                "delta_vs_current_pct": 38.89,
            }
        ],
    }

    answer = _assistant_deterministic_tool_answer("finance_realtime_report", result)

    assert answer is not None
    assert "Proyección de cierre 2026" in answer
    assert "7 movimientos" in answer
    assert "MXN $12,500.50" in answer
    assert "Proyección run-rate: MXN $91,003.64" in answer
    assert "Operaciones: MXN $10,000.00" in answer
    assert "sin depender de una segunda respuesta del proveedor" in answer
    assert '{"name"' not in answer
