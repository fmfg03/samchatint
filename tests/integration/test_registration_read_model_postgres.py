"""Owner-run migration and read contracts against explicitly opted-in temp PG."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from devnous.copa_telmex.registration_read_model import dispatch_registration_snapshot


@pytest.mark.asyncio
async def test_ephemeral_postgres_migration_and_read_contract(monkeypatch):
    socket = os.environ.get("SAMCHAT_TEST_PG_SOCKET")
    if not socket:
        pytest.skip("Explicit ephemeral PostgreSQL socket required")
    assert socket.startswith("/tmp/samchat-ctt-pg-"), "Only isolated temporary sockets"
    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from devnous.copa_telmex import registration_read_model

    failures = []
    read_rows = registration_read_model._rows

    async def traced_rows(*args, **kwargs):
        try:
            return await read_rows(*args, **kwargs)
        except Exception as exc:
            failures.append(str(exc))  # Synthetic test data only.
            raise

    monkeypatch.setattr(registration_read_model, "_rows", traced_rows)

    port = int(os.environ.get("SAMCHAT_TEST_PG_PORT", "55439"))
    schema = "test_registration_" + uuid4().hex
    admin = await asyncpg.connect(
        host=socket, port=port, user="postgres", database="postgres"
    )
    engine = None
    root = Path(__file__).resolve().parents[2]
    up = (
        root / "database/migrations/20261005_copa_telmex_tournament_editions.sql"
    ).read_text()
    down = (
        root
        / "database/migrations/20261005_copa_telmex_tournament_editions.rollback.sql"
    ).read_text()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute(
            "CREATE TABLE tournaments(id UUID PRIMARY KEY, name TEXT, active BOOLEAN)"
        )
        await admin.execute(up)
        await admin.execute(up)  # idempotent installation, no auto-configuration
        a, b = uuid4(), uuid4()
        await admin.execute(
            "INSERT INTO tournaments VALUES ($1,'Copa',TRUE), ($2,'Copa Similar',TRUE)",
            a,
            b,
        )
        for project, year, slug in (
            (a, 2025, "copa-2025"),
            (a, 2026, "copa-2026"),
            (b, 2026, "copa-2026-extra"),
        ):
            await admin.execute(
                "INSERT INTO copa_telmex_tournament_editions "
                "(id,tournament_id,edition_year,roster_slug) VALUES ($1,$2,$3,$4)",
                uuid4(),
                project,
                year,
                slug,
            )
        for project, year, slug, error in (
            (a, 2026, "unique-slug", asyncpg.UniqueViolationError),
            (b, 2025, "copa-2026", asyncpg.UniqueViolationError),
            (uuid4(), 2024, "unknown-project", asyncpg.ForeignKeyViolationError),
        ):
            with pytest.raises(error):
                await admin.execute(
                    "INSERT INTO copa_telmex_tournament_editions "
                    "(id,tournament_id,edition_year,roster_slug) VALUES ($1,$2,$3,$4)",
                    uuid4(),
                    project,
                    year,
                    slug,
                )
        await admin.execute("""
            CREATE TABLE copa_telmex_teams(id UUID PRIMARY KEY, tournament_slug TEXT,
              name TEXT, state TEXT, municipality TEXT, category TEXT, gender TEXT,
              created_at TIMESTAMP);
            CREATE TABLE copa_telmex_players(id UUID PRIMARY KEY, team_id UUID,
              governance_state TEXT, birth_date DATE, curp TEXT);
            CREATE TABLE copa_telmex_registration_review_sessions(id UUID PRIMARY KEY,
              tournament_slug TEXT, committed_team_id UUID, status TEXT);
            CREATE TABLE copa_telmex_registration_review_drafts(id UUID PRIMARY KEY,
              session_id UUID, draft_version INTEGER,
              validation JSONB, extraction JSONB);
        """)
        team = uuid4()
        await admin.execute(
            "INSERT INTO copa_telmex_teams VALUES "
            "($1,'copa-2026','Team','Jalisco','Zapopan','Open','femenil',NOW())",
            team,
        )
        for state in ("ACTIVE", "LEGACY_ACTIVE", "PENDING_FINALITY"):
            await admin.execute(
                "INSERT INTO copa_telmex_players VALUES "
                "($1,$2,$3,'2000-02-03','PRIVATE-CURP')",
                uuid4(),
                team,
                state,
            )
        engine = create_async_engine(
            "postgresql+asyncpg://postgres@/postgres",
            connect_args={
                "host": socket,
                "port": port,
                "server_settings": {"search_path": schema},
            },
        )
        async with AsyncSession(engine) as session:
            from devnous.copa_telmex.models import Team

            pending_team = Team(id=uuid4(), name="Unflushed private team")
            session.add(pending_team)
            snapshot = await dispatch_registration_snapshot(
                session, tournament_id=str(a), edition_year=2026
            )
            assert snapshot["status"] == "AVAILABLE", failures
            assert snapshot["counts"] == {
                "total_teams": 1,
                "active_players": 2,
                "provisional_players": 1,
                "pending_reviews": 0,
            }
            assert snapshot["reports"]["summary"]["jugadores"] == 2
            assert pending_team in session.new
            assert snapshot["counts"]["total_teams"] == 1
            filtered = await dispatch_registration_snapshot(
                session,
                tournament_id=str(a),
                edition_year=2026,
                filters={"date_from": "2026-01-01", "date_to": "2026-12-31"},
            )
            assert filtered["counts"]["total_teams"] == 1
            assert (
                await dispatch_registration_snapshot(
                    session, tournament_id=str(a), edition_year=2024
                )
            )["status"] == "SCOPE_MISSING"
            assert (
                await dispatch_registration_snapshot(
                    session, tournament_id=str(a), edition_year=2025
                )
            )["status"] == "EMPTY"
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await admin.execute("DELETE FROM tournaments WHERE id=$1", a)
        await admin.execute("DROP TABLE copa_telmex_players")
        async with AsyncSession(engine) as session:
            failed = await dispatch_registration_snapshot(
                session, tournament_id=str(a), edition_year=2026
            )
            assert failed["status"] == "SOURCE_FAILED"
            from sqlalchemy import text

            assert (await session.execute(text("SELECT 1"))).scalar() == 1
        await admin.execute(down)
        await admin.execute(down)
        assert (
            await admin.fetchval(
                "SELECT to_regclass('copa_telmex_tournament_editions')"
            )
            is None
        )
        async with AsyncSession(engine) as session:
            assert (
                await dispatch_registration_snapshot(
                    session, tournament_id=str(a), edition_year=2026
                )
                is None
            )
            # Missing migration does not abort the transaction.
            from sqlalchemy import text

            assert (await session.execute(text("SELECT 1"))).scalar() == 1
    finally:
        if engine:
            await engine.dispose()
        await admin.execute("SET search_path TO public")
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()
