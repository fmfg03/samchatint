"""Configured Postgres registration must not read the Supabase roster."""

import pytest

from samchat.tournaments_v2.services.soul_service import build_tournament_soul_snapshot


class SourceClient:
    def __init__(self, *, forbid_registration):
        self.forbid_registration = forbid_registration
        self.queried = []
        self.rows = {
            "tournaments": [
                {"id": "t1", "name": "Copa", "slug": "copa-2026", "is_active": True}
            ],
            "categories": [
                {"id": "c1", "name": "Open", "tournament_id": "t1", "branch": "femenil"}
            ],
            "teams": [
                {
                    "id": "team1",
                    "team_name": "Supabase roster team",
                    "tournament_id": "t1",
                    "state": "Jalisco",
                    "instagram_url": "https://example.invalid/private",
                }
            ],
            "registrations": [{"id": "r1", "team_id": "team1", "category_id": "c1"}],
            "players": [
                {
                    "id": "p1",
                    "registration_id": "r1",
                    "documents_complete": True,
                    "documents_verified": True,
                }
            ],
            "team_managers": [
                {
                    "id": "manager1",
                    "team_id": "team1",
                    "is_primary": True,
                    "first_name": "PRIVATE",
                    "email": "private@example.invalid",
                }
            ],
            "matches": [
                {
                    "id": "m1",
                    "category_id": "c1",
                    "home_team_id": "team1",
                    "away_team_id": "team2",
                    "phase": "national",
                    "match_date": "2026-10-05",
                    "status": "scheduled",
                }
            ],
            "team_standings": [
                {
                    "id": "st1",
                    "team_id": "team1",
                    "category_id": "c1",
                    "played": 1,
                    "points": 3,
                }
            ],
            "match_cedulas": [{"id": "sheet1", "match_id": "m1", "status": "ready"}],
            "gallery_photos": [{"id": "photo1", "category_id": "c1", "title": "Photo"}],
            "featured_videos": [
                {"id": "video1", "category_id": "c1", "title": "Video"}
            ],
            "live_streams": [{"id": "stream1", "match_id": "m1", "title": "Stream"}],
        }

    async def fetch_all_rows(self, *, table, **kwargs):
        assert not (
            self.forbid_registration
            and table in {"teams", "registrations", "players", "team_managers"}
        ), f"Forbidden roster read: {table}"
        self.queried.append(table)
        return self.rows.get(table, [])


@pytest.mark.asyncio
async def test_soul_excludes_roster_reads_but_keeps_other_domains():
    client = SourceClient(forbid_registration=True)
    snapshot = await build_tournament_soul_snapshot(
        tournament_slug="copa-2026", include_registration=False, client=client
    )
    assert snapshot["registration_status"] == "handled_elsewhere"
    assert snapshot["source_metadata"]["registration_tables_queried"] is False
    assert snapshot["filters"]["include_registration"] is False
    for key in (
        "teams_count",
        "players_count",
        "entities_count",
        "registrations_count",
        "document_players_complete",
        "document_players_verified",
        "teams_with_incomplete_documents",
    ):
        assert snapshot["summary"][key] is None
    assert snapshot["summary"]["categories_count"] == 1
    assert snapshot["summary"]["matches_count"] == 1
    assert snapshot["summary"]["standings_rows_count"] == 1
    assert snapshot["operations"]["matches"][0]["match_id"] == "m1"
    assert snapshot["operations"]["matches"][0]["home_team_id"] == "team1"
    assert snapshot["operations"]["standings"][0]["points"] == 3
    assert snapshot["operations"]["cedulas_count"] == 1
    assert snapshot["marketing"]["media"]["photos_count"] == 1
    assert snapshot["marketing"]["media"]["videos_count"] == 1
    assert snapshot["marketing"]["media"]["streams_count"] == 1
    assert snapshot["marketing"]["team_marketing_profiles_count"] is None
    assert snapshot["breakdowns"] == {"entities": [], "categories": [], "branches": []}
    soul = snapshot["soul"]
    assert soul["compliance"]["status"] == "handled_elsewhere"
    assert soul["compliance"]["completion_rate"] is None
    assert soul["compliance"]["players_count"] is None
    assert soul["marketing"]["team_marketing_profiles_count"] is None
    assert soul["entity_folders_seed"] == []
    assert soul["national_phase"]["matches_count"] == 1
    assert not any(risk["code"] == "missing_teams" for risk in soul["risks"])
    assert not any(
        "Registrar equipos" in action or "Registrar jugadores" in action
        for action in soul["pending_actions"]
    )
    assert {
        "tournaments",
        "categories",
        "matches",
        "team_standings",
        "match_cedulas",
        "gallery_photos",
        "featured_videos",
        "live_streams",
    } <= set(client.queried)
    assert "PRIVATE" not in str(snapshot)
    assert "Supabase roster team" not in str(snapshot)


@pytest.mark.asyncio
async def test_default_soul_retains_registration_contract():
    client = SourceClient(forbid_registration=False)
    snapshot = await build_tournament_soul_snapshot(
        tournament_slug="copa-2026", client=client
    )
    assert {"teams", "registrations", "players", "team_managers"} <= set(client.queried)
    assert snapshot["summary"]["teams_count"] == 1
    assert snapshot["summary"]["players_count"] == 1
    assert snapshot["soul"]["compliance"]["completion_rate"] == 1
    assert snapshot["operations"]["matches"][0]["home_team"] == "Supabase roster team"
    assert "registration_status" not in snapshot
