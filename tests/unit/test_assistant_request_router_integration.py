from types import SimpleNamespace

import pytest

from samchat.assistant.conversation_service import (
    run_message_turn_with_pending,
)
from samchat.assistant.router import _maybe_append_export_prompt


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Uniformes", "amount": 1000},
        {"year": 2026, "concepto": "Uniformes", "amount": 1250},
    ]


async def _empty_finance_rows(_intent):
    return []


async def _run_message(
    raw_message,
    *,
    finance_rows_provider=None,
    executor=None,
    assistant_turn=_provider_must_not_be_called,
):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-request", updated_at=None),
        current_empleado=SimpleNamespace(id="emp-1", rol="admin"),
        session=_FakeSession(),
        request=None,
        tournament_key=None,
        bi_year=None,
        bi_scope=None,
        bi_segment=None,
        assistant_mode=None,
        openai_api_key=None,
        latest_pending_run_for_conversation=_pending_none,
        is_explicit_approval_message=lambda _text: False,
        is_explicit_rejection_message=lambda _text: False,
        confirm_pending_run=_provider_must_not_be_called,
        deterministic_pending_builders=[],
        build_deterministic_pending_response=_provider_must_not_be_called,
        assistant_turn=assistant_turn,
        maybe_append_export_prompt=_maybe_append_export_prompt,
        document_action_router_executor=executor,
        finance_rows_provider=finance_rows_provider,
    )


@pytest.mark.asyncio
async def test_deterministic_finance_request_bypasses_provider_and_exports():
    response = await _run_message(
        "Compara gasto 2026 vs 2025 por concepto",
        finance_rows_provider=_finance_rows,
    )

    assert (
        "Comparación de gasto por concepto, 2026 vs 2025" in response.assistant_message
    )
    assert "Uniformes" in response.assistant_message
    assert (
        "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF."
        in response.assistant_message
    )
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["domain"] == "finance"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_empty_finance_request_has_no_export_or_provider_fallback():
    response = await _run_message(
        "gasto por concepto 2026 vs 2025",
        finance_rows_provider=_empty_finance_rows,
    )

    assert "No encontré datos suficientes" in response.assistant_message
    assert "¿Quieres que te lo exporte" not in response.assistant_message
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["status"] == "empty"


@pytest.mark.asyncio
async def test_cfdi_request_uses_read_only_executor_not_provider():
    calls = []

    async def executor(action, payload):
        calls.append((action, payload))
        return {
            "summary": "CFDIs pendientes",
            "data": {
                "title": "CFDIs pendientes",
                "rows": [{"uuid": "A", "status": "pending"}],
            },
        }

    response = await _run_message(
        "Qué CFDIs están pendientes",
        executor=executor,
    )

    assert calls == [
        ("receipts.cfdi_matching_overview", {"view": "pending", "limit": 50})
    ]
    assert "CFDIs pendientes" in response.assistant_message
    assert (
        "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF."
        in response.assistant_message
    )
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["provider_called"] is False


@pytest.mark.asyncio
async def test_payments_request_without_executor_fails_closed_no_provider():
    response = await _run_message("Qué pagos vencen esta semana")

    assert "executor read-only disponible" in response.assistant_message
    assert "¿Quieres que te lo exporte" not in response.assistant_message
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["domain"] == "payments"
    assert trace["status"] == "data_source_unavailable"


@pytest.mark.asyncio
async def test_pending_payment_result_is_rendered_without_internal_action_name():
    async def executor(action, payload):
        assert action == "receipts.pending_payment_overview"
        return {
            "status": "completed",
            "data": {
                "pending_count": 3,
                "total_pendiente": 35522.16,
                "solicitud_terceros": 3,
                "solicitud_personal": 0,
            },
        }

    response = await _run_message("Qué pagos están pendientes", executor=executor)

    assert "Pagos pendientes" in response.assistant_message
    assert "3 solicitudes pendientes por $35,522.16" in response.assistant_message
    assert "pending_payment_overview" not in response.assistant_message
    assert "{'" not in response.assistant_message
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["provider_called"] is False


@pytest.mark.asyncio
async def test_no_write_or_adapter_execution_for_read_only_request():
    write_calls = []

    async def executor(action, payload):
        write_calls.append((action, payload))
        assert action == "receipts.cfdi_matching_overview"
        return {"data": {"rows": []}, "summary": "Sin datos"}

    response = await _run_message("Facturas sin vincular", executor=executor)

    assert write_calls == [
        ("receipts.cfdi_matching_overview", {"view": "unlinked", "limit": 50})
    ]
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["actions_executed"] == []
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_capability_question_does_not_execute_pending_payment_query(
    monkeypatch,
):
    monkeypatch.setenv("ASSISTANT_CAPABILITY_NEGOTIATION_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED", "false")
    calls = []

    async def executor(action, payload):  # pragma: no cover - must not run
        calls.append((action, payload))
        return {"data": {"pending_count": 3}}

    response = await _run_message(
        "si te subo un comprobante puedes hacerme la cuenta de gastos "
        "y la solicitud de pago?",
        executor=executor,
    )

    assert calls == []
    assert "Puedo leer el comprobante" in response.assistant_message
    assert "pending_payment_overview" not in response.assistant_message
    trace = response.tool_trace[0]["capability_negotiation"]
    assert trace["operational_tools_called"] == 0
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_owner_ai_folder_definition_uses_owner_pack_without_provider():
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("owner pack should bypass provider")

    response = await _run_message(
        "Que debe contener una carpeta por entidad para cualquier torneo?",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Estructura propuesta" in response.assistant_message
    assert "Operaciones" in response.assistant_message
    assert "Nombre de la entidad" in response.assistant_message
    assert "Equipos esperados por categoria/genero" in response.assistant_message
    assert "Finanzas" in response.assistant_message
    assert "Ayudas y pagos sucesivos al operador" in response.assistant_message
    assert "Checklist accionable" in response.assistant_message
    assert "Estado de datos" in response.assistant_message
    assert "no inventa informacion" in response.assistant_message
    assert "Superficies disponibles" in response.assistant_message
    assert "/admin/sports/expediente-entidades" in response.assistant_message
    assert "Preguntas para avanzar" in response.assistant_message
    assert "Que evidencia quieres cargar" in response.assistant_message
    assert "Frontera de autoridad" in response.assistant_message
    trace = response.tool_trace[0]["owner_operator_workflow"]
    assert trace["stage"] == "deterministic_read_only_owner_pack"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0
    assert trace["side_effects_detected"] == 0


@pytest.mark.asyncio
async def test_owner_ai_owner_needs_brief_uses_owner_pack_without_provider():
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("owner needs brief should bypass provider")

    response = await _run_message(
        "Cosas que necesito que me de la IA para todos y cada uno "
        "de los torneos: una carpeta por cada entidad.",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Estructura propuesta" in response.assistant_message
    assert "Checklist accionable" in response.assistant_message
    assert "Estado de datos" in response.assistant_message
    assert "no inventa informacion" in response.assistant_message
    assert "Superficies disponibles" in response.assistant_message
    assert "Frontera de autoridad" in response.assistant_message
    trace = response.tool_trace[0]["owner_operator_workflow"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0


@pytest.mark.asyncio
async def test_owner_ai_prepared_dashboards_message_is_data_gap_aware():
    response = await _run_message(
        "Ya estan preparados los tableros para el dueno, pero falta informacion?"
    )

    assert "Estado de datos" in response.assistant_message
    assert "ya estan preparados" in response.assistant_message
    assert "no inventa informacion" in response.assistant_message
    trace = response.tool_trace[0]["owner_operator_workflow"]
    assert trace["provider_called"] is False


@pytest.mark.asyncio
async def test_specialist_preview_surface_bypasses_provider_and_returns_render_contract():
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("specialist preview should bypass provider")

    response = await _run_message(
        "Muestra preview especialista SAMCHAT-CXC-COLLECTION-001",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Preview especialista listo" in response.assistant_message
    assert "Frontera de autoridad" in response.assistant_message
    assert response.preview_render["task_id"] == "SAMCHAT-CXC-COLLECTION-001"
    assert response.preview_render["primary_action_enabled"] is False
    trace = response.tool_trace[0]["specialist_preview_surface"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_registration_executive_report_bypasses_provider_and_is_exportable():
    calls = []

    async def executor(action, payload):
        calls.append((action, payload))
        return {
            "status": "completed",
            "data": {
                "title": "Reportes de cedulas por torneo",
                "reports": {
                    "juvenil_edades": [
                        {
                            "estado": "Jalisco",
                            "municipio": "Zapopan",
                            "edad_15": 1,
                            "edad_16": 2,
                            "edad_17": 3,
                        }
                    ]
                },
                "caveats": ["FMF/Liga MX y RENAPO se reportan como unavailable."],
            },
        }

    response = await _run_message(
        "Dame reportes de cédulas capturadas de CTT",
        executor=executor,
    )

    assert calls[0][0] == "operations.tournament_registration_executive_reports"
    assert "Reportes de cedulas por torneo" in response.assistant_message
    assert "juvenil_edades" in response.assistant_message
    assert (
        "¿Quieres que te lo exporte ahora? Responde Excel (CSV) o PDF."
        in response.assistant_message
    )
    trace = response.tool_trace[0]["request_intelligence_live_wiring"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
