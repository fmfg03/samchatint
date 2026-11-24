"""Scoped CTT team roster, public representatives and observed daily cuts."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from devnous.copa_telmex.ctt_municipality_catalog import (
    load_ctt_municipality_catalog,
    resolve_ctt_geography,
)
from devnous.copa_telmex.registration_read_model import _rows, probe_registration_scope
from samchat.assistant.tournament_registration_reports import normalize_text

TEAM_SOURCE = "Padrón de Copa Telmex · PostgreSQL"
DIRECTORY_SOURCE = "Copa Telmex · Directorio oficial vigente"
HISTORY_SOURCE = "Padrón de Copa Telmex · Corte observado"
DIRECTORY_ORIGIN = "https://api.copatelmextelcel.com.mx"
DIRECTORY_ENDPOINT = DIRECTORY_ORIGIN + "/rest/v1/state_representatives"
DIRECTORY_KEY_ENV = "DIRECTION_CTT_DIRECTORY_ANON_KEY"
DIRECTORY_ORIGIN_ENV = "DIRECTION_CTT_DIRECTORY_ORIGIN"
DIRECTORY_LIMIT = 500
ROSTER_LIMIT = 25_000
CUT_TIMEZONE = "America/Mexico_City"

# Public directory slugs that differ from the full INEGI state name.
_DIRECTORY_SLUGS = {
    "05": "coahuila",
    "15": "estado-de-mexico",
    "16": "michoacan",
    "30": "veracruz",
}
_PLACEHOLDERS = frozenset(
    {"por confirmar", "por definir", "pendiente", "pendiente de confirmar"}
)
_TEAM_FIELDS = frozenset(
    {
        "id",
        "name",
        "label",
        "entity",
        "declared_entity",
        "state_code",
        "state_slug",
        "category",
        "branch",
        "gender",
        "phase",
        "player_registrations",
        "source",
        "status",
        "updated_at",
    }
)


def _section(
    source: str, status: str, rows: list[dict] | None = None, **details: Any
) -> dict:
    rows = rows or []
    updates = [row["updated_at"] for row in rows if row.get("updated_at")]
    return {
        "source": source,
        "status": status,
        "rows": rows,
        "updated_at": max(updates, default=None),
        **details,
    }


def _plain(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _iso(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _state_slug(code: str, name: str) -> str:
    return _DIRECTORY_SLUGS.get(code, normalize_text(name).replace(" ", "-"))


def _uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _scope_status(scope: dict) -> str:
    state = scope.get("status")
    if state == "SOURCE_FAILED":
        return "unavailable"
    if state == "NOT_CONFIGURED":
        return "not_configured"
    return str(state or "scope_missing").lower()


async def _ctt_scope(session: Any, tournament_id: Any, edition_year: int) -> dict:
    project_id = _uuid(tournament_id)
    if not project_id:
        return {"status": "SCOPE_MISSING"}
    scope = await probe_registration_scope(
        session, tournament_key=project_id, edition_year=edition_year
    )
    if scope["status"] == "CONFIGURED" and (
        scope.get("tournament_id") != project_id
        or scope.get("edition_year") != edition_year
        or not scope.get("roster_slug")
    ):
        return {"status": "SCOPE_MISSING"}
    return scope


def _team(row: dict) -> dict:
    declared_entity = _plain(row["state"])
    geography = resolve_ctt_geography(declared_entity, None)
    entity = geography.state_name if geography.state_code else None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "label": row["name"],
        "entity": entity,
        "declared_entity": declared_entity,
        "state_code": geography.state_code or None,
        "state_slug": _state_slug(geography.state_code, entity) if entity else None,
        "category": _plain(row["category"]),
        "branch": _plain(row["gender"]),
        "gender": _plain(row["gender"]),
        "phase": None,
        "player_registrations": row["player_registrations"],
        "source": TEAM_SOURCE,
        "status": "available",
        "updated_at": _iso(row["updated_at"]),
    }


async def _live_teams(session: Any, scope: dict) -> dict:
    try:
        rows = await _rows(
            session,
            """WITH verified AS (
                   SELECT e.roster_slug
                     FROM copa_telmex_tournament_editions e
                     JOIN tournaments project ON project.id = e.tournament_id
                    WHERE e.active AND project.active
                      AND e.tournament_id = CAST(:tournament_id AS uuid)
                      AND e.edition_year = :edition_year
                      AND e.roster_slug = :roster_slug
                ), scoped_teams AS (
                   SELECT t.id, t.name, t.state, t.category, t.gender, t.updated_at
                     FROM verified e
                     JOIN copa_telmex_teams t ON t.tournament_slug = e.roster_slug
                    ORDER BY t.id
                    LIMIT :row_limit
                )
                SELECT t.id, t.name, t.state, t.category, t.gender,
                       GREATEST(t.updated_at, players.updated_at) AS updated_at,
                       players.player_registrations
                  FROM verified
                  LEFT JOIN scoped_teams t ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT COUNT(*) AS player_registrations,
                             MAX(p.updated_at) AS updated_at
                        FROM copa_telmex_players p
                       WHERE p.team_id = t.id
                         AND p.governance_state IN ('ACTIVE', 'LEGACY_ACTIVE')
                  ) players ON t.id IS NOT NULL
                 ORDER BY t.id""",
            {**scope, "row_limit": ROSTER_LIMIT + 1},
        )
        if not rows:
            return _section(TEAM_SOURCE, "scope_missing")
        teams = [_team(row) for row in rows[:ROSTER_LIMIT] if row["id"] is not None]
    except (SQLAlchemyError, OSError, ValueError):
        return _section(TEAM_SOURCE, "unavailable")
    complete = len(rows) <= ROSTER_LIMIT
    return _section(
        TEAM_SOURCE,
        ("available" if teams else "empty") if complete else "partial",
        teams,
        complete=complete,
        team_count=len(teams) if complete else None,
        edition_verified=True,
    )


def _public_key_kind(value: str) -> str | None:
    if re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{12,200}", value):
        return "publishable"
    try:
        _, claims, signature = value.split(".")
        if not signature or len(claims) > 2048:
            return None
        payload = json.loads(
            base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4))
        )
    except (ValueError, UnicodeError):
        return None
    return (
        "anon" if isinstance(payload, dict) and payload.get("role") == "anon" else None
    )


async def _fetch_directory(*, contacts_allowed: bool) -> tuple[list, bool]:
    origin = os.environ.get(DIRECTORY_ORIGIN_ENV, DIRECTORY_ORIGIN).rstrip("/")
    key = os.environ.get(DIRECTORY_KEY_ENV, "").strip()
    kind = _public_key_kind(key)
    if origin != DIRECTORY_ORIGIN or not kind:
        raise ValueError("Official anonymous directory is not configured.")

    headers = {"apikey": key, "Accept": "application/json", "Accept-Profile": "public"}
    if kind == "anon":
        headers["Authorization"] = f"Bearer {key}"
    fields = "id,state_slug,zone,name,is_active,updated_at"
    if contacts_allowed:
        fields += ",email,phone"

    # Ask for bounded pages and honor a lower server-side limit. A terminal
    # empty response proves that the requested offset is past the last row.
    raw: list = []
    async with asyncio.timeout(12), httpx.AsyncClient(
        timeout=6, follow_redirects=False, trust_env=False
    ) as client:
        while len(raw) <= DIRECTORY_LIMIT:
            size = min(100, DIRECTORY_LIMIT + 1 - len(raw))
            result = await client.get(
                DIRECTORY_ENDPOINT,
                params={
                    "select": fields,
                    "is_active": "eq.true",
                    "order": "state_slug.asc,zone.asc,id.asc",
                    "offset": len(raw),
                    "limit": size,
                },
                headers=headers,
            )
            result.raise_for_status()
            page = result.json()
            if not isinstance(page, list) or len(page) > size:
                raise ValueError("Invalid official directory page.")
            if not page:
                return raw, True
            raw.extend(page)
    return raw[:DIRECTORY_LIMIT], False


async def _directory(*, contacts_allowed: bool) -> dict:
    try:
        catalog = load_ctt_municipality_catalog()
        raw, complete = await _fetch_directory(contacts_allowed=contacts_allowed)
    except (OSError, ValueError, httpx.HTTPError):
        return _section(DIRECTORY_SOURCE, "unavailable")
    states = {
        _state_slug(code, name): (code, name) for code, name in catalog.states.items()
    }
    grouped: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict) or row.get("is_active") is not True:
            complete = False
            continue
        slug = _plain(row.get("state_slug"))
        identifier = _uuid(row.get("id"))
        if slug not in states or not identifier or identifier in seen:
            complete = False
            continue
        seen.add(identifier)
        grouped[slug].append(row)

    rows = []
    known_entities = 0
    for slug, (code, name) in states.items():
        contacts = grouped[slug]
        if contacts:
            known_entities += 1
        for contact in contacts or [{}]:
            public_name = _plain(contact.get("name"))
            confirmed = bool(
                public_name and normalize_text(public_name) not in _PLACEHOLDERS
            )
            state = (
                "confirmed"
                if confirmed
                else "pending" if contacts or complete else "partial"
            )
            entry = {
                "position": "Representante estatal",
                "entity": name,
                "state_code": code,
                "state_slug": slug,
                "zone": _plain(contact.get("zone")),
                "representation_status": state,
                "status": state,
                "source": DIRECTORY_SOURCE,
                "updated_at": _iso(contact.get("updated_at")),
            }
            if contacts_allowed and contacts:
                entry.update(
                    representative_id=_uuid(contact.get("id")),
                    representative_name=public_name,
                    email=_plain(contact.get("email")),
                    phone=_plain(contact.get("phone")),
                )
            rows.append(entry)
    return _section(
        DIRECTORY_SOURCE,
        "available" if complete else "partial",
        rows,
        observed_entities=known_entities,
        catalog_entities=len(states),
        complete=complete,
        temporal_scope="current",
    )


async def _historical_teams(session: Any, scope: dict, cut_date: date) -> dict:
    try:
        cuts = await _rows(
            session,
            """SELECT cut.observed_at, cut.team_count, cut.team_roster
                 FROM direction_dossier_roster_daily_cuts cut
                 JOIN copa_telmex_tournament_editions e ON e.id = cut.edition_id
                 JOIN tournaments project ON project.id = e.tournament_id
                WHERE e.active AND project.active
                  AND e.tournament_id = CAST(:tournament_id AS uuid)
                  AND e.edition_year = :edition_year AND e.roster_slug = :roster_slug
                  AND cut.cut_date = :cut_date""",
            {**scope, "cut_date": cut_date},
        )
    except SQLAlchemyError:
        return _section(HISTORY_SOURCE, "unavailable")
    if not cuts:
        return _section(HISTORY_SOURCE, "not_recorded")
    cut = cuts[0]
    payload = cut["team_roster"]
    if (
        not isinstance(payload, list)
        or len(payload) != cut["team_count"]
        or not all(isinstance(row, dict) for row in payload)
    ):
        return _section(HISTORY_SOURCE, "unavailable")
    return _section(
        HISTORY_SOURCE,
        "available" if payload else "empty",
        [
            {key: value for key, value in row.items() if key in _TEAM_FIELDS}
            for row in payload
        ],
        edition_verified=True,
        complete=True,
        team_count=cut["team_count"],
        observed_at=_iso(cut["observed_at"]),
    )


async def _directory_applies(session: Any, tournament_id: Any, scope: dict) -> bool:
    if scope["status"] == "CONFIGURED":
        return bool(scope["roster_slug"] == f"copa-telmex-{scope['edition_year']}")
    project_id = _uuid(tournament_id)
    if not project_id or scope["status"] not in {"SCOPE_MISSING", "SCOPE_INACTIVE"}:
        return False
    try:
        rows = await _rows(
            session,
            """SELECT 1
                 FROM copa_telmex_tournament_editions e
                 JOIN tournaments project ON project.id = e.tournament_id
                WHERE e.active AND project.active
                  AND e.tournament_id = CAST(:tournament_id AS uuid)
                  AND e.roster_slug = CONCAT('copa-telmex-', e.edition_year)
                LIMIT 1""",
            {"tournament_id": project_id},
        )
    except SQLAlchemyError:
        return False
    return bool(rows)


async def load_dossier_registry(
    session: Any,
    *,
    tournament: dict,
    edition_year: int,
    operations_allowed: bool,
    contacts_allowed: bool = False,
    cut_date: date | None = None,
) -> dict:
    """Read facts only from the authorized CTT project and explicit edition."""
    roster_source = HISTORY_SOURCE if cut_date else TEAM_SOURCE
    roster = _section(roster_source, "denied")
    directory = _section(DIRECTORY_SOURCE, "denied")
    if operations_allowed:
        scope = await _ctt_scope(session, tournament.get("id"), edition_year)
        if scope["status"] != "CONFIGURED":
            status = _scope_status(scope)
            roster = _section(roster_source, status)
            directory = _section(DIRECTORY_SOURCE, status)
        elif cut_date is not None:
            roster = await _historical_teams(session, scope, cut_date)
            directory = _section(DIRECTORY_SOURCE, "not_recorded")
        else:
            roster = await _live_teams(session, scope)
            directory = _section(DIRECTORY_SOURCE, "not_configured")
        if cut_date is None and await _directory_applies(
            session, tournament.get("id"), scope
        ):
            directory = await _directory(contacts_allowed=contacts_allowed)
    return {
        "team_roster": roster,
        "directory": directory,
        "entities": sorted(
            {
                item["entity"]
                for item in roster["rows"] + directory["rows"]
                if item.get("entity")
            }
        ),
        "categories": sorted(
            {item["category"] for item in roster["rows"] if item.get("category")}
        ),
        "cut_date": cut_date.isoformat() if cut_date else None,
    }


async def _current_cut(session: Any, scope: dict) -> dict | None:
    rows = await _rows(
        session,
        """SELECT cut.cut_date, cut.observed_at, cut.team_count
             FROM direction_dossier_roster_daily_cuts cut
             JOIN copa_telmex_tournament_editions e ON e.id = cut.edition_id
             JOIN tournaments project ON project.id = e.tournament_id
            WHERE e.active AND project.active
              AND e.tournament_id = CAST(:tournament_id AS uuid)
              AND e.edition_year = :edition_year AND e.roster_slug = :roster_slug
              AND cut.cut_date = (
                  transaction_timestamp() AT TIME ZONE :cut_timezone
              )::date""",
        {**scope, "cut_timezone": CUT_TIMEZONE},
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "status": "already_recorded",
        "cut_date": _iso(row["cut_date"]),
        "observed_at": _iso(row["observed_at"]),
        "team_count": row["team_count"],
    }


async def record_observed_roster_cut(
    session: Any, *, tournament_id: str, edition_year: int, apply: bool = False
) -> dict:
    """Prepare or append the first complete observed roster of the current day."""
    scope = await _ctt_scope(session, tournament_id, edition_year)
    if scope["status"] != "CONFIGURED":
        return {"status": _scope_status(scope), "edition_year": edition_year}
    identity = {"tournament_id": tournament_id, "edition_year": edition_year}
    if apply and (prior := await _current_cut(session, scope)):
        return {**identity, **prior}
    roster = await _live_teams(session, scope)
    if not roster.get("complete"):
        return {**identity, "status": roster["status"]}
    result = {**identity, "status": "ready", "team_count": roster["team_count"]}
    if not apply:
        return result
    # Clock and date are supplied by PostgreSQL, never by the caller. The
    # immutable unique key preserves the first successful observation per day.
    write = await session.execute(
        text("""
            INSERT INTO direction_dossier_roster_daily_cuts
                (edition_id, cut_date, observed_at, team_count, team_roster)
            SELECT e.id, (transaction_timestamp() AT TIME ZONE :cut_timezone)::date,
                   transaction_timestamp(), :team_count, CAST(:team_roster AS jsonb)
              FROM copa_telmex_tournament_editions e
              JOIN tournaments project ON project.id = e.tournament_id
             WHERE e.active AND project.active
               AND e.tournament_id = CAST(:tournament_id AS uuid)
               AND e.edition_year = :edition_year AND e.roster_slug = :roster_slug
            ON CONFLICT (edition_id, cut_date) DO NOTHING
            RETURNING cut_date, observed_at, team_count
        """),
        {
            **scope,
            "cut_timezone": CUT_TIMEZONE,
            "team_count": roster["team_count"],
            "team_roster": json.dumps(roster["rows"], ensure_ascii=False),
        },
    )
    saved = write.mappings().one_or_none()
    if saved:
        return {
            **result,
            "status": "recorded",
            "cut_date": _iso(saved["cut_date"]),
            "observed_at": _iso(saved["observed_at"]),
        }
    # Re-read the exact stored observation for a retry. A scope that changed
    # since the read must not be reported as a successful image of that day.
    prior = await _current_cut(session, scope)
    return {**identity, **prior} if prior else {**identity, "status": "scope_missing"}
