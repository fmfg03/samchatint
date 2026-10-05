"""Exact edition isolation and safe aggregates on a synthetic database."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from devnous.copa_telmex.edition_models import CopaTelmexTournamentEdition
from devnous.copa_telmex.registration_read_model import (
    build_registration_snapshot,
    dispatch_registration_snapshot,
    probe_registration_scope,
)


class AsyncReadSession:
    """Exercise actual SQL against SQLite without an extra async dependency."""

    def __init__(self, connection):
        self.connection = connection
        self.statements = []

    def get_bind(self):
        return self.connection

    async def execute(self, statement, params):
        self.statements.append(str(statement))
        assert str(statement).lstrip().startswith("SELECT")
        return self.connection.execute(statement, params)


@pytest.fixture
def source():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        ddl = (
            "CREATE TABLE tournaments (id TEXT PRIMARY KEY, name TEXT, active BOOLEAN)",
            "CREATE TABLE copa_telmex_tournament_editions (id TEXT PRIMARY KEY, "
            "tournament_id TEXT, edition_year INTEGER, roster_slug TEXT UNIQUE, "
            "active BOOLEAN, created_at TEXT, UNIQUE(tournament_id, edition_year))",
            "CREATE TABLE copa_telmex_teams (id TEXT PRIMARY KEY, "
            "tournament_slug TEXT, "
            "name TEXT, state TEXT, municipality TEXT, category TEXT, gender TEXT, "
            "created_at TEXT)",
            "CREATE TABLE copa_telmex_players (id TEXT PRIMARY KEY, team_id TEXT, "
            "governance_state TEXT, birth_date TEXT, curp TEXT)",
            "CREATE TABLE copa_telmex_registration_review_sessions "
            "(id TEXT PRIMARY KEY, "
            "tournament_slug TEXT, committed_team_id TEXT, status TEXT)",
            "CREATE TABLE copa_telmex_registration_review_drafts (id TEXT PRIMARY KEY, "
            "session_id TEXT, draft_version INTEGER, validation TEXT, extraction TEXT)",
        )
        for sql in ddl:
            conn.execute(text(sql))
        a, b = str(uuid4()), str(uuid4())
        for project_id, name in ((a, "Copa"), (b, "Copa Similar")):
            conn.execute(
                text("INSERT INTO tournaments VALUES (:id,:name,TRUE)"),
                {"id": project_id, "name": name},
            )
        for project_id, year, slug in (
            (a, 2026, "copa-2026"),
            (a, 2025, "copa-2025"),
            (b, 2026, "copa-2026-extra"),
        ):
            conn.execute(
                text(
                    "INSERT INTO copa_telmex_tournament_editions "
                    "VALUES (:id,:project,:year,:slug,TRUE,NULL)"
                ),
                {"id": str(uuid4()), "project": project_id, "year": year, "slug": slug},
            )
        yield AsyncReadSession(conn), conn, a, b
    engine.dispose()


def add_team(conn, slug="copa-2026", state="Jalisco", name="Equipo"):
    team_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO copa_telmex_teams VALUES "
            "(:id,:slug,:name,:state,'Zapopan','Open','femenil',"
            "'2026-01-01 00:00:00')"
        ),
        {"id": team_id, "slug": slug, "state": state, "name": name},
    )
    return team_id


def add_player(conn, team_id, state):
    conn.execute(
        text(
            "INSERT INTO copa_telmex_players VALUES "
            "(:id,:team,:state,'2000-02-03','PRIVATE-CURP')"
        ),
        {"id": str(uuid4()), "team": team_id, "state": state},
    )


def add_review(conn, slug="copa-2026", committed=None):
    review_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO copa_telmex_registration_review_sessions "
            "VALUES (:id,:slug,:committed,'ready')"
        ),
        {"id": review_id, "slug": slug, "committed": committed},
    )
    for version in (1, 2):
        conn.execute(
            text(
                "INSERT INTO copa_telmex_registration_review_drafts "
                "VALUES (:id,:review,:version,:validation,:extraction)"
            ),
            {
                "id": str(uuid4()),
                "review": review_id,
                "version": version,
                "validation": json.dumps(
                    {
                        "incident_policy": {
                            "player_results": [
                                {
                                    "name": "PRIVATE PERSON",
                                    "incidents": ["SECRET"] * version,
                                }
                            ]
                        }
                    }
                ),
                "extraction": json.dumps(
                    {"team": {"state": "Jalisco"}, "manager": "PRIVATE CONTACT"}
                ),
            },
        )
    return review_id


@pytest.mark.asyncio
async def test_exact_identity_editions_and_aliases(source):
    session, _, a, b = source
    for selector, year, slug in (
        (a, 2025, "copa-2025"),
        ("Copa", 2026, "copa-2026"),
        ("copa-2026-extra", None, "copa-2026-extra"),
        (b, None, "copa-2026-extra"),
    ):
        result = await probe_registration_scope(
            session, tournament_key=selector, edition_year=year
        )
        assert result["status"] == "CONFIGURED"
        assert result["roster_slug"] == slug
    assert (await probe_registration_scope(session, tournament_key="Cop"))[
        "status"
    ] == "NOT_CONFIGURED"
    assert (await probe_registration_scope(session, tournament_key=a))[
        "status"
    ] == "SCOPE_AMBIGUOUS"
    wrong = await dispatch_registration_snapshot(
        session, tournament_id=a, edition_year=2024
    )
    assert wrong["status"] == "SCOPE_MISSING"
    assert wrong["counts"] is None


@pytest.mark.asyncio
async def test_persisted_active_provisional_pending_and_privacy(source):
    session, conn, a, _ = source
    team = add_team(conn)
    for state in ("ACTIVE", "LEGACY_ACTIVE", "PENDING_FINALITY", "UNKNOWN"):
        add_player(conn, team, state)
    add_player(conn, add_team(conn, "copa-2025"), "ACTIVE")
    add_player(conn, add_team(conn, "copa-2026-extra"), "ACTIVE")
    add_review(conn, committed=team)
    add_review(conn)
    add_review(conn, "copa-2026-extra")
    result = await dispatch_registration_snapshot(
        session, tournament_id=a, edition_year=2026, as_of_date="2026-10-05"
    )
    assert result["status"] == "AVAILABLE"
    assert result["counts"] == {
        "total_teams": 1,
        "active_players": 2,
        "provisional_players": 2,
        "pending_reviews": 1,
    }
    assert result["executive_reports"]["summary"]["jugadores"] == 2
    assert result["reviews"][0]["incident_count"] == 2
    assert result["reviews"][0]["missing_team_fields"] == [
        "municipality",
        "gender",
        "category",
    ]
    output = json.dumps(result)
    for secret in ("PRIVATE", "SECRET", "2000-02-03", "birth_date", '"curp":'):
        assert secret not in output
    assert result["source_metadata"]["eligibility"] == "not_asserted"
    assert result["groups"]["by_state"][0]["active_players"] == 2


@pytest.mark.asyncio
async def test_empty_missing_inactive_and_failure_are_distinct(source):
    session, conn, a, _ = source
    assert (
        await build_registration_snapshot(session, tournament_id=a, edition_year=2026)
    )["status"] == "EMPTY"
    assert (
        await build_registration_snapshot(
            session, tournament_id=str(uuid4()), edition_year=2026
        )
    )["status"] == "SCOPE_MISSING"
    assert (
        await dispatch_registration_snapshot(session, tournament_selector="Other")
        is None
    )
    conn.execute(
        text(
            "UPDATE copa_telmex_tournament_editions SET active=FALSE "
            "WHERE edition_year=2026"
        )
    )
    assert (
        await dispatch_registration_snapshot(
            session, tournament_id=a, edition_year=2026
        )
    )["status"] == "SCOPE_INACTIVE"
    conn.execute(text("UPDATE copa_telmex_tournament_editions SET active=TRUE"))
    conn.execute(text("UPDATE tournaments SET active=FALSE WHERE id=:id"), {"id": a})
    assert (
        await dispatch_registration_snapshot(
            session, tournament_id=a, edition_year=2026
        )
    )["status"] == "SCOPE_INACTIVE"
    conn.execute(text("UPDATE tournaments SET active=TRUE"))
    conn.execute(text("DROP TABLE copa_telmex_players"))
    failed = await dispatch_registration_snapshot(
        session, tournament_id=a, edition_year=2026
    )
    assert failed["status"] == "SOURCE_FAILED" and failed["summary"] is None


@pytest.mark.asyncio
async def test_no_migration_preserves_legacy_without_failed_transaction(source):
    session, conn, a, _ = source
    conn.execute(text("DROP TABLE copa_telmex_tournament_editions"))
    assert (
        await dispatch_registration_snapshot(
            session, tournament_id=a, edition_year=2026
        )
        is None
    )
    assert conn.execute(text("SELECT 1")).scalar() == 1


@pytest.mark.asyncio
async def test_filter_counts_missing_metadata_and_truncation(source):
    session, conn, a, _ = source
    add_team(conn, state=None)
    for _ in range(2):
        add_team(conn)
        add_review(conn)
    result = await dispatch_registration_snapshot(
        session,
        tournament_id=a,
        edition_year=2026,
        filters={"state": "Jalisco", "limit": 1},
    )
    assert result["counts"]["total_teams"] == 2
    assert len(result["teams"]) == 1
    assert result["source_metadata"]["team_list_truncated"]
    assert result["source_metadata"]["review_list_truncated"]
    result = await dispatch_registration_snapshot(
        session, tournament_id=a, edition_year=2026
    )
    assert any(row["state"] is None for row in result["groups"]["by_state"])
    for filters in ({"untrusted_sql": "x"}, {"limit": 0}, {"date_from": "broken"}):
        invalid = await dispatch_registration_snapshot(
            session, tournament_id=a, edition_year=2026, filters=filters
        )
        assert invalid["status"] == "SOURCE_FAILED"


@pytest.mark.asyncio
async def test_selector_conflicts_do_not_broaden(source):
    session, _, a, _ = source
    result = await probe_registration_scope(
        session,
        tournament_key="Copa",
        tournament_slug="copa-2026-extra",
        edition_year=2026,
    )
    assert result["status"] == "SCOPE_AMBIGUOUS"
    result = await probe_registration_scope(
        session, tournament_key=a, tournament_slug="copa-2026-extra", edition_year=2026
    )
    assert result["roster_slug"] == "copa-2026"


def test_model_and_migration_agree():
    model = CopaTelmexTournamentEdition
    assert model.__tablename__ == "copa_telmex_tournament_editions"
    assert model.roster_slug.type.length == 80
    assert not model.tournament_id.nullable
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "database/migrations/20261005_copa_telmex_tournament_editions.sql"
    ).read_text()
    assert "REFERENCES tournaments(id) ON DELETE RESTRICT" in migration
    assert "UNIQUE (tournament_id, edition_year)" in migration


@pytest.mark.asyncio
async def test_invalid_uuid_scope_and_source_error_do_not_fallback(source):
    session, _, _, _ = source
    for build in (build_registration_snapshot, dispatch_registration_snapshot):
        result = await build(session, tournament_id="Copa", edition_year=2026)
        assert result["status"] == "SCOPE_MISSING"

    class FailedSource:
        def get_bind(self):
            raise RuntimeError("PRIVATE_CONNECTION_SECRET")

    result = await dispatch_registration_snapshot(
        FailedSource(), tournament_selector="Copa", edition_year=2026
    )
    assert result["status"] == "SOURCE_FAILED"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,extra",
    [
        ("tournament_ops_query", {}),
        ("tournament_registration_breakdown", {"state": "Jalisco"}),
    ],
)
async def test_router_exact_project_name_uses_actual_configured_source(
    source, monkeypatch, tool, extra
):
    from samchat.assistant import router

    session, conn, a, _ = source
    add_player(conn, add_team(conn), "ACTIVE")
    monkeypatch.setattr(
        router,
        "get_tournament_session_maker",
        lambda *_: pytest.fail("Configured project fell back to legacy"),
    )
    result = await router._run_read_tool(
        tool,
        {"tournament_key": "Copa", "edition_year": 2026, **extra},
        gastos_session=session,
        tournament_key_default=None,
    )
    assert result["source"] == "postgres_registration"
    assert result["tournament_id"] == a
    assert result["total_equipos"] == 1
    assert result["total_jugadores"] == 1
