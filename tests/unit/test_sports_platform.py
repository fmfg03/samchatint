from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from devnous.gastos.routes.admin_routes import admin_sports_platform
from samchat.sports_platform import (
    build_director_general_entity_dossier,
    build_sports_platform_snapshot,
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
        "registrations": [
            {"id": "reg-1", "team_id": "team-1", "payment_status": "paid"}
        ],
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
        "communications": {
            "email_inbox_unread": 2,
            "whatsapp_unread": 1,
            "scheduled_emails_count": 1,
            "whatsapp_templates_active": 3,
        },
        "marketing": {
            "media": {"photos_count": 4, "videos_count": 0, "streams_count": 0}
        },
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
                        "teams_count": 1,
                        "players_count": 2,
                        "teams": [
                            {
                                "team_id": "team-1",
                                "team_name": "Halcones Morelos",
                                "category": "Sub 15",
                                "players_count": 2,
                                "documents_complete_players": 1,
                                "documents_verified_players": 0,
                                "primary_manager": {
                                    "email": "mario@example.com",
                                    "phone": "7771234567",
                                },
                                "instagram_url": "https://instagram.com/halcones",
                            },
                            {
                                "team_id": "team-2",
                                "team_name": "Leones Morelos",
                                "category": "Sub 15",
                                "players_count": 2,
                                "documents_complete_players": 2,
                                "documents_verified_players": 2,
                                "primary_manager": {
                                    "email": "leones@example.com",
                                    "phone": "7770000000",
                                },
                            },
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


def test_build_sports_platform_snapshot_exposes_core_sports_modules():
    platform = build_sports_platform_snapshot(_snapshot())

    assert platform["read_only"] is True
    assert platform["summary"]["teams"] == 1
    assert platform["mission_control"]["title"] == "Sports Mission Control"
    assert platform["mission_control"]["today_plan"]
    assert platform["command_center"]["title"] == "Tournament Command Center"
    assert platform["team_journey"]["title"] == "Team Journey"
    assert platform["team_journey"]["blocked_count"] == 1
    assert platform["match_center"]["title"] == "Match Center"
    assert (
        platform["match_center"]["matches"][0]["home_team_name"] == "Halcones Morelos"
    )
    assert platform["action_queue"]["title"] == "Action Queue"
    assert platform["action_queue"]["open_count"] >= 3
    assert platform["action_queue"]["high_count"] >= 1
    assert platform["action_queue"]["actions"][0]["severity"] == "high"
    assert platform["ops_brief"]["title"] == "One-click Ops Brief"
    assert "Readiness global" in platform["ops_brief"]["plain_text"]
    assert "WhatsApp" in platform["ops_brief"]["export_targets"]
    assert platform["global_readiness"]["title"] == "Readiness Score global"
    assert platform["global_readiness"]["status"] in {"green", "yellow", "red"}
    assert platform["ops_copilot"]["title"] == "Ops Copilot"
    assert platform["ops_copilot"]["drafts"]
    assert platform["public_microsite"]["title"] == "Public microsite generator"
    assert platform["public_microsite"]["preview_url"].startswith("/sports/")
    assert platform["sponsor_media"]["title"] == "Sponsor/Media dashboard"
    assert platform["sponsor_media"]["proof_points"]
    assert platform["incident_center"]["title"] == "Incident Center"
    assert platform["incident_center"]["open_count"] >= 1
    assert platform["venue_ops"]["title"] == "Venue Ops"
    assert platform["venue_ops"]["venues"][0]["venue"] == "1"
    assert platform["post_tournament_report"]["title"] == "Post-tournament report"
    assert "PDF" in platform["post_tournament_report"]["export_formats"]
    assert platform["team_portal"]["action_needed_count"] == 1
    assert platform["roster_intelligence"]["completion_rate"] == 0.5
    assert platform["matchday_ops"]["open_cedulas_count"] == 1
    assert platform["communications"]["whatsapp_unread"] == 1
    assert platform["risk_radar"]["risk_count"] == 1
    assert platform["sports_crm"]["entities"][0]["entity_name"] == "Morelos"
    assert platform["public_layer"]["media_ready"] is True
    assert platform["mobile_field_app"]["blocking_counts"]["open_cedulas"] == 1
    assert platform["ai_ops_assistant"]["suggested_prompts"]


def test_sponsor_media_declares_marketing_accelerator_modules():
    platform = build_sports_platform_snapshot(_snapshot())

    assert "sponsor_media" in platform
    sponsor_media = platform["sponsor_media"]
    assert sponsor_media["status"] == "commercial_snapshot_v0"
    modules = sponsor_media["modules"]
    assert [module["id"] for module in modules] == [
        "video_recap_sponsor_proof",
        "content_rendering_sponsor_evidence",
        "sponsor_obligation_tracker",
        "brand_compliance_logo_evidence",
        "content_approval_queue",
        "matchday_content_command_center",
        "sponsor_proof_package_builder",
    ]

    for module in modules:
        assert module["production_integrated"] is False
        assert module["human_review_required"] is True
        assert module["implementation_stage"] == "commercial_snapshot_v0"

    modules_by_id = {module["id"]: module for module in modules}
    video_module = modules_by_id["video_recap_sponsor_proof"]
    rendering_module = modules_by_id["content_rendering_sponsor_evidence"]

    assert video_module["base_concept"] == "browser-use/video-use"
    assert video_module["function"] == "video_editing_and_assembly"
    assert "highlights" in video_module["outputs"]
    assert "sponsor_pdfs" not in video_module["outputs"]
    assert "dashboard_screenshots" not in video_module["outputs"]
    assert "event_screenshots" not in video_module["outputs"]

    assert rendering_module["base_concept"] == "Cloudflare Browser Run"
    assert rendering_module["function"] == "render_capture_export_visual_assets"
    assert "sponsor_pdfs" in rendering_module["outputs"]
    assert "dashboard_screenshots" in rendering_module["outputs"]
    assert "highlights" not in rendering_module["outputs"]
    assert "daily_recaps" not in rendering_module["outputs"]

    assert (
        modules_by_id["sponsor_obligation_tracker"]["function"]
        == "track_sponsor_deliverables_against_contract_commitments"
    )
    assert (
        "sponsor_deliverable_matrix"
        in modules_by_id["sponsor_obligation_tracker"]["outputs"]
    )
    assert (
        "missing_evidence_alerts"
        in modules_by_id["sponsor_obligation_tracker"]["outputs"]
    )
    assert (
        "logo_presence_checks"
        in modules_by_id["brand_compliance_logo_evidence"]["outputs"]
    )
    assert "approved_packages" in modules_by_id["content_approval_queue"]["outputs"]
    assert (
        "active_sponsors_by_match"
        in modules_by_id["matchday_content_command_center"]["outputs"]
    )
    assert (
        "obligation_coverage_summary"
        in modules_by_id["sponsor_proof_package_builder"]["outputs"]
    )

    expected_boundary = {
        "not_autonomous_publishing",
        "not_creative_replacement",
        "not_canva_replacement",
        "not_video_editor_replacement",
        "requires_brand_or_sponsor_approval",
        "evidence_assistive_not_guaranteed_detection",
    }
    assert set(sponsor_media["claim_boundary"]) == expected_boundary
    for module in modules:
        assert set(module["claim_boundary"]) == expected_boundary
    assert sponsor_media["proof_of_performance_v1"]["status"] == "internal_v1"
    assert sponsor_media["approval_workflow_v1"]["status"] == "state_machine_v1"
    assert sponsor_media["direct_social_publishing"] == {
        "status": "client_not_authorized",
        "reason": (
            "Fundacion Telmex requires human review and "
            "manual/human-supervised publishing."
        ),
        "external_publishing_enabled": False,
        "manual_distribution_required": True,
    }


@pytest.mark.asyncio
async def test_admin_sports_platform_renders_command_center(monkeypatch):
    async def fake_soul_snapshot(*_args, **_kwargs):
        return _snapshot()

    monkeypatch.setattr(
        "samchat.tournaments_v2.services.build_tournament_soul_snapshot",
        fake_soul_snapshot,
    )

    response = await admin_sports_platform(
        request=SimpleNamespace(),
        current_empleado=SimpleNamespace(nombre="Admin", rol="finanzas"),
        tournament_key="all",
        tournament_slug="liga-telmex-telcel-beisbol-2026",
    )
    body = response.body.decode("utf-8")

    assert "Command Center deportivo" in body
    assert "Portal para equipos + Roster inteligente" in body
    assert "Sports Mission Control" in body
    assert "Action Queue" in body
    assert "One-click Ops Brief" in body
    assert "Brief operativo" in body
    assert "Team Journey" in body
    assert "Match Center" in body
    assert "Joyas Sports" in body
    assert "Ops Copilot" in body
    assert "Public microsite generator" in body
    assert "Sponsor / Media dashboard" in body
    assert "Incident Center" in body
    assert "Venue Ops" in body
    assert "Post-tournament report" in body
    assert "Matchday Ops" in body
    assert "AI Ops Assistant" in body
    assert "Halcones Morelos" in body


def test_director_general_entity_dossier_is_read_only_and_fail_closed():
    dossier = build_director_general_entity_dossier(_snapshot())

    assert dossier["read_only"] is True
    assert dossier["schema_version"] == "samchat.dg_entity_dossier.v1"
    assert dossier["tournament"]["name"] == "Liga Telmex Telcel 2026"
    assert dossier["summary"]["entities_count"] == 1
    assert dossier["summary"]["players_count"] == 4
    assert dossier["non_claims"]

    entity = dossier["entities"][0]
    assert entity["entity_name"] == "Morelos"
    assert entity["operations"]["ps_owner"] is None
    assert entity["operations"]["expected_teams_status"] == "pending_data"
    assert entity["finance"]["source_status"] == "pending_finance_entity_bridge"
    assert "Equipos esperados" in " ".join(entity["operations"]["pending_fields"])
    assert "Fecha y monto" in " ".join(entity["finance"]["pending_fields"])


def test_director_general_entity_dossier_groups_real_teams_and_contacts():
    dossier = build_director_general_entity_dossier(_snapshot())
    operations = dossier["entities"][0]["operations"]

    real_rows = operations["real_teams_by_category_gender"]
    assert real_rows == [
        {
            "category": "Sub 15",
            "gender_or_branch": "Sin género/rama",
            "teams_count": 2,
            "players_count": 4,
            "team_names": ["Halcones Morelos", "Leones Morelos"],
        }
    ]
    assert operations["document_metrics"]["completion_rate"] == 0.75
    assert operations["document_metrics"]["verification_rate"] == 0.5
    assert len(operations["entity_contacts"]) == 2
    assert {contact["email"] for contact in operations["entity_contacts"]} == {
        "mario@example.com",
        "leones@example.com",
    }
    assert (
        operations["players_by_category_age_gender"][0]["age_status"]
        == "pending_player_birthdate_rollup"
    )


def test_director_general_entity_dossier_route_is_exposed():
    source = Path("src/devnous/gastos/routes/admin_routes.py").read_text()

    assert '"/admin/sports/expediente-entidades"' in source
    assert "Expediente ejecutivo por entidad" in source
    assert "build_director_general_entity_dossier" in source
    assert "Command Center" in source
