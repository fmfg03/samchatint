from __future__ import annotations

from samchat.assistant.owner_entity_dossier_audit import (
    OWNER_ENTITY_DOSSIER_AUDIT_ONLY,
    build_owner_entity_dossier_audit_from_snapshot,
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
        "soul": {
            "tournament": {
                "id": "tor-1",
                "name": "Liga Telmex Telcel 2026",
                "slug": "liga-telmex-telcel-beisbol-2026",
            },
            "operations": {
                "entities": [
                    {
                        "entity_name": "Morelos",
                        "teams_count": 1,
                        "players_count": 2,
                        "teams": [
                            {
                                "team_id": "team-1",
                                "team_name": "Halcones Morelos",
                                "category": "Sub 15",
                                "branch": "Varonil",
                                "players_count": 2,
                                "documents_complete_players": 1,
                                "documents_verified_players": 0,
                                "primary_manager": {
                                    "name": "Mario",
                                    "email": "mario@example.com",
                                    "phone": "7771234567",
                                },
                            }
                        ],
                    }
                ]
            },
        },
    }


def test_owner_entity_dossier_audit_wraps_dg_dossier_without_writes() -> None:
    report = build_owner_entity_dossier_audit_from_snapshot(_snapshot())

    assert report.audit_id == "owner_entity_dossier_audit_v1"
    assert report.decision == "wrap_before_wiring"
    assert report.entity_count == 1
    assert report.tournament["name"] == "Liga Telmex Telcel 2026"
    assert report.execution_status == "not_executed"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.audit_language == OWNER_ENTITY_DOSSIER_AUDIT_ONLY
    assert report.safety_summary["writes_enabled"] is False


def test_owner_entity_dossier_audit_identifies_supported_missing_and_overlap() -> None:
    report = build_owner_entity_dossier_audit_from_snapshot(_snapshot())
    entity = report.entities[0]

    assert entity.entity_name == "Morelos"
    assert "entity_name" in entity.supported_fields
    assert "real_teams" in entity.supported_fields
    assert "players_by_category_age_gender" in entity.supported_fields
    assert "real_teams" in entity.overlap_with_owner_pack
    assert any("Equipos esperados" in item for item in entity.missing_fields)
    assert any("finance" in item.lower() or "financ" in item.lower() for item in entity.improvement_notes)
    assert entity.source_summary["teams_count"] == 1
    assert entity.source_summary["contacts_count"] == 1


def test_owner_entity_dossier_audit_filters_entity_name_fail_closed() -> None:
    report = build_owner_entity_dossier_audit_from_snapshot(_snapshot(), entity_name="Jalisco")

    assert report.decision == "do_not_wire_directly"
    assert report.entity_count == 0
    assert report.missing_evidence == []
    assert "No hay entidades" in report.headline


def test_owner_entity_dossier_audit_dict_payload_is_assistant_friendly() -> None:
    payload = build_owner_entity_dossier_audit_from_snapshot(_snapshot()).to_dict()

    assert payload["audit_id"] == "owner_entity_dossier_audit_v1"
    assert payload["entities"][0]["entity_name"] == "Morelos"
    assert payload["redundancy_notes"]
    assert payload["recommended_next_steps"]
    assert payload["non_claims"]
