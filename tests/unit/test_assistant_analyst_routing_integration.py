from types import SimpleNamespace

import pytest

import samchat.assistant.router as assistant_router
from samchat.assistant.conversation_service import (
    run_message_turn_with_pending,
)
from samchat.assistant.router import _maybe_append_export_prompt


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, latest_contents=None):
        self.added = []
        self.commits = 0
        self.latest_contents = latest_contents or []

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        rows = [SimpleNamespace(content=item) for item in self.latest_contents]
        return _FakeExecuteResult(rows)


async def _pending_none(**_kwargs):
    return None


async def _provider_must_not_be_called(**_kwargs):  # pragma: no cover
    raise AssertionError("provider path should not be called")


async def _finance_rows(_intent):
    return [
        {"year": 2025, "concepto": "Hospedaje", "amount": 1000},
        {"year": 2026, "concepto": "Hospedaje", "amount": 1500},
    ]


async def _run_message(
    raw_message,
    *,
    session=None,
    finance_rows_provider=None,
    live_evidence_rows_provider=None,
    current_empleado=None,
    executor=None,
):
    return await run_message_turn_with_pending(
        raw_message=raw_message,
        conversation=SimpleNamespace(id="conv-analyst", updated_at=None),
        current_empleado=current_empleado
        or SimpleNamespace(
            id="emp-1",
            rol="empleado",
            permissions=set(),
        ),
        session=session or _FakeSession(),
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
        assistant_turn=_provider_must_not_be_called,
        maybe_append_export_prompt=_maybe_append_export_prompt,
        document_action_router_executor=executor,
        finance_rows_provider=finance_rows_provider,
        live_evidence_rows_provider=live_evidence_rows_provider,
    )


@pytest.mark.asyncio
async def test_analyst_needs_context_no_provider(monkeypatch):
    monkeypatch.delenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        raising=False,
    )
    response = await _run_message("Explícame esta balanza")

    assert "Necesito contexto para analizar" in response.assistant_message
    assert "subas, pegues o selecciones" in response.assistant_message
    trace = response.tool_trace[0]["analyst_workbench_live_wiring"]
    assert trace["status"] == "needs_context"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
    assert "analyst_live_evidence" not in response.tool_trace[0]


def test_disabled_live_evidence_does_not_initialize_database(monkeypatch):
    monkeypatch.delenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        raising=False,
    )

    def fail_if_called():  # pragma: no cover
        raise AssertionError("database session maker must remain dormant")

    monkeypatch.setattr(
        assistant_router,
        "get_expenses_session_maker",
        fail_if_called,
    )

    assert assistant_router._configured_live_evidence_rows_provider() is None


@pytest.mark.asyncio
async def test_analyst_uses_latest_document_context_without_provider():
    context = (
        "DOCUMENT_INTAKE_RESULT JSON:\n"
        '{"detected_document_type":"accounting_balance",'
        '"summary":"Balanza mayo 2026 con descuadre cero",'
        '"missing_fields":[]}\n\n'
        "Archivo procesado."
    )
    response = await _run_message(
        "Explícame esta balanza",
        session=_FakeSession(latest_contents=[context]),
    )

    assert (
        "Explicación con el contexto disponible"
        in response.assistant_message
    )
    assert "Balanza mayo 2026" in response.assistant_message
    trace = response.tool_trace[0]["analyst_workbench_live_wiring"]
    assert trace["status"] == "success"
    assert trace["provider_called"] is False
    assert trace["evidence_types"] == ["document_intake"]
    assert trace["evidence_rank_scores"][0] > 0
    assert trace["evidence_rank_reasons"][0]


@pytest.mark.asyncio
async def test_analyst_uses_authorized_live_evidence(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,budgets",
    )
    calls = []

    async def live_rows(_context, sources):
        calls.append(sources)
        return {
            "expenses": [
                {
                    "id": "gasto-live-1",
                    "label": "Hospedaje Nacional",
                    "summary": (
                        "Gasto de hospedaje del proyecto nacional por "
                        "2,500 MXN."
                    ),
                    "date": "2026-07-10",
                    "metadata": {
                        "amount": 2500,
                        "currency": "MXN",
                    },
                }
            ]
        }

    response = await _run_message(
        "Explícame el gasto de este caso",
        live_evidence_rows_provider=live_rows,
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="empleado",
            permissions={"gastos:read"},
        ),
    )

    assert calls == [{"expenses"}]
    assert "Hospedaje Nacional" in response.assistant_message
    assert "evidencia en vivo autorizada" in response.assistant_message
    assert "No revisé datos vivos" not in response.assistant_message
    trace = response.tool_trace[0]["analyst_live_evidence"]
    assert trace["enabled"] is True
    assert trace["allowed_sources"] == ["expenses"]
    assert trace["denied_sources"] == []
    assert trace["source_counts"] == {"expenses": 1}
    assert trace["evidence_count"] == 1
    workbench_trace = response.tool_trace[0][
        "analyst_workbench_live_wiring"
    ]
    assert workbench_trace["evidence_labels"] == ["expense"]
    assert "Hospedaje Nacional" not in str(response.tool_trace)
    assert "gasto-live-1" not in str(response.tool_trace)


@pytest.mark.parametrize(
    ("question", "source", "permission"),
    (
        ("Explica el CFDI UUID-OLD", "cfdi_documents", "cfdi:read"),
        (
            "Explica el pago REF-OLD",
            "registered_payments",
            "pagos:read",
        ),
        (
            "Explica el torneo Nacional",
            "projects",
            "proyectos:read",
        ),
    ),
)
@pytest.mark.asyncio
async def test_enabled_live_operational_explanations_reach_analyst(
    monkeypatch,
    question,
    source,
    permission,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        source,
    )
    calls = []

    async def live_rows(_context, sources):
        calls.append(sources)
        return {
            source: [
                {
                    "id": "live-finance-1",
                    "label": "Evidencia financiera solicitada",
                    "summary": "Registro financiero autorizado.",
                    "date": "2026-07-10",
                    "metadata": {},
                }
            ]
        }

    response = await _run_message(
        question,
        live_evidence_rows_provider=live_rows,
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="empleado",
            permissions={permission},
        ),
    )

    assert calls == [{source}]
    assert "Evidencia financiera solicitada" in response.assistant_message
    trace = response.tool_trace[0]["analyst_live_evidence"]
    assert trace["attempted_sources"] == [source]
    assert trace["source_counts"] == {source: 1}


@pytest.mark.parametrize(
    ("question", "source", "permission"),
    (
        (
            "Expl\u00edcame qu\u00e9 pagos est\u00e1n pendientes",
            "registered_payments",
            "pagos:read",
        ),
        (
            "Expl\u00edcame qu\u00e9 pagos vencen",
            "registered_payments",
            "pagos:read",
        ),
        (
            "Expl\u00edcame los CFDI sin vincular",
            "cfdi_documents",
            "cfdi:read",
        ),
    ),
)
@pytest.mark.asyncio
async def test_status_explanation_stays_operational(
    monkeypatch,
    question,
    source,
    permission,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        source,
    )

    async def live_rows(_context, _sources):  # pragma: no cover
        raise AssertionError("pending report must not query paid evidence")

    response = await _run_message(
        question,
        live_evidence_rows_provider=live_rows,
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="empleado",
            permissions={permission},
        ),
    )

    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_live_evidence" not in response.tool_trace[0]
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]


@pytest.mark.parametrize(
    ("configured_sources", "live_rows"),
    (
        (
            "projects",
            None,
        ),
        (
            "registered_payments",
            "empty",
        ),
    ),
)
@pytest.mark.asyncio
async def test_live_explanation_falls_back_when_evidence_unavailable(
    monkeypatch,
    configured_sources,
    live_rows,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        configured_sources,
    )
    calls = []

    async def provider(_context, sources):
        calls.append(sources)
        return {"registered_payments": []}

    response = await _run_message(
        "Explica el pago REF-1",
        live_evidence_rows_provider=(
            provider if live_rows == "empty" else None
        ),
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="empleado",
            permissions={"pagos:read"},
        ),
    )

    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]
    if live_rows == "empty":
        assert calls == [{"registered_payments"}]
    else:
        assert calls == []


@pytest.mark.asyncio
async def test_ambiguous_follow_up_preserves_history_without_live_reads(
    monkeypatch,
):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,cfdi_documents,budgets,registered_payments,documents",
    )

    async def live_rows(_context, _sources):  # pragma: no cover
        raise AssertionError("ambiguous follow-up must not query live sources")

    response = await _run_message(
        "¿Qué implica?",
        session=_FakeSession(
            latest_contents=[
                "El contrato previo mantiene una penalización abierta y "
                "no define al responsable de aceptación."
            ]
        ),
        live_evidence_rows_provider=live_rows,
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="superadmin",
            permissions={"*"},
        ),
    )

    live_trace = response.tool_trace[0]["analyst_live_evidence"]
    assert live_trace["attempted_sources"] == []
    assert live_trace["provider_called"] is False
    workbench_trace = response.tool_trace[0][
        "analyst_workbench_live_wiring"
    ]
    assert workbench_trace["evidence_types"] == ["conversation"]


@pytest.mark.asyncio
async def test_live_claim_requires_live_item_in_final_pack(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "projects",
    )
    history = [
        (
            "DOCUMENT_INTAKE_RESULT JSON:\n"
            '{"detected_document_type":"contract",'
            f'"summary":"Contrato {index} con obligaciones, responsables, '
            'fechas, montos, riesgos y evidencia suficiente para análisis.",'
            '"missing_fields":[]}\n\n'
            "Archivo procesado."
        )
        for index in range(6)
    ]

    async def live_rows(_context, _sources):
        return {
            "projects": [
                {
                    "id": "project-live-1",
                    "label": "Proyecto",
                    "summary": "Proyecto activo.",
                    "metadata": {},
                }
            ]
        }

    response = await _run_message(
        "Explica el torneo Nacional",
        session=_FakeSession(latest_contents=history),
        live_evidence_rows_provider=live_rows,
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="superadmin",
            permissions={"*"},
        ),
    )

    assert "evidencia en vivo autorizada" not in response.assistant_message
    assert "No revis\u00e9 datos vivos" in response.assistant_message
    workbench_trace = response.tool_trace[0][
        "analyst_workbench_live_wiring"
    ]
    assert "project" not in workbench_trace["evidence_types"]


@pytest.mark.asyncio
async def test_partial_live_evidence_warning_is_rendered(monkeypatch):
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        "expenses,documents",
    )

    async def live_rows(_context, sources):
        source = next(iter(sources))
        if source == "documents":
            raise RuntimeError("document source unavailable")
        return {
            "expenses": [
                {
                    "id": "gasto-live-2",
                    "label": "Transporte Nacional",
                    "summary": (
                        "Gasto de transporte del proyecto nacional por "
                        "1,200 MXN."
                    ),
                    "date": "2026-07-10",
                    "metadata": {
                        "amount": 1200,
                        "currency": "MXN",
                    },
                }
            ]
        }

    response = await _run_message(
        "Explícame el gasto y el documento financiero de este caso",
        live_evidence_rows_provider=live_rows,
        current_empleado=SimpleNamespace(
            id="emp-1",
            rol="empleado",
            permissions={"gastos:read", "documentos:read"},
        ),
    )

    assert "evidencia en vivo es parcial" in response.assistant_message
    trace = response.tool_trace[0]["analyst_live_evidence"]
    assert trace["failed_sources"] == ["documents"]


@pytest.mark.asyncio
async def test_analyst_uses_inline_context_before_history_no_provider():
    response = await _run_message(
        "Qué riesgos ves en este contrato: "
        "El contrato no define responsable de aceptación, "
        "omite fecha límite y deja penalizaciones abiertas.",
        session=_FakeSession(
            latest_contents=[
                "Contexto previo suficientemente largo para ser considerado "
                "evidencia secundaria de conversación."
            ]
        ),
    )

    assert "Riesgos visibles" in response.assistant_message
    assert "Respuesta:" in response.assistant_message
    assert "Soporte en evidencia:" in response.assistant_message
    assert "Límites:" in response.assistant_message
    assert "contexto inline" in response.assistant_message
    assert "responsable de aceptación" in response.assistant_message
    trace = response.tool_trace[0]["analyst_workbench_live_wiring"]
    assert trace["status"] == "success"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
    assert trace["evidence_types"][0] == "inline_context"
    assert trace["evidence_labels"][0] == "contexto inline"
    assert "conversation" in trace["evidence_types"]
    assert trace["evidence_rank_scores"][0] > trace["evidence_rank_scores"][1]
    assert "risk_review_terms" in trace["evidence_rank_reasons"][0]
    assert trace["evidence_diagnostic_count"] == 2
    assert trace["evidence_diagnostics"][0]["source_type"] == "inline_context"
    assert trace["evidence_diagnostics"][0]["rank_score"] == (
        trace["evidence_rank_scores"][0]
    )
    assert trace["evidence_diagnostics"][0]["clipped"] is False
    assert trace["coverage_level"] in {"medium", "high"}
    assert trace["coverage_reasons"] in (
        ["supported_context"],
        ["multi_source_high_relevance"],
    )
    assert trace["overclaim_guard_applied"] is False
    assert trace["answer_contract_version"] == "analyst_answer_contract_v1"
    assert trace["answer_contract_status"] == "success"
    assert trace["selected_route"] == "analyst"
    assert trace["conflict_reason"] == "document_context_analysis"


@pytest.mark.asyncio
async def test_operational_finance_route_wins_over_analyst():
    response = await _run_message(
        "Compara gasto 2026 vs 2025 por concepto",
        finance_rows_provider=_finance_rows,
    )

    assert "Comparación de gasto por concepto" in response.assistant_message
    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]


@pytest.mark.asyncio
async def test_operational_finance_route_wins_with_inline_context_words():
    response = await _run_message(
        "Compara gasto 2026 vs 2025 por concepto: "
        "quiero riesgos y conclusiones del reporte",
        finance_rows_provider=_finance_rows,
    )

    assert "Comparación de gasto por concepto" in response.assistant_message
    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]


@pytest.mark.asyncio
async def test_document_hybrid_enters_analyst_with_conflict_trace():
    response = await _run_message(
        "resume este documento para dirección: "
        "Contrato con obligaciones, responsables y fechas de entrega.",
    )

    assert "Analyst Workbench" in response.assistant_message
    trace = response.tool_trace[0]
    assert trace.get("analyst_workbench_live_wiring")
    resolution = trace["analyst_intent"]["conflict_resolution"]
    assert resolution["selected_route"] == "analyst"
    assert resolution["reason"] == "document_context_analysis"
    wiring = trace["analyst_workbench_live_wiring"]
    assert wiring["selected_route"] == "analyst"
    assert wiring["conflict_reason"] == "document_context_analysis"
    assert wiring["answer_contract_status"] == "success"


@pytest.mark.asyncio
async def test_cfdi_request_route_wins_over_analyst():
    calls = []

    async def executor(action, payload):
        calls.append((action, payload))
        return {
            "summary": "CFDI pendientes",
            "data": {"rows": [{"uuid": "A"}]},
        }

    response = await _run_message(
        "Qué CFDIs están pendientes",
        executor=executor,
    )

    assert calls == [
        ("receipts.cfdi_matching_overview", {"view": "pending", "limit": 50})
    ]
    assert response.tool_trace[0].get("request_intelligence_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]


@pytest.mark.asyncio
async def test_document_confirmation_command_wins_over_analyst():
    response = await _run_message("CONFIRMAR accion abc123")

    assert (
        "No encontre una accion documental propuesta"
        in response.assistant_message
    )
    assert response.tool_trace[0].get("document_confirmation_live_wiring")
    assert "analyst_workbench_live_wiring" not in response.tool_trace[0]
