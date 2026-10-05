"""SELECT-only, explicitly scoped operational registration projections."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text

SOURCE = "postgres_registration"
ACTIVE_STATES = frozenset({"ACTIVE", "LEGACY_ACTIVE"})
FILTER_COLUMNS = {
    "state": "state",
    "municipality": "municipality",
    "category": "category",
    "gender": "gender",
    "team_name": "name",
}


async def _rows(session: Any, sql: str, params: dict | None = None) -> list[dict]:
    if session.get_bind().dialect.name == "postgresql":
        # Connection savepoints protect the caller's transaction without invoking
        # Session.begin_nested(), which flushes pending ORM writes even with
        # autoflush disabled. This read must never confer persistence authority.
        connection = await session.connection()
        async with connection.begin_nested():
            result = await connection.execute(text(sql), params or {})
            return [dict(row) for row in result.mappings().all()]
    result = await session.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings().all()]


def _unavailable(status: str, **scope: Any) -> dict:
    return {
        **scope,
        "status": status,
        "source_status": status,
        "source": SOURCE,
        "available": False,
        "summary": None,
        "counts": None,
        "teams": [],
        "groups": {},
        "breakdowns": {},
        "reviews": [],
        "next_action": (
            "Verificar la fuente PostgreSQL y su migración."
            if status == "SOURCE_FAILED"
            else "Configurar una asociación activa y exacta de torneo y edición."
        ),
    }


async def probe_registration_scope(
    session: Any,
    *,
    tournament_key: str,
    tournament_slug: str | None = None,
    edition_year: int | None = None,
) -> dict:
    """Distinguish legacy scope from configured-but-unavailable scope.

    UUID, project name, and roster slug are exact selectors. An edition is never
    derived from a name, creation timestamp, or an age calculation cutoff.
    """
    try:
        # Existence is checked before accessing the optional migration's table:
        # a missing relation must not leave a shared PostgreSQL transaction aborted.
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            exists = await _rows(
                session,
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='copa_telmex_tournament_editions'",
            )
        else:
            exists = await _rows(
                session,
                "SELECT to_regclass('copa_telmex_tournament_editions') AS name",
            )
        if not exists or not exists[0].get("name"):
            return {"status": "NOT_CONFIGURED"}
        try:
            project_id = str(UUID(str(tournament_key)))
        except (ValueError, TypeError, AttributeError):
            project_id = None
        selector_clause = (
            "CAST(e.tournament_id AS VARCHAR) = :project_id"
            if project_id
            else "(t.name = :selector OR e.roster_slug = :selector "
            "OR e.roster_slug = :slug)"
        )
        rows = await _rows(
            session,
            """SELECT CAST(e.tournament_id AS VARCHAR) AS tournament_id,
                      e.edition_year, e.roster_slug, e.active,
                      t.active AS project_active, t.name AS tournament_name
               FROM copa_telmex_tournament_editions e
               JOIN tournaments t ON t.id = e.tournament_id
               WHERE """ + selector_clause,
            {
                "project_id": project_id,
                "selector": str(tournament_key),
                "slug": tournament_slug,
            },
        )
        if not rows:
            return {"status": "NOT_CONFIGURED"}
        # A competing exact alias cannot broaden a project-name selector.
        projects = {row["tournament_id"] for row in rows}
        if len(projects) != 1:
            return _unavailable("SCOPE_AMBIGUOUS")
        if edition_year is not None:
            selected = [row for row in rows if row["edition_year"] == edition_year]
        else:
            slug_matches = [
                row
                for row in rows
                if row["roster_slug"] in {str(tournament_key), tournament_slug}
            ]
            selected = slug_matches or rows
        if not selected:
            return _unavailable(
                "SCOPE_MISSING",
                tournament_id=rows[0]["tournament_id"],
                edition_year=edition_year,
            )
        if len(selected) != 1:
            return _unavailable("SCOPE_AMBIGUOUS")
        scope = selected[0]
        if not scope["active"] or not scope.pop("project_active"):
            return _unavailable("SCOPE_INACTIVE", **scope)
        return {**scope, "status": "CONFIGURED"}
    except Exception:
        # Never include driver errors, connection strings, or source PII.
        return _unavailable("SOURCE_FAILED")


def _json_dict(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _review_summary(row: dict) -> dict:
    validation = _json_dict(row.pop("validation"))
    extraction = _json_dict(row.pop("extraction"))
    policy = validation.get("incident_policy")
    policy = policy if isinstance(policy, dict) else {}
    incident_count = sum(
        len(player.get("incidents") or [])
        for player in policy.get("player_results", [])
        if isinstance(player, dict)
    )
    team = extraction.get("team")
    team = team if isinstance(team, dict) else {}
    return {
        **row,
        "incident_count": incident_count if policy else None,
        "missing_team_fields": (
            [
                name
                for name in ("state", "municipality", "gender", "category")
                if not team.get(name)
            ]
            if extraction
            else None
        ),
    }


def _reports(
    scope: dict, teams: list[dict], players: list[dict], as_of: str | None
) -> dict:
    # Existing report builder returns aggregates only. The private player dataset
    # (individual birth dates/CURPs) never enters the returned snapshot.
    from samchat.assistant.tournament_registration_reports import (
        build_registration_executive_reports,
    )

    return cast(
        dict,
        build_registration_executive_reports(
            dataset={
                "tournaments": [
                    {
                        "id": scope["tournament_id"],
                        "name": scope["tournament_name"],
                        "slug": scope["roster_slug"],
                    }
                ],
                "teams": teams,
                "categories": [
                    {"id": team["id"], "name": team["category"]} for team in teams
                ],
                "registrations": [
                    {"id": team["id"], "team_id": team["id"], "category_id": team["id"]}
                    for team in teams
                ],
                "players": [
                    {
                        "registration_id": row["team_id"],
                        "birth_date": row["birth_date"],
                        "curp": row["curp"],
                    }
                    for row in players
                    if row["governance_state"] in ACTIVE_STATES
                ],
            },
            tournament_key=scope["tournament_id"],
            tournament_slug=scope["roster_slug"],
            as_of_date=as_of,
            source=SOURCE,
        ),
    )


async def build_registration_snapshot(
    session: Any,
    *,
    tournament_id: str,
    edition_year: int | None = None,
    filters: dict | None = None,
    as_of_date: str | None = None,
) -> dict:
    """Read one exact edition without granting registration or eligibility."""
    try:
        tournament_id = str(UUID(str(tournament_id)))
    except (ValueError, TypeError, AttributeError):
        return _unavailable("SCOPE_MISSING", edition_year=edition_year)
    scope = await probe_registration_scope(
        session, tournament_key=tournament_id, edition_year=edition_year
    )
    if scope["status"] != "CONFIGURED":
        return (
            _unavailable(
                "SCOPE_MISSING", tournament_id=tournament_id, edition_year=edition_year
            )
            if scope["status"] == "NOT_CONFIGURED"
            else scope
        )
    return await _build_scoped_snapshot(session, scope, filters or {}, as_of_date)


async def _build_scoped_snapshot(
    session: Any, scope: dict, filters: dict, as_of_date: str | None
) -> dict:
    try:
        unknown = set(filters) - set(FILTER_COLUMNS) - {"date_from", "date_to", "limit"}
        if unknown:
            raise ValueError("Unsupported filters")
        limit = int(filters.get("limit", 50))
        if not 1 <= limit <= 500:
            raise ValueError("Invalid limit")
        clauses = ["t.tournament_slug = :roster_slug"]
        params = {"roster_slug": scope["roster_slug"]}
        for name, column in FILTER_COLUMNS.items():
            if filters.get(name) is not None:
                clauses.append(f"t.{column} = :{name}")
                params[name] = filters[name]
        for name, operator in (("date_from", ">="), ("date_to", "<=")):
            if filters.get(name):
                parsed = datetime.fromisoformat(str(filters[name]))
                clauses.append(f"t.created_at {operator} :{name}")
                params[name] = parsed
        where = " AND ".join(clauses)
        teams = await _rows(
            session,
            "SELECT CAST(t.id AS VARCHAR) AS id, t.name, t.state, t.municipality, "
            "t.category, t.gender FROM copa_telmex_teams t "
            f"WHERE {where} ORDER BY t.id",
            params,
        )
        players = await _rows(
            session,
            "SELECT CAST(p.team_id AS VARCHAR) AS team_id, p.governance_state, "
            "p.birth_date, p.curp FROM copa_telmex_players p "
            f"JOIN copa_telmex_teams t ON t.id = p.team_id WHERE {where}",
            params,
        )
        # Pending review scope is the entire edition. Team filters cannot safely
        # classify an uncommitted session using inferred/extracted metadata.
        reviews = await _rows(
            session,
            """SELECT CAST(s.id AS VARCHAR) AS id, s.status,
                      d.validation, d.extraction
               FROM copa_telmex_registration_review_sessions s
               LEFT JOIN copa_telmex_registration_review_drafts d
                 ON d.session_id = s.id AND d.draft_version = (
                   SELECT MAX(latest.draft_version)
                   FROM copa_telmex_registration_review_drafts latest
                   WHERE latest.session_id = s.id)
               WHERE s.tournament_slug = :roster_slug
                 AND s.committed_team_id IS NULL AND s.status != 'committed'
               ORDER BY s.id""",
            {"roster_slug": scope["roster_slug"]},
        )
        counts_by_team: dict = defaultdict(
            lambda: {"active_players": 0, "provisional_players": 0}
        )
        for player in players:
            key = (
                "active_players"
                if player["governance_state"] in ACTIVE_STATES
                else "provisional_players"
            )
            counts_by_team[player["team_id"]][key] += 1
        for team in teams:
            team.update(counts_by_team[team["id"]])
        counts = {
            "total_teams": len(teams),
            "active_players": sum(t["active_players"] for t in teams),
            "provisional_players": sum(t["provisional_players"] for t in teams),
            "pending_reviews": len(reviews),
        }
        groups = {}
        for field in ("state", "municipality", "category", "gender"):
            bucket: dict = defaultdict(
                lambda: {"teams": 0, "active_players": 0, "provisional_players": 0}
            )
            for team in teams:
                entry = bucket[team[field]]
                entry["teams"] += 1
                entry["active_players"] += team["active_players"]
                entry["provisional_players"] += team["provisional_players"]
            groups[f"by_{field}"] = [
                {field: value, **aggregate} for value, aggregate in bucket.items()
            ]
        reports = _reports(scope, teams, players, as_of_date)
        status = "AVAILABLE" if teams or reviews else "EMPTY"
        return {
            **scope,
            "status": status,
            "source_status": status,
            "available": True,
            "source": SOURCE,
            "summary": counts,
            "counts": counts,
            "groups": groups,
            "breakdowns": groups,
            "teams": teams[:limit],
            "reviews": [_review_summary(row) for row in reviews[:limit]],
            "executive_reports": reports,
            "reports": reports,
            "filters": filters,
            "next_action": None,
            "source_metadata": {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "tournament_id": scope["tournament_id"],
                "edition_year": scope["edition_year"],
                "roster_slug": scope["roster_slug"],
                "pending_review_scope": "entire_edition",
                "team_list_truncated": len(teams) > limit,
                "review_list_truncated": len(reviews) > limit,
                "coverage": "persisted_teams_and_review_sessions",
                "eligibility": "not_asserted",
            },
        }
    except Exception:
        return _unavailable(
            "SOURCE_FAILED",
            tournament_id=scope["tournament_id"],
            edition_year=scope["edition_year"],
        )


async def dispatch_registration_snapshot(
    session: Any,
    *,
    tournament_id: str | None = None,
    tournament_selector: str | None = None,
    edition_year: int | None = None,
    filters: dict | None = None,
    as_of_date: str | None = None,
    tournament_slug: str | None = None,
) -> dict | None:
    """Return None only for a proven unconfigured legacy selector."""
    if tournament_id is not None:
        try:
            tournament_id = str(UUID(str(tournament_id)))
        except (ValueError, TypeError, AttributeError):
            return _unavailable("SCOPE_MISSING", edition_year=edition_year)
    scope = await probe_registration_scope(
        session,
        tournament_key=tournament_id or tournament_selector or tournament_slug or "",
        tournament_slug=None if tournament_id else tournament_slug,
        edition_year=edition_year,
    )
    if scope["status"] == "NOT_CONFIGURED":
        return None
    if scope["status"] != "CONFIGURED":
        return scope
    return await _build_scoped_snapshot(session, scope, filters or {}, as_of_date)
