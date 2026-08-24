from __future__ import annotations

import json

import pytest
from pathlib import Path

from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set
from samchat.assistant.owner_pack_readiness import (
    OWNER_PACK_NEEDS_TARGET,
    OWNER_PACK_PARTIAL_LIVE_EVIDENCE,
    OWNER_PACK_READINESS_ONLY,
    OWNER_PACK_READY_FOR_REVIEW,
    OWNER_PACK_SCHEMA_ONLY,
    build_owner_pack_readiness_from_scope,
    owner_pack_readiness_contains_execution_claim,
)
from samchat.assistant.owner_pack_readiness_answer import (
    OWNER_PACK_READINESS_ANSWER_ONLY,
    render_owner_pack_readiness_answer,
)
from samchat.assistant.owner_pack_status import build_owner_pack_status_report


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


def _status_report():
    prompts = parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))
    return build_owner_pack_status_report(prompts)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_owner_pack_readiness_without_tournament_is_schema_only_and_safe() -> None:
    report = build_owner_pack_readiness_from_scope(
        status_report=_status_report(),
        scope="all",
    )

    assert report.readiness_id == "owner_pack_readiness_v1"
    assert report.status == OWNER_PACK_SCHEMA_ONLY
    assert report.readiness_score == 0
    assert report.audit_language == OWNER_PACK_READINESS_ONLY
    assert report.execution_status == "not_executed"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.safety_summary["writes_enabled"] is False
    assert report.safety_summary["complete_claim_allowed"] is False
    assert "De que torneo" in report.next_questions[0]
    assert owner_pack_readiness_contains_execution_claim(report) is False

    answer = render_owner_pack_readiness_answer(report)
    assert answer.status == OWNER_PACK_SCHEMA_ONLY
    assert answer.audit_language == OWNER_PACK_READINESS_ANSWER_ONLY
    assert "Owner Pack" in answer.rendered_text
    assert "Frontera de autoridad" in answer.rendered_text
    assert '{"name"' not in answer.rendered_text


def test_owner_pack_readiness_entity_scope_requires_entity_name(tmp_path: Path) -> None:
    report = build_owner_pack_readiness_from_scope(
        status_report=_status_report(),
        scope="entity_folder",
        tournament_slug="Copa Telmex",
        root_dir=tmp_path,
    )

    assert report.status == OWNER_PACK_NEEDS_TARGET
    assert report.surfaces[0].status == OWNER_PACK_NEEDS_TARGET
    assert report.surfaces[0].live_lookup_performed is False
    assert any("entidad" in question.lower() for question in report.next_questions)
    assert report.safety_summary["writes_enabled"] is False


def test_owner_pack_readiness_uses_live_workspace_evidence(tmp_path: Path) -> None:
    entity_dir = tmp_path / "copa-telmex" / "entities" / "jalisco"
    _write_json(
        entity_dir / "operations.json",
        {
            "entity_name": "Jalisco",
            "tournament_slug": "Copa Telmex",
            "expected_teams_by_category_gender": [{"categoria": "Juvenil", "equipos": 12}],
            "real_teams_by_category_gender": [{"categoria": "Juvenil", "equipos": 10}],
        },
    )
    _write_json(
        entity_dir / "finance.json",
        {"operator_transfers": [{"fecha": "2026-07-01", "monto": 10000}]},
    )

    report = build_owner_pack_readiness_from_scope(
        status_report=_status_report(),
        scope="entity_folder",
        tournament_slug="Copa Telmex",
        entity_name="Jalisco",
        root_dir=tmp_path,
    )

    assert report.status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE
    assert report.readiness_score > 0
    assert report.surfaces[0].live_lookup_performed is True
    assert report.surfaces[0].supported_field_count >= 4
    assert report.surfaces[0].missing_field_count > 0
    assert report.evidence_found
    assert report.missing_evidence
    assert "owner_pack_live_snapshot_v1" in report.source_reports
    assert report.safety_summary["complete_claim_allowed"] is False


def test_owner_pack_readiness_can_be_ready_when_all_fields_supported(tmp_path: Path) -> None:
    national_dir = tmp_path / "dcc" / "national"
    _write_json(
        national_dir / "operations.json",
        {
            "tournament_category_dates_city": "DCC Nacional Sub-17, CDMX, nov 2026",
            "hotels_and_bed_nights": [{"hotel": "Uno", "camas_noche": 120}],
            "meals_breakdown": [{"tipo": "desayuno", "cantidad": 100}],
            "sports_facility": "Unidad Norte",
            "field_types_and_count": [{"tipo": "futbol", "cantidad": 4}],
            "medical_services_description": "Medico y ambulancia",
            "accidents_with_transfer": [{"traslado": True}],
        },
    )
    _write_json(
        national_dir / "finance.json",
        {
            "ps_travel_costs": [{"monto": 1000}],
            "hotel_payments_advance_settlement": [{"monto": 50000}],
            "supplier_payments": [{"proveedor": "Ambulancias", "monto": 12000}],
            "medical_service_costs": [{"monto": 8000}],
            "insurance_costs": [{"monto": 9000}],
        },
    )
    _write_json(
        national_dir / "marketing.json",
        {
            "photo_evidence": [{"archivo": "foto.jpg"}],
            "onsite_brand_activation_providers": [{"proveedor": "Sponsor"}],
            "sponsor_visitors": [{"nombre": "Visitante"}],
            "activities_and_results": [{"actividad": "Activacion", "resultado": "OK"}],
        },
    )

    report = build_owner_pack_readiness_from_scope(
        status_report=_status_report(),
        scope="national_phase_folder",
        tournament_slug="DCC",
        root_dir=tmp_path,
    )

    assert report.status == OWNER_PACK_READY_FOR_REVIEW
    assert report.readiness_score == 100
    assert report.missing_evidence == []
    assert report.safety_summary["complete_claim_allowed"] is True
    assert report.safety_summary["approval_required_for_durable_outputs"] is True
    assert owner_pack_readiness_contains_execution_claim(report) is False


async def test_owner_pack_readiness_router_tool_is_read_only() -> None:
    from samchat.assistant.router import _run_read_tool

    payload = await _run_read_tool(
        "assistant_owner_pack_readiness",
        {"scope": "entity_folder", "tournament_slug": "Copa Telmex"},
        gastos_session=None,
        tournament_key_default=None,
        current_role="admin",
    )

    assert payload["readiness_id"] == "owner_pack_readiness_v1"
    assert payload["status"] == OWNER_PACK_NEEDS_TARGET
    assert payload["execution_status"] == "not_executed"
    assert payload["safety_summary"]["writes_enabled"] is False
    assert payload["writes_attempted"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["conversation_answer"]["status"] == OWNER_PACK_NEEDS_TARGET
    assert "Owner Pack" in payload["conversation_answer"]["rendered_text"]
    assert "falta indicar" in payload["conversation_answer"]["rendered_text"].lower()
    assert '{"name"' not in payload["conversation_answer"]["rendered_text"]

@pytest.mark.asyncio
async def test_owner_pack_readiness_conversation_renders_pack_del_dueno_question(monkeypatch) -> None:
    from samchat.assistant import conversation_service as cs

    async def _noop_persist(**kwargs):
        return None

    monkeypatch.setattr(cs, "_persist_document_conversation_messages", _noop_persist)

    response = await cs._build_owner_pack_readiness_response(
        raw_message="tenemos listo el pack del dueno?",
        conversation=object(),
        session=object(),
        maybe_append_export_prompt=lambda message, trace: message,
    )

    assert response is not None
    assert "Readiness" in response.assistant_message
    assert "Owner Pack" in response.assistant_message
    assert "Estado" in response.assistant_message
    assert "assistant_owner_pack_readiness" not in response.assistant_message
    assert '{"name"' not in response.assistant_message
    assert response.tool_trace[0]["tool"] == "assistant_owner_pack_readiness"
    answer = response.tool_trace[0]["result"]["conversation_answer"]
    assert answer["status"] == OWNER_PACK_SCHEMA_ONLY
    assert "Frontera de autoridad" in answer["rendered_text"]
