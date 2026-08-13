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
