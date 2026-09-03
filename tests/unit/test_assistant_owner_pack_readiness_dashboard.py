from __future__ import annotations

import json
from pathlib import Path

import pytest

from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set
from samchat.assistant.owner_pack_readiness import (
    OWNER_PACK_NEEDS_TARGET,
    OWNER_PACK_PARTIAL_LIVE_EVIDENCE,
    OWNER_PACK_SCHEMA_ONLY,
    build_owner_pack_readiness_from_scope,
)
from samchat.assistant.owner_pack_readiness_dashboard import (
    OWNER_PACK_DASHBOARD_SECTIONS,
    OWNER_PACK_READINESS_DASHBOARD_ONLY,
    build_owner_pack_readiness_dashboard,
    owner_pack_readiness_dashboard_contains_execution_claim,
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


def test_owner_pack_readiness_dashboard_without_tournament_is_navigable_and_safe() -> None:
    readiness = build_owner_pack_readiness_from_scope(
        status_report=_status_report(),
        scope="all",
    )

    dashboard = build_owner_pack_readiness_dashboard(readiness)
    payload = dashboard.to_dict()

    assert dashboard.dashboard_id == "owner_pack_readiness_dashboard_v1"
    assert dashboard.audit_language == OWNER_PACK_READINESS_DASHBOARD_ONLY
    assert dashboard.execution_status == "not_executed"
    assert dashboard.writes_attempted == 0
    assert dashboard.side_effects_detected == 0
    assert dashboard.safety_summary["read_only_dashboard"] is True
    assert dashboard.safety_summary["writes_enabled"] is False
    assert dashboard.headline == "Estado ejecutivo del Owner Pack"
    assert dashboard.overall_status == OWNER_PACK_SCHEMA_ONLY
    assert dashboard.coverage_score == 0
    assert [card.section_id for card in dashboard.cards] == list(OWNER_PACK_DASHBOARD_SECTIONS)

    tournament_card = dashboard.cards[0]
    assert tournament_card.section_id == "tournament_context"
    assert tournament_card.status == OWNER_PACK_NEEDS_TARGET
    assert tournament_card.coverage_score == 0
    assert tournament_card.missing_items == ["Torneo objetivo"]
    assert "De que torneo" in tournament_card.next_questions[0]
    assert "scope: all" in tournament_card.available_sources
    assert payload["cards"][0]["label"] == "Torneo / contexto"
    assert payload["cards"][1]["label"] == "Carpeta por entidad"
    assert payload["cards"][2]["label"] == "Fase nacional"
    assert "Readiness" not in dashboard.headline
    assert "Entity folder" not in str(payload["cards"])
    assert "National phase" not in str(payload["cards"])
    assert owner_pack_readiness_dashboard_contains_execution_claim(dashboard) is False


def test_owner_pack_readiness_dashboard_surfaces_live_entity_coverage(tmp_path: Path) -> None:
    entity_dir = tmp_path / "copa-telmex" / "entities" / "jalisco"
    _write_json(
        entity_dir / "operations.json",
        {
            "entity_name": "Jalisco",
            "ps_entity_owner_name": "Alicia",
            "entity_contact": {"nombre": "Operador Jalisco", "telefono": "555"},
            "expected_teams_by_category_gender": [{"categoria": "Juvenil", "equipos": 12}],
            "real_teams_by_category_gender": [{"categoria": "Juvenil", "equipos": 10}],
        },
    )
    _write_json(
        entity_dir / "finance.json",
        {"operator_transfers": [{"fecha": "2026-07-01", "monto": 10000}]},
    )

    readiness = build_owner_pack_readiness_from_scope(
        status_report=_status_report(),
        scope="entity_folder",
        tournament_slug="Copa Telmex",
        entity_name="Jalisco",
        root_dir=tmp_path,
    )
    dashboard = build_owner_pack_readiness_dashboard(readiness)
    cards = {card.section_id: card for card in dashboard.cards}

    assert dashboard.overall_status == OWNER_PACK_PARTIAL_LIVE_EVIDENCE
    assert dashboard.coverage_score > 0
    assert cards["tournament_context"].coverage_score == 100
    assert cards["entity_folder"].coverage_score > 0
    assert cards["entity_folder"].available_sources
    assert cards["entity_folder"].missing_items
    assert cards["entity_folder"].next_action
    assert dashboard.source_reports
    assert dashboard.next_questions
    assert owner_pack_readiness_dashboard_contains_execution_claim(dashboard) is False


@pytest.mark.asyncio
async def test_owner_pack_readiness_dashboard_router_tool_is_read_only() -> None:
    from samchat.assistant.router import _run_read_tool

    payload = await _run_read_tool(
        "assistant_owner_pack_readiness_dashboard",
        {"scope": "entity_folder", "tournament_slug": "Copa Telmex"},
        gastos_session=None,
        tournament_key_default=None,
        current_role="admin",
    )

    assert payload["dashboard_id"] == "owner_pack_readiness_dashboard_v1"
    assert payload["audit_language"] == OWNER_PACK_READINESS_DASHBOARD_ONLY
    assert payload["execution_status"] == "not_executed"
    assert payload["writes_attempted"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["safety_summary"]["writes_enabled"] is False
    assert payload["readiness"]["readiness_id"] == "owner_pack_readiness_v1"
    assert payload["conversation_answer"]["status"] == OWNER_PACK_NEEDS_TARGET
    assert [card["section_id"] for card in payload["cards"]] == list(OWNER_PACK_DASHBOARD_SECTIONS)
    assert "Owner Pack" in payload["conversation_answer"]["rendered_text"]
    assert '{"name"' not in payload["conversation_answer"]["rendered_text"]
