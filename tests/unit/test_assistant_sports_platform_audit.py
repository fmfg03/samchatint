from __future__ import annotations

from samchat.assistant.sports_platform_audit import (
    SPORTS_PLATFORM_AUDIT_ONLY,
    build_sports_platform_audit_from_snapshot,
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


def test_sports_platform_audit_classifies_raw_snapshot_before_wiring() -> None:
    report = build_sports_platform_audit_from_snapshot(_snapshot())

    assert report.audit_id == "sports_platform_audit_v1"
    assert report.decision == "wrap_before_wiring"
    assert report.tournament["name"] == "Liga Telmex Telcel 2026"
    assert "mission_control" in report.assistant_ready_modules
    assert "action_queue" in report.assistant_ready_modules
    assert "command_center" in report.internal_source_modules
    assert "sponsor_media" in report.commercial_or_demo_modules
    assert report.safety_summary["raw_snapshot_should_not_be_exposed"] is True
    assert report.execution_status == "not_executed"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.audit_language == SPORTS_PLATFORM_AUDIT_ONLY


def test_sports_platform_audit_module_payloads_explain_exposure() -> None:
    report = build_sports_platform_audit_from_snapshot(_snapshot())
    modules = {item.module_id: item for item in report.modules}

    assert modules["mission_control"].classification == "assistant_ready_summary"
    assert "prioridades" in modules["mission_control"].reason.lower() or "conteos" in modules["mission_control"].reason.lower()
    assert modules["sponsor_media"].recommended_exposure.startswith("Do not expose")
    assert modules["action_queue"].evidence_keys
    assert report.redundancy_notes
    assert report.improvement_notes
    assert report.recommended_next_steps


def test_sports_platform_audit_can_focus_on_assistant_ready_modules() -> None:
    report = build_sports_platform_audit_from_snapshot(_snapshot(), focus="assistant_ready")

    assert report.module_count == len(report.modules)
    assert report.module_count >= 1
    assert all(item.classification == "assistant_ready_summary" for item in report.modules)
    assert "mission_control" in report.assistant_ready_modules
    assert not report.commercial_or_demo_modules


def test_sports_platform_audit_dict_payload_is_read_only() -> None:
    payload = build_sports_platform_audit_from_snapshot(_snapshot()).to_dict()

    assert payload["audit_id"] == "sports_platform_audit_v1"
    assert payload["safety_summary"]["writes_enabled"] is False
    assert payload["modules"][0]["module_id"]
    assert payload["module_count"] == len(payload["modules"])
