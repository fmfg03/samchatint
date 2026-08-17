from __future__ import annotations

import pytest

from samchat.assistant.specialist_preview_renderer import (
    SECTION_AUTHORITY,
    SECTION_PROPOSED_CHANGES,
    SECTION_SUMMARY,
)
from samchat.assistant.specialist_preview_surface import (
    detect_specialist_preview_task_id,
    extract_specialist_preview_understood_context,
    render_specialist_preview_surface,
)


def test_detect_specialist_preview_requires_explicit_preview_intent() -> None:
    assert (
        detect_specialist_preview_task_id(
            "Muestra preview especialista SAMCHAT-CXC-COLLECTION-001"
        )
        == "SAMCHAT-CXC-COLLECTION-001"
    )
    assert detect_specialist_preview_task_id("SAMCHAT-CXC-COLLECTION-001") is None
    assert detect_specialist_preview_task_id("preview SAMCHAT-UNKNOWN-001") is None


def test_detect_specialist_preview_routes_natural_business_intents() -> None:
    assert (
        detect_specialist_preview_task_id(
            "Prepara la CxC de la factura 669DBF39 contra DCC Nacional"
        )
        == "SAMCHAT-CXC-COLLECTION-001"
    )
    assert (
        detect_specialist_preview_task_id(
            "Revisa la comprobacion AMEX referencia 28 de Odilon"
        )
        == "SAMCHAT-FIN-AMEX-001"
    )
    assert (
        detect_specialist_preview_task_id(
            "Armame un borrador para el impuesto sobre hospedaje del hotel de Leon"
        )
        == "SAMCHAT-SUPPLIER-HOTEL-001"
    )
    assert (
        detect_specialist_preview_task_id(
            "Genera propuesta para crear torneo 2027 con categoria Sub-17"
        )
        == "SAMCHAT-TOURNAMENT-2027-001"
    )


def test_detect_specialist_preview_does_not_trigger_on_domain_chatter() -> None:
    assert detect_specialist_preview_task_id("Tenemos facturas de Bimbo en DCC") is None
    assert detect_specialist_preview_task_id("AMEX de Odilon") is None
    assert detect_specialist_preview_task_id("Que es CxC?") is None


def test_detect_specialist_preview_fails_closed_on_ambiguous_business_intent() -> None:
    assert (
        detect_specialist_preview_task_id(
            "Prepara preview de AMEX referencia 28 y tambien CxC de DCC con factura"
        )
        is None
    )


def test_extract_specialist_preview_understood_context_from_business_prompt() -> None:
    context = extract_specialist_preview_understood_context(
        "Prepara CxC factura 669DBF39 contra DCC Nacional REF 28 con cuenta 1150-001-001"
    )

    assert context["source"] == "user_message"
    assert context["live_lookup_performed"] is False
    assert context["authority"] == "context_hint_only"
    assert context["operations_refs"] == ["28"]
    assert context["uuid_or_prefixes"] == ["669DBF39"]
    assert context["account_codes"] == ["1150-001-001"]
    assert "cxc" in context["domains"]
    assert "cfdi" in context["domains"]
    assert "DCC Nacional" in context["entities"]


def test_specialist_preview_surface_includes_understood_context_in_message_and_trace() -> (
    None
):
    surface = render_specialist_preview_surface(
        "SAMCHAT-FIN-AMEX-001",
        raw_message="Revisa AMEX referencia 28 de Odilon FGV 45007",
    )
    trace = surface.tool_trace()

    assert "Contexto entendido" in surface.assistant_message
    assert "Referencias operaciones: 28" in surface.assistant_message
    assert surface.understood_context["operations_refs"] == ["28"]
    assert "amex" in surface.understood_context["domains"]
    assert "Odilon" in surface.understood_context["entities"]
    assert (
        trace["specialist_preview_surface"]["understood_context"]
        == surface.understood_context
    )
    assert trace["result"]["understood_context"] == surface.understood_context


def test_specialist_preview_surface_is_structured_and_inert() -> None:
    surface = render_specialist_preview_surface("SAMCHAT-CXC-COLLECTION-001")
    payload = surface.preview_render.to_dict()

    assert surface.provider_called is False
    assert surface.writes_attempted is False
    assert payload["primary_action_enabled"] is False
    assert payload["execution_status"] == "not_executed"
    assert [section["section_id"] for section in payload["sections"]][:3] == [
        SECTION_SUMMARY,
        SECTION_PROPOSED_CHANGES,
        "evidence",
    ]
    assert payload["sections"][-1]["section_id"] == SECTION_AUTHORITY
    assert payload["sections"][-1]["status"] == "blocked"
    assert "Preview especialista listo" in surface.assistant_message
    assert "Frontera de autoridad" in surface.assistant_message


@pytest.mark.parametrize(
    "task_id", ("SAMCHAT-CXC-COLLECTION-001", "SAMCHAT-FIN-AMEX-001")
)
def test_specialist_preview_surface_trace_exposes_renderer_contract(
    task_id: str,
) -> None:
    surface = render_specialist_preview_surface(task_id)
    trace = surface.tool_trace()

    assert trace["specialist_preview_surface"]["stage"] == (
        "deterministic_read_only_preview_surface"
    )
    assert trace["specialist_preview_surface"]["primary_action_enabled"] is False
    assert trace["tool"] == "assistant.specialist_preview.render"
    assert trace["result"]["preview_render"] == surface.preview_render.to_dict()
    assert trace["result"]["business_preview"]["task_id"] == task_id


def test_specialist_preview_workspace_cards_contract() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_workspace_cards,
    )

    cards = build_specialist_preview_workspace_cards(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"authority": "context_hint_only", "domains": ["cxc"]},
        live_context={
            "authority": "read_only_context",
            "status": "matched",
            "matched": True,
            "cfdis": [{"cfdi_uuid": "669DBF39"}],
        },
        diagnostics={
            "authority": "read_only_diagnostic",
            "readiness": "ready_for_read_only_preview",
        },
        preview_render={
            "task_id": "SAMCHAT-CXC-COLLECTION-001",
            "execution_status": "not_executed",
        },
    )

    assert [card["card_id"] for card in cards] == [
        "understood_context",
        "live_context",
        "operational_diagnostics",
        "business_preview",
        "authority_boundary",
    ]
    assert cards[0]["authority"] == "context_hint_only"
    assert cards[1]["kind"] == "evidence"
    assert cards[2]["status"] == "ready_for_read_only_preview"
    assert cards[-1]["status"] == "blocked"
    assert cards[-1]["data"]["primary_action_enabled"] is False
    assert cards[-1]["data"]["writes_attempted"] is False


def test_specialist_resume_guidance_paths_are_read_only() -> None:
    from samchat.assistant.specialist_resume_guidance import (
        build_specialist_resume_guidance,
        render_specialist_resume_guidance_markdown,
    )

    missing = build_specialist_resume_guidance(
        diagnostics={"readiness": "needs_more_context", "missing": ["Falta CFDI"]},
        continuity_context={"matched": True, "active_case": {"case_id": "analyst_case_" + "a" * 32}},
        memory_context={"matched": True},
    )
    active = build_specialist_resume_guidance(
        diagnostics={"readiness": "ready_for_read_only_preview", "missing": []},
        continuity_context={"matched": True, "active_case": {"case_id": "analyst_case_" + "b" * 32}},
        memory_context={"matched": False},
    )
    precedent = build_specialist_resume_guidance(
        diagnostics={"readiness": "ready_for_read_only_preview", "missing": []},
        continuity_context={"matched": False},
        memory_context={"matched": True},
    )

    assert missing["status"] == "needs_more_context"
    assert active["status"] == "ready_to_continue_active_case"
    assert precedent["status"] == "precedent_only"
    assert active["primary_action_enabled"] is False
    assert active["writes_attempted"] is False
    assert "Guia de reanudacion" in render_specialist_resume_guidance_markdown(active)


def test_specialist_continuity_context_detects_active_case() -> None:
    from types import SimpleNamespace

    from samchat.assistant.specialist_continuity_context import (
        render_specialist_continuity_context_markdown,
        resolve_specialist_preview_continuity_context,
    )

    context = resolve_specialist_preview_continuity_context(
        SimpleNamespace(
            tournament_key="copa_telmex",
            metadata_={
                "module_key": "tournaments",
                "module_label": "Torneos",
                "active_tournament_goal_case": {
                    "case_id": "analyst_case_" + "a" * 32,
                    "case_version": 3,
                    "status": "draft",
                },
            },
        )
    )
    message = render_specialist_continuity_context_markdown(context)

    assert context["authority"] == "read_only_continuity"
    assert context["matched"] is True
    assert context["active_case"]["case_version"] == 3
    assert "Caso activo" in message
    assert "continuidad read-only" in message


def test_specialist_continuity_context_fails_closed_on_invalid_pointer() -> None:
    from types import SimpleNamespace

    from samchat.assistant.specialist_continuity_context import (
        resolve_specialist_preview_continuity_context,
    )

    context = resolve_specialist_preview_continuity_context(
        SimpleNamespace(
            tournament_key=None,
            metadata_={"active_tournament_goal_case": {"case_id": "bad"}},
        )
    )

    assert context["matched"] is False
    assert context["status"] == "invalid_active_case_pointer"
    assert context["active_case"] is None


def test_specialist_preview_workspace_cards_include_continuity_when_provided() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_workspace_cards,
    )

    cards = build_specialist_preview_workspace_cards(
        task_id="SAMCHAT-TOURNAMENT-2027-001",
        understood_context={"authority": "context_hint_only", "domains": ["torneo"]},
        live_context={"authority": "read_only_context", "status": "no_matches", "matched": False},
        continuity_context={
            "authority": "read_only_continuity",
            "status": "active_case_found",
            "matched": True,
            "active_case": {"case_id": "analyst_case_" + "a" * 32},
        },
        diagnostics={"authority": "read_only_diagnostic", "readiness": "needs_more_context"},
        preview_render={"task_id": "SAMCHAT-TOURNAMENT-2027-001", "execution_status": "not_executed"},
    )

    assert [card["card_id"] for card in cards] == [
        "understood_context",
        "live_context",
        "case_continuity",
        "operational_diagnostics",
        "business_preview",
        "authority_boundary",
    ]
    assert cards[2]["authority"] == "read_only_continuity"
    assert cards[-1]["status"] == "blocked"


def test_specialist_memory_context_markdown_is_read_only() -> None:
    from samchat.assistant.specialist_memory_context import (
        render_specialist_memory_context_markdown,
    )

    message = render_specialist_memory_context_markdown(
        {
            "source": "case_memory_artifacts",
            "lookup_performed": True,
            "authority": "read_only_memory",
            "matched": True,
            "status": "matched",
            "snippets": [
                {
                    "label": "memory:case_summary:abc",
                    "score": 1.12,
                    "text": "Memoria de caso resumida :: se resolvio como no deducible.",
                }
            ],
        }
    )

    assert "Memoria de casos" in message
    assert "memory:case_summary:abc" in message
    assert "precedente read-only" in message


def test_specialist_preview_workspace_cards_include_case_memory_when_provided() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_workspace_cards,
    )

    cards = build_specialist_preview_workspace_cards(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"authority": "context_hint_only", "domains": ["cxc"]},
        live_context={"authority": "read_only_context", "status": "matched", "matched": True},
        memory_context={
            "authority": "read_only_memory",
            "status": "matched",
            "matched": True,
            "snippets": [{"label": "memory:case_summary:abc"}],
        },
        diagnostics={"authority": "read_only_diagnostic", "readiness": "ready_for_read_only_preview"},
        preview_render={"task_id": "SAMCHAT-CXC-COLLECTION-001", "execution_status": "not_executed"},
    )

    assert [card["card_id"] for card in cards] == [
        "understood_context",
        "live_context",
        "case_memory",
        "operational_diagnostics",
        "business_preview",
        "authority_boundary",
    ]
    assert cards[2]["authority"] == "read_only_memory"
    assert cards[2]["data"]["snippet_count"] == 1
    assert cards[-1]["data"]["primary_action_enabled"] is False


def test_specialist_preview_workspace_cards_include_resume_guidance_when_provided() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_workspace_cards,
    )

    cards = build_specialist_preview_workspace_cards(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"authority": "context_hint_only", "domains": ["cxc"]},
        live_context={"authority": "read_only_context", "status": "matched", "matched": True},
        diagnostics={"authority": "read_only_diagnostic", "readiness": "ready_for_read_only_preview"},
        resume_guidance={
            "authority": "read_only_guidance",
            "status": "ready_for_isolated_preview",
            "recommendation": "Continuar read-only.",
            "primary_action_enabled": False,
        },
        preview_render={"task_id": "SAMCHAT-CXC-COLLECTION-001", "execution_status": "not_executed"},
    )

    assert "resume_guidance" in [card["card_id"] for card in cards]
    guidance = next(card for card in cards if card["card_id"] == "resume_guidance")
    assert guidance["authority"] == "read_only_guidance"
    assert guidance["data"]["primary_action_enabled"] is False


def test_specialist_preview_workspace_cards_include_evidence_quality_gate_when_provided() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_workspace_cards,
    )

    cards = build_specialist_preview_workspace_cards(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"authority": "context_hint_only", "domains": ["cxc"]},
        live_context={"authority": "read_only_context", "status": "matched", "matched": True},
        diagnostics={"authority": "read_only_diagnostic", "readiness": "ready_for_read_only_preview"},
        evidence_quality_gate={
            "authority": "read_only_evidence_gate",
            "quality_status": "partial",
            "safe_to_execute": False,
            "primary_action_enabled": False,
        },
        preview_render={"task_id": "SAMCHAT-CXC-COLLECTION-001", "execution_status": "not_executed"},
    )

    assert "evidence_quality" in [card["card_id"] for card in cards]
    gate = next(card for card in cards if card["card_id"] == "evidence_quality")
    assert gate["authority"] == "read_only_evidence_gate"
    assert gate["status"] == "partial"
    assert gate["data"]["primary_action_enabled"] is False


def test_specialist_preview_diagnostics_marks_cxc_ready_with_cfdi() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_diagnostics,
        render_specialist_preview_diagnostics_markdown,
    )

    diagnostics = build_specialist_preview_diagnostics(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"domains": ["cxc", "cfdi"]},
        live_context={
            "live_lookup_performed": True,
            "matched": True,
            "documents": [],
            "expenses": [],
            "cfdis": [{"cfdi_uuid": "669DBF39", "total": 100}],
            "unresolved": {},
        },
    )
    message = render_specialist_preview_diagnostics_markdown(diagnostics)

    assert diagnostics["authority"] == "read_only_diagnostic"
    assert diagnostics["readiness"] == "ready_for_read_only_preview"
    assert diagnostics["writes_attempted"] is False
    assert "CFDI encontrados: 1" in message
    assert "Continuar con preview/diff read-only" in message


def test_specialist_preview_diagnostics_requires_cxc_cfdi() -> None:
    from samchat.assistant.specialist_live_context import (
        build_specialist_preview_diagnostics,
    )

    diagnostics = build_specialist_preview_diagnostics(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"domains": ["cxc"]},
        live_context={
            "live_lookup_performed": True,
            "matched": True,
            "documents": [{"numero_referencia": "S-2600071"}],
            "expenses": [],
            "cfdis": [],
            "unresolved": {},
        },
    )

    assert diagnostics["readiness"] == "needs_more_context"
    assert "Para CxC falta identificar al menos un CFDI emitido." in diagnostics["missing"]


def test_render_specialist_live_context_markdown_reports_read_only_matches() -> None:
    from samchat.assistant.specialist_live_context import (
        render_specialist_live_context_markdown,
    )

    message = render_specialist_live_context_markdown(
        {
            "source": "samchat_db",
            "live_lookup_performed": True,
            "authority": "read_only_context",
            "matched": True,
            "documents": [
                {
                    "numero_referencia": "S-2600071",
                    "tipo": "SOLICITUD",
                    "estado": "aprobado",
                    "referencia_operaciones": "9",
                    "monto_solicitado": 628,
                }
            ],
            "expenses": [
                {
                    "numero_referencia": "O-26000312",
                    "concepto": "CONSUMO",
                    "monto": 296,
                    "cfdi_uuid_manual": "75D37C50",
                }
            ],
            "cfdis": [
                {
                    "cfdi_uuid": "669DBF39-F23C-4AD5-B858-F1F5A9AC8626",
                    "emisor_nombre": "BIMBO",
                    "total": 1972903,
                    "tipo_de_comprobante": "I",
                }
            ],
            "unresolved": {"uuid_or_prefixes": ["NOPE0000"]},
            "status": "matched",
        }
    )

    assert "Contexto encontrado" in message
    assert "Documento S-2600071 | SOLICITUD | estado aprobado | REF 9" in message
    assert "Gasto O-26000312 | CONSUMO | $296.00 | CFDI 75D37C50" in message
    assert "CFDI 669DBF39-F23C-4AD5-B858-F1F5A9AC8626 | BIMBO" in message
    assert "Sin resolver: uuid_or_prefixes: NOPE0000" in message
    assert "consulta read-only" in message


@pytest.mark.asyncio
async def test_specialist_preview_surface_persists_render_payload() -> None:
    from types import SimpleNamespace

    import pydantic

    if not hasattr(pydantic, "field_validator"):
        pytest.skip("conversation_service import requires pydantic v2")

    from samchat.assistant.conversation_service import (
        _build_specialist_preview_surface_response,
    )

    class FakeSession:
        def __init__(self) -> None:
            self.added = []
            self.committed = False

        def add(self, item) -> None:
            self.added.append(item)

        async def commit(self) -> None:
            self.committed = True

    session = FakeSession()
    conversation = SimpleNamespace(id="conv-1", updated_at=None)
    response = await _build_specialist_preview_surface_response(
        raw_message="Muestra preview especialista SAMCHAT-CXC-COLLECTION-001",
        conversation=conversation,
        session=session,
    )

    assert response is not None
    assert response.preview_render["task_id"] == "SAMCHAT-CXC-COLLECTION-001"
    assert session.committed is True
    assistant_messages = [
        m for m in session.added if getattr(m, "role", None) == "assistant"
    ]
    assert len(assistant_messages) == 1
    payload = assistant_messages[0].tool_payload
    assert payload["preview_render"] == response.preview_render
    assert payload["business_preview"]["task_id"] == "SAMCHAT-CXC-COLLECTION-001"
    assert "Calidad de evidencia" in response.assistant_message
    assert payload["memory_context"]["authority"] == "read_only_memory"
    assert payload["evidence_quality_gate"]["authority"] == "read_only_evidence_gate"
    assert payload["evidence_quality_gate"]["safe_to_execute"] is False
    assert payload["operator_workspace_snapshot"]["authority"] == "read_only_workspace_snapshot"
    assert payload["operator_workspace_snapshot"]["persistence_medium"] == "assistant_message_tool_payload"
    assert payload["operator_workspace_snapshot"]["primary_action_enabled"] is False
    assert payload["operator_workspace_snapshot"]["components"]["workspace_cards"] == payload["workspace_cards"]
    assert payload["resume_guidance"]["authority"] == "read_only_guidance"


def test_specialist_preview_workspace_trace_and_sources_contract() -> None:
    from samchat.assistant.assistant_workspace_trace import (
        build_specialist_workspace_source_panel,
        build_specialist_workspace_step_trace,
    )

    understood_context = {
        "authority": "context_hint_only",
        "domains": ["cxc", "cfdi"],
        "operations_refs": ["28"],
        "uuid_or_prefixes": ["669DBF39"],
    }
    live_context = {
        "authority": "read_only_context",
        "status": "matched",
        "live_lookup_performed": True,
        "matched": True,
        "documents": [{"numero_referencia": "S-2600071"}],
        "expenses": [],
        "cfdis": [{"cfdi_uuid": "669DBF39"}],
        "unresolved": {},
    }
    diagnostics = {
        "authority": "read_only_diagnostic",
        "readiness": "ready_for_read_only_preview",
        "findings": ["CFDI encontrados: 1."],
        "missing": [],
        "risks": [],
        "next_steps": ["Continuar con preview/diff read-only."],
    }
    preview_render = {
        "preview_id": "preview-1",
        "task_id": "SAMCHAT-CXC-COLLECTION-001",
        "preview_type": "specialist_business_diff",
        "sections": [{"section_id": "summary"}, {"section_id": "authority"}],
        "primary_action_enabled": False,
        "execution_status": "not_executed",
    }

    steps = build_specialist_workspace_step_trace(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context=understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=preview_render,
    )
    sources = build_specialist_workspace_source_panel(
        understood_context=understood_context,
        live_context=live_context,
        diagnostics=diagnostics,
        preview_render=preview_render,
    )

    assert [step["step_id"] for step in steps] == [
        "understand_request",
        "read_live_context",
        "diagnose_readiness",
        "prepare_preview",
        "hold_authority_boundary",
    ]
    assert steps[1]["data"]["documents"] == 1
    assert steps[1]["data"]["cfdis"] == 1
    assert steps[-1]["status"] == "blocked"
    assert steps[-1]["data"]["execution_allowed"] is False
    assert [source["source_id"] for source in sources] == [
        "user_message",
        "samchat_db_readonly",
        "deterministic_diagnostics",
        "specialist_preview_contract",
    ]
    assert sources[1]["status"] == "matched"
    assert sources[-1]["data"]["primary_action_enabled"] is False


def test_specialist_preview_workspace_trace_and_sources_include_case_memory() -> None:
    from samchat.assistant.assistant_workspace_trace import (
        build_specialist_workspace_source_panel,
        build_specialist_workspace_step_trace,
    )

    memory_context = {
        "authority": "read_only_memory",
        "lookup_performed": True,
        "matched": True,
        "status": "matched",
        "snippets": [{"label": "memory:case_summary:abc"}],
    }
    guidance = {
        "authority": "read_only_guidance",
        "status": "precedent_only",
        "recommendation": "Usar precedente.",
        "primary_action_enabled": False,
    }
    steps = build_specialist_workspace_step_trace(
        task_id="SAMCHAT-CXC-COLLECTION-001",
        understood_context={"authority": "context_hint_only"},
        live_context={"authority": "read_only_context", "live_lookup_performed": True},
        memory_context=memory_context,
        resume_guidance=guidance,
        diagnostics={"authority": "read_only_diagnostic", "readiness": "ready_for_read_only_preview"},
        preview_render={"execution_status": "not_executed", "sections": []},
    )
    sources = build_specialist_workspace_source_panel(
        understood_context={},
        live_context={"status": "matched"},
        memory_context=memory_context,
        resume_guidance=guidance,
        diagnostics={"readiness": "ready_for_read_only_preview"},
        preview_render={"execution_status": "not_executed"},
    )

    assert "recall_case_memory" in [step["step_id"] for step in steps]
    assert "case_memory_readonly" in [source["source_id"] for source in sources]
    assert "recommend_safe_next_step" in [step["step_id"] for step in steps]
    assert "deterministic_resume_guidance" in [source["source_id"] for source in sources]
    assert steps[-1]["data"]["execution_allowed"] is False
