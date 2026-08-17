from __future__ import annotations

from samchat.assistant.sports_operations_status import (
    SPORTS_OPERATIONS_STATUS_ONLY,
    build_sports_operations_status_from_snapshot,
)


def _snapshot():
    return {
        "ok": True,
        "tournaments": [
            {
                "id": "tor-1",
                "name": "Liga Telmex Telcel 2026",
                "slug": "liga-telmex-telcel-beisbol-2026",
            }
        ],
        "summary": {
            "teams_count": 1,
            "players_count": 2,
            "matches_count": 1,
            "teams_with_incomplete_documents": 1,
        },
        "operations": {
            "matches": [
                {
                    "id": "match-1",
                    "match_date": "2026-06-01T12:00:00Z",
                    "home_team_id": "team-1",
                    "away_team_id": "team-2",
                    "phase": "group",
                    "field_number": "1",
                    "status": "scheduled",
                    "cedula_status": "pending",
                }
            ],
            "standings": [{"team_id": "team-1", "points": 3}],
        },
        "communications": {"email_inbox_unread": 2, "whatsapp_unread": 1},
        "marketing": {"media": {"photos_count": 4}},
        "soul": {
            "tournament": {
                "id": "tor-1",
                "name": "Liga Telmex Telcel 2026",
                "slug": "liga-telmex-telcel-beisbol-2026",
            },
            "pending_actions": ["Atender equipos con documentos incompletos."],
            "risks": [
                {
                    "severity": "medium",
                    "code": "incomplete_documents",
                    "message": "Hay equipos con documentacion incompleta.",
                }
            ],
            "operations": {
                "entities": [
                    {
                        "entity_name": "Morelos",
                        "teams": [
                            {
                                "team_id": "team-1",
                                "team_name": "Halcones Morelos",
                                "category": "Sub 15",
                                "players_count": 2,
                                "documents_complete_players": 1,
                                "documents_verified_players": 0,
                                "primary_manager": {"email": "mario@example.com"},
                            }
                        ],
                    }
                ]
            },
            "compliance": {
                "players_count": 2,
                "completion_rate": 0.5,
                "verification_rate": 0.0,
                "incomplete_teams": [{"team_id": "team-1"}],
            },
        },
    }


def test_sports_operations_status_is_narrow_read_only_wrapper() -> None:
    report = build_sports_operations_status_from_snapshot(_snapshot())

    assert report.report_id == "sports_operations_status_v1"
    assert report.report_language == SPORTS_OPERATIONS_STATUS_ONLY
    assert report.operational_status == "attention_required"
    assert report.tournament["name"] == "Liga Telmex Telcel 2026"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.execution_status == "not_executed"
    assert report.safety_summary["read_only"] is True
    assert report.safety_summary["writes_enabled"] is False
    assert report.safety_summary["raw_snapshot_exposed"] is False


def test_sports_operations_status_keeps_only_assistant_safe_surfaces() -> None:
    report = build_sports_operations_status_from_snapshot(_snapshot())

    assert "mission_control" in report.source_modules
    assert "action_queue" in report.source_modules
    assert "incident_center" in report.source_modules
    assert "roster_intelligence" in report.source_modules
    assert "sponsor_media" in report.excluded_modules
    assert "public_microsite_generator" in report.excluded_modules

    payload = report.to_dict()
    assert "command_center" not in payload
    assert "public_layer" not in payload
    assert "ai_ops_assistant" not in payload


def test_sports_operations_status_summarizes_roster_incidents_and_actions() -> None:
    report = build_sports_operations_status_from_snapshot(_snapshot(), max_actions=2)

    assert report.action_counts["open"] >= 1
    assert report.action_counts["high"] >= 1
    assert len(report.top_actions) == 2
    assert report.roster_summary["players_count"] == 2
    assert report.roster_summary["completion_rate"] == 0.5
    assert report.roster_summary["incomplete_team_count"] == 1
    assert report.incident_summary["open_count"] >= 1
    assert report.matchday_summary["open_cedulas_count"] == 1
    assert report.communication_summary == {
        "whatsapp_unread": 1,
        "email_inbox_unread": 2,
    }


def test_sports_operations_status_focus_filters_top_actions_without_changing_source_contract() -> None:
    report = build_sports_operations_status_from_snapshot(_snapshot(), focus="communications")

    assert report.source_modules
    assert report.excluded_modules
    assert all("communications" in (item.get("source") or "") for item in report.top_actions)
    assert report.safety_summary["audit_decision"] == "wrap_before_wiring"


def _wizard_payload() -> dict:
    return {
        "draft_id": "dcc-2027",
        "tournament_name": "De la Calle a la Cancha",
        "edition_year": 2027,
        "categories": ["Sub 15"],
        "branches": ["Varonil", "Femenil"],
        "expected_entities": ["CDMX"],
        "expected_teams": 32,
        "required_documents": ["CURP", "Acta"],
        "eligibility_rules": ["Sin duplicidad CURP"],
        "phases": [
            {
                "phase_id": "state",
                "name": "Fase estatal",
                "start_date": "2027-03-01",
                "end_date": "2027-05-15",
                "activities": [
                    {
                        "activity_id": "uniforms",
                        "name": "Entrega de uniformes",
                        "owner": "Operaciones",
                        "due_date": "2027-04-01",
                    }
                ],
            }
        ],
    }


def test_sports_operations_status_accepts_soul_wizard_context_without_creating_operations() -> None:
    report = build_sports_operations_status_from_snapshot(
        _snapshot(),
        soul_wizard_payload=_wizard_payload(),
    )

    alignment = report.wizard_alignment
    assert alignment["present"] is True
    assert alignment["source"] == "soul_wizard"
    assert alignment["draft_id"] == "dcc-2027"
    assert alignment["readiness_status"] == "ready_for_review"
    assert alignment["phase_count"] == 1
    assert alignment["activity_count"] == 1
    assert alignment["integration_decision"] == "wizard_ready_for_operations_review"
    assert alignment["live_operations_created"] is False
    assert alignment["operational_writes_allowed"] is False
    assert report.safety_summary["soul_wizard_context_accepted"] is True
    assert report.safety_summary["soul_wizard_creates_live_operations"] is False


def test_sports_operations_status_marks_incomplete_wizard_context_as_planning_gap() -> None:
    report = build_sports_operations_status_from_snapshot(
        _snapshot(),
        soul_wizard_payload={"tournament_name": "Copa incompleta"},
    )

    alignment = report.wizard_alignment
    assert alignment["present"] is True
    assert alignment["readiness_status"] == "incomplete"
    assert alignment["required_missing_count"] >= 2
    assert "edition_year" in alignment["missing_paths"]
    assert "phases" in alignment["missing_paths"]
    assert alignment["integration_decision"] == "wizard_needs_completion_before_operations_review"
    assert alignment["live_operations_created"] is False
