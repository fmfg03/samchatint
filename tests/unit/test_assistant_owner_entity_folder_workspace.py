from __future__ import annotations

import json
from pathlib import Path

import pytest

from samchat.assistant.owner_entity_folder_workspace import (
    OWNER_ENTITY_FOLDER_WORKSPACE_ONLY,
    WORKSPACE_NEEDS_TARGET,
    WORKSPACE_PARTIAL,
    build_owner_entity_folder_workspace_from_tournament_source,
    owner_entity_folder_workspace_contains_execution_claim,
)
from samchat.assistant.owner_needs_eval import parse_owner_needs_eval_set
from samchat.assistant.owner_pack_status import build_owner_pack_status_report


ROOT = Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "docs/assistant/rqf-assistant-009e-evaluation-set.md"


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _status_report():
    prompts = parse_owner_needs_eval_set(EVAL_SET.read_text(encoding="utf-8"))
    return build_owner_pack_status_report(prompts)


def _source():
    return _Obj(
        schema_version="local.v1",
        source_hash="sha256:workspace",
        domain_write_performed=False,
        project=_Obj(
            id="tor-1",
            name="Copa Local",
            categorias=["Sub 15"],
            etapas=["Inscripcion"],
        ),
        operations_link=_Obj(operations_tournament_slug="copa-local"),
        observed_operations=_Obj(
            scope_slug="copa-local",
            teams_count=3,
            players_count=42,
            categories=["Sub 15"],
            branches=["Varonil"],
            states=["CDMX"],
            municipalities=["Benito Juarez"],
        ),
        unavailable_components=["rich_tournament_dates", "communications"],
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_owner_entity_folder_workspace_composes_live_evidence_without_writes(tmp_path: Path) -> None:
    entity_dir = tmp_path / "copa-local" / "entities" / "cdmx"
    _write_json(
        entity_dir / "operations.json",
        {
            "entity_name": "CDMX",
            "tournament_slug": "copa-local",
            "expected_teams_by_category_gender": [{"categoria": "Sub 15", "equipos": 4}],
            "real_teams_by_category_gender": [{"categoria": "Sub 15", "equipos": 3}],
            "players_by_category_age_gender": [{"edad": 15, "jugadores": 42}],
        },
    )
    _write_json(
        entity_dir / "finance.json",
        {
            "entity_name": "CDMX",
            "operator_transfers": [{"fecha": "2026-07-01", "monto": 10000}],
        },
    )

    workspace = build_owner_entity_folder_workspace_from_tournament_source(
        _source(),
        status_report=_status_report(),
        entity_name="CDMX",
        root_dir=tmp_path,
    )

    assert workspace.audit_language == OWNER_ENTITY_FOLDER_WORKSPACE_ONLY
    assert workspace.execution_status == "not_executed"
    assert workspace.writes_attempted == 0
    assert workspace.side_effects_detected == 0
    assert workspace.safety_summary["read_only"] is True
    assert workspace.safety_summary["writes_enabled"] is False
    assert workspace.safety_summary["approval_required_for_durable_output"] is True
    assert workspace.status == WORKSPACE_PARTIAL
    assert workspace.workspace_cards
    assert workspace.folder_sections
    assert workspace.evidence
    assert workspace.missing_fields
    assert workspace.preview["blocked_actions"]
    assert workspace.preview["execution_status"] == "not_executed"
    assert any("carpeta" in item.lower() for item in workspace.non_claims)
    assert owner_entity_folder_workspace_contains_execution_claim(workspace) is False


def test_owner_entity_folder_workspace_requires_entity_target(tmp_path: Path) -> None:
    workspace = build_owner_entity_folder_workspace_from_tournament_source(
        _source(),
        status_report=_status_report(),
        entity_name=None,
        root_dir=tmp_path,
    )

    assert workspace.status == WORKSPACE_NEEDS_TARGET
    assert workspace.evidence == []
    assert workspace.missing_fields
    assert any("entidad" in question.lower() for question in workspace.next_questions)
    assert workspace.safety_summary["complete_claim_allowed"] is False
    assert owner_entity_folder_workspace_contains_execution_claim(workspace) is False


async def _fake_inspect_tournament_source(session, *, tournament_id=None, tournament_name=None):
    assert tournament_name == "Copa Local"
    assert tournament_id is None
    return _source()


@pytest.mark.asyncio
async def test_router_runs_owner_entity_folder_workspace_read_only(monkeypatch, tmp_path: Path) -> None:
    import samchat.assistant.router as assistant_router

    monkeypatch.setattr(assistant_router, "inspect_tournament_source", _fake_inspect_tournament_source)
    monkeypatch.setenv("TOURNAMENT_AI_WORKSPACE_ROOT", str(tmp_path))

    payload = await assistant_router._run_read_tool(
        "assistant_owner_entity_folder_workspace",
        {"tournament_name": "Copa Local", "entity_name": "CDMX"},
        gastos_session=object(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert payload["workspace_id"].startswith("oefw_")
    assert payload["audit_language"] == OWNER_ENTITY_FOLDER_WORKSPACE_ONLY
    assert payload["execution_status"] == "not_executed"
    assert payload["writes_attempted"] == 0
    assert payload["side_effects_detected"] == 0
    assert payload["safety_summary"]["writes_enabled"] is False
    assert payload["preview"]["approval_required_for_durable_output"] is True
