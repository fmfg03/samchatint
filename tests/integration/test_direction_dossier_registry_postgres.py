"""Database contracts for exact CTT observed cuts, using a disposable socket."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import devnous.gastos  # noqa: F401 - initialize owning runtime first
from samchat.client_executive import dossier_registry as registry

MIGRATIONS = Path(__file__).resolve().parents[2] / "database" / "migrations"


@pytest.mark.asyncio
async def test_roster_cuts_observe_today_once_and_do_not_rebuild_history(monkeypatch):
    socket = os.environ.get("SAMCHAT_DOSSIER_TEST_PG_SOCKET", "")
    if not socket:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL socket.")
    assert socket.startswith("/tmp/samchat-dossier-pg-")
    port = int(os.environ.get("SAMCHAT_DOSSIER_TEST_PG_PORT", "55474"))
    admin = await asyncpg.connect(host=socket, port=port, user="postgres")
    schema = f"dossier_roster_test_{uuid4().hex}"
    engine = create_async_engine(
        "postgresql+asyncpg://postgres@/postgres",
        connect_args={
            "host": socket,
            "port": port,
            "server_settings": {"search_path": schema},
        },
    )
    tour, outside, old, future, unrelated = (uuid4() for _ in range(5))
    team_id, future_team, outside_team = (uuid4() for _ in range(3))
    migration = MIGRATIONS / "20261124_direction_dossier_roster_daily_cuts.sql"
    rollback = MIGRATIONS / "20261124_direction_dossier_roster_daily_cuts.rollback.sql"
    directory = AsyncMock(return_value=registry._section("Pública", "empty"))
    monkeypatch.setattr(registry, "_directory", directory)
    try:
        await admin.execute(
            "DO $$ BEGIN CREATE ROLE devnous_user NOLOGIN; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute("""
            CREATE TABLE tournaments (id uuid PRIMARY KEY, name text, active boolean);
            CREATE TABLE copa_telmex_teams (
                id uuid PRIMARY KEY, name text, state text, category text,
                gender text, tournament_slug text, updated_at timestamp
            );
            CREATE TABLE copa_telmex_players (
                team_id uuid, governance_state text, updated_at timestamp, curp text
            );
        """)
        await admin.execute(
            (MIGRATIONS / "20261005_copa_telmex_tournament_editions.sql").read_text()
        )
        await admin.execute(migration.read_text())
        await admin.execute(migration.read_text())
        await admin.execute(rollback.read_text())
        await admin.execute(migration.read_text())
        privileges = await admin.fetchrow("""
            SELECT has_table_privilege(
                       'devnous_user', 'direction_dossier_roster_daily_cuts', 'SELECT'
                   ) AS can_read,
                   has_table_privilege(
                       'devnous_user', 'direction_dossier_roster_daily_cuts', 'INSERT'
                   ) AS can_insert
        """)
        assert privileges["can_read"] and not privileges["can_insert"]
        await admin.executemany(
            "INSERT INTO tournaments VALUES ($1, $2, TRUE)",
            [(tour, "Copa Telmex"), (outside, "Otro torneo")],
        )
        await admin.executemany(
            """
            INSERT INTO copa_telmex_tournament_editions
                (id, tournament_id, edition_year, roster_slug)
            VALUES ($1, $2, $3, $4)
            """,
            [
                (old, tour, 2026, "copa-telmex-2026"),
                (future, tour, 2027, "copa-telmex-2027"),
                (unrelated, outside, 2026, "fuera-2026"),
            ],
        )
        await admin.execute(
            """
            INSERT INTO copa_telmex_teams VALUES
                ($1,'Equipo observado','Edo. México','Juvenil','varonil',
                 'copa-telmex-2026','2026-11-23 14:00'),
                ($2,'Otro año','Jalisco','Femenil','femenil',
                 'copa-telmex-2027','2027-03-15 10:00'),
                ($3,'Otro torneo','Puebla','Juvenil','varonil',
                 'fuera-2026','2026-11-23 14:00');
            """,
            team_id,
            future_team,
            outside_team,
        )
        await admin.execute(
            """
            INSERT INTO copa_telmex_players VALUES
                ($1,'ACTIVE','2026-11-23 14:00','not-public'),
                ($1,'LEGACY_ACTIVE','2026-11-24 14:00','not-public'),
                ($1,'PROVISIONAL','2026-11-25 14:00','not-public'),
                ($2,'ACTIVE','2027-03-15 10:00','not-public');
            """,
            team_id,
            future_team,
        )
        maker = async_sessionmaker(engine)
        async with maker.begin() as session:
            live = await registry.load_dossier_registry(
                session,
                tournament={"id": str(tour)},
                edition_year=2026,
                operations_allowed=True,
            )
            (team,) = live["team_roster"]["rows"]
            assert team["name"] == "Equipo observado"
            assert team["state_slug"] == "estado-de-mexico"
            assert team["player_registrations"] == 2
            assert team["updated_at"] == "2026-11-24T14:00:00"
            assert "curp" not in team
            without_link = await registry.load_dossier_registry(
                session,
                tournament={"id": str(tour)},
                edition_year=2028,
                operations_allowed=True,
            )
            assert without_link["team_roster"]["status"] == "scope_missing"
            assert without_link["directory"]["status"] == "empty"
            sibling = await registry.load_dossier_registry(
                session,
                tournament={"id": str(outside)},
                edition_year=2026,
                operations_allowed=True,
            )
            assert sibling["directory"]["status"] == "not_configured"
            preview = await registry.record_observed_roster_cut(
                session, tournament_id=str(tour), edition_year=2026
            )
            assert preview["status"] == "ready"
            assert (
                await admin.fetchval(
                    "SELECT COUNT(*) FROM direction_dossier_roster_daily_cuts"
                )
                == 0
            )
            result = await registry.record_observed_roster_cut(
                session, tournament_id=str(tour), edition_year=2026, apply=True
            )
            assert result["status"] == "recorded"
            assert result["team_count"] == 1

        await admin.execute(
            "UPDATE copa_telmex_teams SET name = 'Nombre actual' WHERE id=$1", team_id
        )
        day = date.fromisoformat(result["cut_date"])
        async with maker.begin() as session:
            retry = await registry.record_observed_roster_cut(
                session, tournament_id=str(tour), edition_year=2026, apply=True
            )
            assert retry["status"] == "already_recorded"
            assert retry["observed_at"] == result["observed_at"]
            history = await registry.load_dossier_registry(
                session,
                tournament={"id": str(tour)},
                edition_year=2026,
                operations_allowed=True,
                cut_date=day,
            )
            assert history["team_roster"]["rows"][0]["name"] == "Equipo observado"
            assert history["directory"]["status"] == "not_recorded"
            for tournament, year, target_date in (
                (tour, 2026, day - timedelta(days=1)),
                (tour, 2027, day),
                (outside, 2026, day),
            ):
                missing = await registry.load_dossier_registry(
                    session,
                    tournament={"id": str(tournament)},
                    edition_year=year,
                    operations_allowed=True,
                    cut_date=target_date,
                )
                assert missing["team_roster"]["status"] == "not_recorded"
                assert missing["team_roster"]["rows"] == []
        assert directory.await_count == 2
        assert (
            await admin.fetchval(
                "SELECT COUNT(*) FROM direction_dossier_roster_daily_cuts"
            )
            == 1
        )
        with pytest.raises(asyncpg.RaiseError, match="Preserve observed"):
            await admin.execute(rollback.read_text())
        await admin.execute("ROLLBACK")
    finally:
        await engine.dispose()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
