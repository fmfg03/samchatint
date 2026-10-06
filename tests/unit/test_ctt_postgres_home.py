"""Registration consistency on Direction's serial and batched home paths."""

from unittest.mock import AsyncMock

import pytest

from samchat.client_executive import home, service


@pytest.mark.asyncio
@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("status", ["AVAILABLE", "EMPTY", "SOURCE_FAILED"])
async def test_home_uses_configured_projection_before_legacy_reads(
    monkeypatch, batched, status
):
    configured = {"id": "00000000-0000-0000-0000-000000000001", "name": "Copa"}
    legacy = {"id": "00000000-0000-0000-0000-000000000002", "name": "Legacy"}
    scope = {
        "portfolio_ids": [],
        "portfolio_id": None,
        "tournament_id": None,
        "portfolios": [],
        "tournaments": [configured, legacy],
        "selected": [configured, legacy],
    }
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=scope))
    session = object()
    events = []
    available = status != "SOURCE_FAILED"
    snapshot = {
        "available": available,
        "status": status,
        "source": "postgres_registration",
        "summary": {
            "total_teams": 0 if status == "EMPTY" else 7,
            "active_players": 0 if status == "EMPTY" else 20,
            "provisional_players": 0 if status == "EMPTY" else 3,
            "pending_reviews": 2,
        },
        "next_action": "Revisar fuente" if not available else None,
    }

    async def dispatch(read_session, *, tournament_id, edition_year):
        assert read_session is session
        assert edition_year == 2026
        events.append(("postgres", tournament_id))
        return snapshot if tournament_id == configured["id"] else None

    async def batch(tournaments, *, edition_year):
        assert tournaments == [legacy]
        assert edition_year == 2026
        events.append(("legacy_batch", legacy["id"]))
        return {legacy["id"]: {"teams_count": 9, "players_count": 99}}

    async def dossier(tournament, *, edition_year):
        assert tournament == legacy
        assert edition_year == 2026
        events.append(("legacy_serial", legacy["id"]))
        return {
            "source_status": "available",
            "summary": {"teams_count": 9, "players_count": 99},
        }

    monkeypatch.setattr(service, "dispatch_registration_snapshot", dispatch)
    monkeypatch.setattr(service, "_build_operational_summaries", batch)
    monkeypatch.setattr(service, "_build_operational_dossier", dossier)
    monkeypatch.setattr(
        home, "_tournament_read_factory", lambda _: object() if batched else None
    )
    payload, _ = await home.build_home(
        session,
        actor="direction",
        superadmin=False,
        year=2026,
        source_access={"budget": False, "finance": False},
    )
    rows = {row["id"]: row for row in payload["tournaments"]}
    actual = rows[configured["id"]]["operations"]
    assert actual["teams"] == (
        snapshot["summary"]["total_teams"] if available else None
    )
    assert actual["players"] == (
        snapshot["summary"]["active_players"] if available else None
    )
    assert actual["provisional_players"] == (
        3 if status == "AVAILABLE" else 0 if available else None
    )
    assert actual["registration_status"] == status
    assert actual["registration_source"] == "postgres_registration"
    assert actual["next_action"] == snapshot["next_action"]
    assert rows[legacy["id"]]["operations"]["teams"] == 9
    assert rows[legacy["id"]]["operations"]["players"] == 99
    assert events[:2] == [
        ("postgres", configured["id"]),
        ("postgres", legacy["id"]),
    ]
    assert events[2:] == [
        ("legacy_batch" if batched else "legacy_serial", legacy["id"])
    ]


@pytest.mark.asyncio
async def test_fully_configured_home_skips_alternate_summary_batch(monkeypatch):
    tournaments = [
        {"id": "00000000-0000-0000-0000-000000000001", "name": "First"},
        {"id": "00000000-0000-0000-0000-000000000002", "name": "Second"},
    ]
    scope = {
        "portfolio_ids": [],
        "portfolio_id": None,
        "tournament_id": None,
        "portfolios": [],
        "tournaments": tournaments,
        "selected": tournaments,
    }
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(return_value=scope))
    dispatch = AsyncMock(
        return_value={
            "available": True,
            "status": "EMPTY",
            "source": "postgres_registration",
            "summary": {"total_teams": 0, "active_players": 0},
        }
    )
    monkeypatch.setattr(service, "dispatch_registration_snapshot", dispatch)
    batch, dossier = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "_build_operational_summaries", batch)
    monkeypatch.setattr(service, "_build_operational_dossier", dossier)
    monkeypatch.setattr(home, "_tournament_read_factory", lambda _: object())
    payload, _ = await home.build_home(
        object(),
        actor="direction",
        superadmin=False,
        year=2026,
        source_access={"budget": False, "finance": False},
    )
    assert dispatch.await_count == 2
    assert all(row["operations"]["teams"] == 0 for row in payload["tournaments"])
    batch.assert_not_awaited()
    dossier.assert_not_awaited()


@pytest.mark.asyncio
async def test_home_resolves_authority_before_registration_read(monkeypatch):
    denied = service.ClientExecutiveAccessError("Outside assigned scope")
    monkeypatch.setattr(home, "resolve_scope", AsyncMock(side_effect=denied))
    dispatch = AsyncMock()
    monkeypatch.setattr(service, "dispatch_registration_snapshot", dispatch)
    with pytest.raises(service.ClientExecutiveAccessError):
        await home.build_home(object(), actor="outside", superadmin=False, year=2026)
    dispatch.assert_not_awaited()
