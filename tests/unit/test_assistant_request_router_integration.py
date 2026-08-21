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
async def test_owner_pack_readiness_question_uses_readiness_tool_without_provider():
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("owner readiness should bypass provider")

    response = await _run_message(
        "Que falta para contestarle al Director General sobre Copa Telmex?",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Readiness del Owner Pack" in response.assistant_message
    assert "Faltantes para poder contestar sin inventar" in response.assistant_message
    assert "torneo=copa-telmex" in response.assistant_message
    assert "Frontera de autoridad" in response.assistant_message
    trace = response.tool_trace[0]["owner_pack_readiness"]
    assert trace["stage"] == "deterministic_read_only_owner_pack_readiness"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0
    assert response.tool_trace[0]["tool"] == "assistant_owner_pack_readiness"


@pytest.mark.asyncio
async def test_owner_variable_question_uses_conversation_answer_without_provider():
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("owner variable query should bypass provider")

    response = await _run_message(
        "Cuantos equipos reales tiene Copa Telmex para el dueno?",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Se que variable necesitas" in response.assistant_message
    assert "Equipos reales participantes" in response.assistant_message
    assert "no hay evidencia viva suficiente" in response.assistant_message
    assert "No ejecute cambios" in response.assistant_message
    trace = response.tool_trace[0]["owner_variable_query"]
    assert trace["stage"] == "deterministic_read_only_owner_variable_query"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0
    assert trace["side_effects_detected"] == 0
    assert response.tool_trace[0]["tool"] == "assistant_owner_variable_query"
    assert response.tool_trace[0]["result"]["conversation_answer"]["status"] == "missing"


@pytest.mark.asyncio
async def test_owner_variable_question_uses_live_evidence_when_available(monkeypatch):
    import samchat.assistant.conversation_service as conversation_service
    from samchat.assistant.owner_pack_live_evidence import (
        OwnerPackLiveEvidenceResolution,
    )
    from samchat.assistant.owner_pack_live_snapshot import (
        OWNER_PACK_LIVE_SUPPORTED,
        OwnerPackLiveFieldSnapshot,
        OwnerPackLiveSnapshotReport,
        OwnerPackLiveSurfaceSnapshot,
    )

    async def fake_live_evidence(session, *, scope, tournament_hint, entity_name):
        assert tournament_hint == "copa-telmex"
        field = OwnerPackLiveFieldSnapshot(
            field="real_teams",
            label="Equipos reales participantes",
            section_id="operations",
            evidence_type="count",
            status=OWNER_PACK_LIVE_SUPPORTED,
            value=[
                {
                    "category": "Sub-17",
                    "gender_or_branch": "Varonil",
                    "teams_count_total": 18,
                }
            ],
            source_paths=["db.copa_telmex.teams"],
            source_files=["samchat_local_tournament_db"],
        )
        surface = OwnerPackLiveSurfaceSnapshot(
            surface_id="entity_folder",
            label="Carpeta de entidad",
            target={"tournament_name": "Copa Telmex Telcel de Futbol"},
            workspace_root="samchat_local_tournament_db",
            workspace_files_checked=["samchat_local_tournament_db"],
            workspace_files_found=["samchat_local_tournament_db"],
            fields=[field],
            supported_field_count=1,
            missing_field_count=0,
        )
        report = OwnerPackLiveSnapshotReport(
            snapshot_id="owner_pack_live_evidence_v2_entity_folder",
            headline="Evidencia viva local",
            summary="ok",
            surfaces=[surface],
            supported_field_count=1,
            missing_field_count=0,
        )
        return OwnerPackLiveEvidenceResolution(status="resolved", reports=[report])

    monkeypatch.setattr(
        conversation_service,
        "resolve_owner_pack_live_evidence",
        fake_live_evidence,
    )

    response = await _run_message(
        "Cuantos equipos reales tiene Copa Telmex para el dueno?"
    )

    assert "Si tengo ese dato" in response.assistant_message
    assert "18" in response.assistant_message
    assert "db.copa_telmex.teams" in response.assistant_message
    trace = response.tool_trace[0]["owner_variable_query"]
    assert trace["live_evidence_status"] == "resolved"
    assert trace["live_evidence_reports"] == 1
    assert trace["provider_called"] is False
    assert response.tool_trace[0]["result"]["conversation_answer"]["status"] == "supported"


@pytest.mark.asyncio
async def test_unmapped_owner_like_question_falls_through_to_provider():
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            assistant_message="provider fallback",
            tool_trace=[],
            run_id="provider",
            pending_confirmation=None,
        )

    response = await _run_message(
        "Le fue bien el ambiente al dueno?",
        assistant_turn=provider,
    )

    assert calls
    assert response.assistant_message == "provider fallback"


def _owner_workspace_source():
    return SimpleNamespace(
        schema_version="local.v1",
        source_hash="sha256:test-owner-workspace",
        domain_write_performed=False,
        project=SimpleNamespace(
            id="tor-ctt",
            name="Copa Telmex Telcel de Futbol",
            categorias=["Juvenil"],
            etapas=["Inscripcion"],
        ),
        operations_link=SimpleNamespace(operations_tournament_slug="copa-telmex"),
        observed_operations=SimpleNamespace(
            scope_slug="copa-telmex",
            teams_count=12,
            players_count=180,
            categories=["Juvenil"],
            branches=["Varonil"],
            states=["CDMX"],
            municipalities=["Cuauhtemoc"],
        ),
        unavailable_components=["rich_tournament_dates"],
    )


@pytest.mark.asyncio
async def test_owner_entity_folder_workspace_routes_specific_entity_request_without_provider(
    monkeypatch,
):
    import samchat.assistant.conversation_service as conversation_service

    seen_candidates = []

    async def fake_inspect(session, *, tournament_id=None, tournament_name=None):
        assert tournament_id is None
        seen_candidates.append(tournament_name)
        if tournament_name in {"copa-telmex", "Copa Telmex"}:
            return _owner_workspace_source()
        raise conversation_service.TournamentSourceNotFoundError("not found")

    monkeypatch.setattr(conversation_service, "inspect_tournament_source", fake_inspect)
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("owner entity workspace should bypass provider")

    response = await _run_message(
        "Prepara la carpeta de entidad: CDMX. Torneo Copa Telmex para el Director General",
        assistant_turn=provider,
    )

    assert calls == []
    assert seen_candidates
    assert "Owner Entity Folder Workspace" in response.assistant_message
    assert "Objetivo revisado" in response.assistant_message
    assert "entidad=CDMX" in response.assistant_message
    assert "Tarjetas del workspace" in response.assistant_message
    assert "Secciones de la carpeta" in response.assistant_message
    assert "Faltantes para no inventar" in response.assistant_message
    assert "Preview / frontera de autoridad" in response.assistant_message
    assert "no crea carpetas" in response.assistant_message
    trace = response.tool_trace[0]["owner_entity_folder_workspace"]
    assert trace["stage"] == "deterministic_read_only_owner_entity_folder_workspace"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0
    assert trace["side_effects_detected"] == 0
    assert response.tool_trace[0]["tool"] == "assistant_owner_entity_folder_workspace"

@pytest.mark.asyncio
async def test_owner_pack_readiness_without_tournament_requests_context():
    response = await _run_message(
        "Que tan listo esta el Owner Pack para responderle al dueno?"
    )

    assert "Readiness del Owner Pack" in response.assistant_message
    assert "schema" in response.assistant_message.lower()
    assert "De que torneo quieres revisar el Owner Pack" in response.assistant_message
    trace = response.tool_trace[0]["owner_pack_readiness"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0


@pytest.mark.asyncio
async def test_owner_pack_readiness_uses_local_live_evidence_when_available(monkeypatch):
    import samchat.assistant.conversation_service as conversation_service
    from samchat.assistant.owner_pack_live_evidence import (
        OwnerPackLiveEvidenceResolution,
    )
    from samchat.assistant.owner_pack_live_snapshot import (
        OWNER_PACK_LIVE_SNAPSHOT_ONLY,
        OWNER_PACK_LIVE_SUPPORTED,
        OwnerPackLiveFieldSnapshot,
        OwnerPackLiveSnapshotReport,
        OwnerPackLiveSurfaceSnapshot,
    )

    async def fake_live_evidence(session, *, scope, tournament_hint, entity_name):
        assert tournament_hint == "copa-telmex"
        surface = OwnerPackLiveSurfaceSnapshot(
            surface_id="national_phase_folder",
            label="Carpeta fase nacional",
            target={"tournament_name": "Copa Telmex Telcel de Futbol"},
            workspace_root="samchat_local_tournament_db",
            workspace_files_checked=["samchat_local_tournament_db"],
            workspace_files_found=["samchat_local_tournament_db"],
            fields=[
                OwnerPackLiveFieldSnapshot(
                    field="tournament_category",
                    label="Torneo / categor?a",
                    section_id="operations",
                    evidence_type="tournament",
                    status=OWNER_PACK_LIVE_SUPPORTED,
                    value={"tournament_name": "Copa Telmex Telcel de Futbol"},
                    source_paths=["db.tournaments.name"],
                    source_files=["samchat_local_tournament_db"],
                )
            ],
            supported_field_count=1,
            missing_field_count=0,
            audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
        )
        report = OwnerPackLiveSnapshotReport(
            snapshot_id="owner_pack_live_evidence_v2_national_phase_folder",
            headline="Evidencia viva local",
            summary="ok",
            surfaces=[surface],
            supported_field_count=1,
            missing_field_count=0,
        )
        return OwnerPackLiveEvidenceResolution(status="resolved", reports=[report])

    monkeypatch.setattr(
        conversation_service,
        "resolve_owner_pack_live_evidence",
        fake_live_evidence,
    )

    response = await _run_message(
        "Que falta para contestarle al Director General sobre Copa Telmex?"
    )

    assert "db.tournaments.name" in str(response.tool_trace[0]["result"])
    trace = response.tool_trace[0]["owner_pack_readiness"]
    assert trace["live_evidence_status"] == "resolved"
    assert trace["live_evidence_reports"] == 1
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] == 0


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
async def test_specialist_preview_surface_routes_natural_business_intent_without_provider():
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("natural specialist preview should bypass provider")

    response = await _run_message(
        "Prepara la CxC de la factura 669DBF39 contra DCC Nacional",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Preview especialista listo" in response.assistant_message
    assert response.preview_render["task_id"] == "SAMCHAT-CXC-COLLECTION-001"
    assert response.preview_render["primary_action_enabled"] is False
    trace = response.tool_trace[0]["specialist_preview_surface"]
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False


@pytest.mark.asyncio
async def test_specialist_preview_surface_attaches_read_only_live_context(monkeypatch):
    import samchat.assistant.conversation_service as conversation_service

    async def fake_live_context(session, understood_context):
        assert understood_context["uuid_or_prefixes"] == ["669DBF39"]
        return {
            "source": "samchat_db",
            "live_lookup_performed": True,
            "authority": "read_only_context",
            "matched": True,
            "documents": [],
            "expenses": [],
            "cfdis": [
                {
                    "cfdi_uuid": "669DBF39-F23C-4AD5-B858-F1F5A9AC8626",
                    "emisor_nombre": "BIMBO",
                    "total": 1972903,
                    "tipo_de_comprobante": "I",
                }
            ],
            "unresolved": {},
            "status": "matched",
        }

    monkeypatch.setattr(
        conversation_service,
        "resolve_specialist_preview_live_context",
        fake_live_context,
    )

    response = await _run_message(
        "Prepara la CxC de la factura 669DBF39 contra DCC Nacional"
    )

    assert "Contexto encontrado" in response.assistant_message
    assert "Diagnostico operativo" in response.assistant_message
    assert "Calidad de evidencia" in response.assistant_message
    assert "CFDI 669DBF39-F23C-4AD5-B858-F1F5A9AC8626" in response.assistant_message
    assert "ready_for_read_only_preview" in response.assistant_message
    assert "Frontera de autoridad" in response.assistant_message
    trace = response.tool_trace[0]["specialist_preview_surface"]
    assert trace["live_context"]["authority"] == "read_only_context"
    assert trace["live_context"]["matched"] is True
    assert trace["diagnostics"]["authority"] == "read_only_diagnostic"
    assert trace["diagnostics"]["readiness"] == "ready_for_read_only_preview"
    assert [card["card_id"] for card in trace["workspace_cards"]] == [
        "understood_context",
        "live_context",
        "case_continuity",
        "case_memory",
        "operational_diagnostics",
        "evidence_quality",
        "resume_guidance",
        "business_preview",
        "authority_boundary",
    ]
    assert trace["workspace_cards"][2]["authority"] == "read_only_continuity"
    assert trace["workspace_cards"][3]["authority"] == "read_only_memory"
    assert trace["resume_guidance"]["authority"] == "read_only_guidance"
    assert trace["operator_workspace_snapshot"]["authority"] == "read_only_workspace_snapshot"
    assert trace["operator_workspace_snapshot"]["operational_writes"] is False
    assert trace["workspace_cards"][-1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_operator_workspace_resume_bypasses_provider_and_does_not_create_new_preview(monkeypatch):
    import samchat.assistant.conversation_service as conversation_service
    from samchat.assistant.operator_workspace_snapshot import build_operator_workspace_snapshot

    snapshot = build_operator_workspace_snapshot(
        conversation_id="conv-request",
        task_id="SAMCHAT-CXC-COLLECTION-001",
        preview_render={
            "preview_id": "preview-1",
            "task_id": "SAMCHAT-CXC-COLLECTION-001",
            "preview_type": "accounts_receivable_collection",
            "primary_action_enabled": False,
            "execution_status": "not_executed",
        },
        business_preview={"task_id": "SAMCHAT-CXC-COLLECTION-001"},
        understood_context={"authority": "context_hint_only"},
        live_context={"authority": "read_only_context", "matched": True},
        continuity_context={"authority": "read_only_continuity", "matched": False},
        memory_context={"authority": "read_only_memory", "matched": False},
        diagnostics={"authority": "read_only_diagnostic", "readiness": "ready_for_read_only_preview"},
        evidence_quality_gate={
            "authority": "read_only_evidence_gate",
            "quality_status": "supported",
            "safe_to_execute": False,
        },
        resume_guidance={
            "authority": "read_only_guidance",
            "status": "ready_for_isolated_preview",
            "recommendation": "Continuar con preview/diff read-only.",
        },
        workspace_cards=[{"card_id": "understood_context"}],
        step_trace=[{"step_id": "understand_request"}],
        source_panel=[{"source_id": "user_message"}],
    )

    async def fake_loader(*, session, conversation_id, limit=30):
        assert conversation_id == "conv-request"
        return {
            "status": "matched",
            "matched": True,
            "message_id": "msg-1",
            "snapshot": snapshot,
        }

    monkeypatch.setattr(
        conversation_service,
        "load_latest_operator_workspace_snapshot",
        fake_loader,
    )
    calls = []

    async def provider(**kwargs):  # pragma: no cover
        calls.append(kwargs)
        raise AssertionError("workspace resume should bypass provider")

    response = await _run_message(
        "Retoma el workspace anterior",
        assistant_turn=provider,
    )

    assert calls == []
    assert "Workspace retomado" in response.assistant_message
    assert "SAMCHAT-CXC-COLLECTION-001" in response.assistant_message
    assert "no ejecuta acciones" in response.assistant_message
    trace = response.tool_trace[0]["operator_workspace_resume"]
    assert trace["status"] == "ready_to_resume"
    assert trace["provider_called"] is False
    assert trace["writes_attempted"] is False
    assert trace["safe_to_execute"] is False


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
