"""Bounded canonical reads for an already authorized tournament edition."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError

from devnous.copa_telmex.registration_read_model import (
    _rows,
    probe_registration_scope,
)
from devnous.gastos.services.document_amount_service import (
    EMPLOYEE_REIMBURSEMENT_PREFIX,
)
from devnous.gastos.services.payment_run_service import _PAYMENT_RUN_SOURCE_SQL
from samchat.assistant.tournament_registration_reports import normalize_text, parse_date
from samchat.tournaments_v2.adapters.queries import _in_filter
from samchat.tournaments_v2.config import load_tournaments_v2_config
from samchat.tournaments_v2.services import build_tournament_soul_snapshot
from samchat.tournaments_v2.supabase_client import (
    SupabaseRestClient,
    TournamentsV2Error,
)

REGISTRATION = "PostgreSQL · Padrón operativo de Copa Telmex"
OPERATIONS = "SOUL · Operaciones de torneos"
FINANCE = "Gastos · Documentos y Payment Run"
MARKETING = "SOUL · Galería de torneos"
FINANCE_LIMIT = 250
MATCH_LIMIT = 250
PHOTO_LIMIT = 50
PHOTO_MAX_BYTES = 8 * 1024 * 1024
PHOTO_MAX_PIXELS = 24_000_000
PHOTO_BUCKETS = frozenset({"gallery-media", "gallery-photos"})

# No participant identifiers are returned by this query. CURP is only used
# within the authorized aggregate, after matching the canonical identity check.
_ROSTER = """
    SELECT t.id AS team_id, NULLIF(BTRIM(t.state), '') AS entity,
           NULLIF(BTRIM(t.category), '') AS category,
           NULLIF(BTRIM(t.gender), '') AS gender,
           t.updated_at AS team_updated_at, p.id AS player_id,
           p.updated_at AS player_updated_at, p.birth_date,
           CASE WHEN p.curp_valid IS TRUE
                AND UPPER(BTRIM(p.curp)) ~ '^[A-Z]{4}[0-9]{6}[HM][A-Z]{5}[A-Z0-9][0-9]$'
                THEN UPPER(BTRIM(p.curp)) END AS identity_key
      FROM copa_telmex_tournament_editions e
      JOIN tournaments project ON project.id = e.tournament_id AND project.active
      JOIN copa_telmex_teams t ON t.tournament_slug = e.roster_slug
      LEFT JOIN copa_telmex_players p ON p.team_id = t.id
                                  AND p.governance_state IN ('ACTIVE', 'LEGACY_ACTIVE')
     WHERE e.active
       AND e.tournament_id = CAST(:tournament_id AS uuid)
       AND e.edition_year = :edition_year
       AND e.roster_slug = :roster_slug
"""


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _section(
    source: str, status: str, rows: list[dict] | None = None, **details: Any
) -> dict:
    rows = rows if rows is not None else []
    dates = [row["updated_at"] for row in rows if row.get("updated_at")]
    return {
        "source": source,
        "status": status,
        "updated_at": max(dates, default=None),
        "rows": rows,
        **details,
    }


async def _age_cutoff(session: Any, scope: dict) -> date | None:
    # PR #537 owns the canonical cutoff per edition. Its report rollout
    # switch is separate from Direction's scoped aggregate access.
    try:
        rows = await _rows(
            session,
            """SELECT s.age_cutoff
                 FROM copa_telmex_report_settings s
                 JOIN copa_telmex_tournament_editions e ON e.id = s.edition_id
                WHERE e.tournament_id = CAST(:tournament_id AS uuid)
                  AND e.edition_year = :edition_year
                  AND e.roster_slug = :roster_slug AND e.active""",
            scope,
        )
    except SQLAlchemyError:
        return None
    return parse_date(rows[0]["age_cutoff"]) if rows else None


def _registration_row(row: dict) -> dict:
    registrations = row["registrations"]
    missing_identity = row["registrations_without_verified_identity"]
    return {
        "label": "Padrón de la edición",
        "source": REGISTRATION,
        "status": "available",
        "entity": row["entity"],
        "category": row["category"],
        "gender": row["gender"],
        "phase": None,
        "teams": row["teams"],
        "registrations": registrations,
        "unique_people_identified": (
            row["identified"]
            if registrations == 0 or missing_identity < registrations
            else None
        ),
        "registrations_without_verified_identity": missing_identity,
        "updated_at": _iso(row["updated_at"]),
    }


async def _registration(session: Any, scope: dict) -> dict:
    if scope["status"] != "CONFIGURED":
        return _section(
            REGISTRATION,
            "unavailable" if scope["status"] == "SOURCE_FAILED" else "needs_link",
        )

    cutoff = await _age_cutoff(session, scope)
    try:
        grouped = await _rows(
            session,
            f"""WITH roster AS ({_ROSTER})
                SELECT entity, category, gender,
                       GROUPING(entity, category, gender) AS scope_level,
                       COUNT(DISTINCT team_id) AS teams,
                       COUNT(player_id) AS registrations,
                       COUNT(DISTINCT identity_key) AS identified,
                       COUNT(player_id) FILTER (WHERE identity_key IS NULL)
                           AS registrations_without_verified_identity,
                       GREATEST(MAX(team_updated_at), MAX(player_updated_at))
                           AS updated_at
                  FROM roster
                 GROUP BY GROUPING SETS ((entity, category, gender), ())
                 ORDER BY scope_level, entity, category, gender""",
            scope,
        )
        ages = (
            await _rows(
                session,
                f"""WITH roster AS ({_ROSTER})
                    SELECT entity, category, gender,
                           CASE WHEN birth_date <= :age_cutoff
                            THEN EXTRACT(YEAR FROM AGE(
                                CAST(:age_cutoff AS date), birth_date
                            ))::int
                           END AS age,
                           COUNT(player_id) AS registrations
                      FROM roster
                     WHERE player_id IS NOT NULL
                     GROUP BY entity, category, gender, age
                     ORDER BY entity, category, gender, age""",
                {**scope, "age_cutoff": cutoff},
            )
            if cutoff is not None
            else []
        )
    except SQLAlchemyError:
        return _section(REGISTRATION, "unavailable")

    age_bins: dict[tuple, list[dict]] = defaultdict(list)
    for age in ages:
        dimensions = (age["entity"], age["category"], age["gender"])
        age_bins[dimensions].append(
            {"age": age["age"], "registrations": age["registrations"]}
        )
    rows = []
    summary = None
    for group in grouped:
        row = _registration_row(group)
        if group["scope_level"]:
            summary = row
            continue
        dimensions = (row["entity"], row["category"], row["gender"])
        row["ages"] = age_bins[dimensions] if cutoff is not None else None
        rows.append(row)
    return _section(
        REGISTRATION,
        "available" if rows else "empty",
        rows,
        summary=summary,
        age_cutoff=_iso(cutoff),
        gender_basis="División declarada del equipo",
        phase_available=False,
        edition_verified=True,
    )


def _phase(raw: Any) -> str | None:
    label = normalize_text(raw)
    for phase, terms in (
        (
            "nacional",
            (
                "nacional",
                "national",
                "fase nacional",
                "etapa nacional",
                "final nacional",
            ),
        ),
        (
            "estatal",
            ("estatal", "state", "fase estatal", "etapa estatal", "final estatal"),
        ),
    ):
        if label in terms or any(
            label.startswith(term + separator)
            for term in terms
            for separator in (" - ", ": ", " / ")
        ):
            return phase
    return None


async def _soul_edition_metadata(
    client: SupabaseRestClient,
    source_id: str | None,
    *,
    roster_slug: str | None = None,
) -> dict | None:
    if not source_id and not roster_slug:
        return None
    selector = {"id": f"eq.{source_id}"} if source_id else {"slug": f"eq.{roster_slug}"}
    for edition_columns in (
        ("edition_year", "edition"),
        ("edition_year",),
        ("edition",),
        (),
    ):
        fields = ",".join(("id", "slug", *edition_columns))
        try:
            rows = await client.fetch_all_rows(
                table="tournaments",
                select_expr=fields,
                filters=selector,
                order="id.asc",
                max_rows=2,
            )
        except TournamentsV2Error as exc:
            detail = str(exc)
            if "42703" not in detail or not any(
                column in detail for column in edition_columns
            ):
                raise
            continue
        if len(rows) != 1 or not _uuid(rows[0].get("id")):
            return None
        return (
            rows[0]
            if (
                _uuid(rows[0].get("id")) == source_id
                if source_id
                else rows[0].get("slug") == roster_slug
            )
            else None
        )
    return None


async def _soul_snapshot(
    session: Any,
    tournament: dict,
    edition_year: int,
    *,
    include_registration: bool,
    operations_edition_id: str | None = None,
    roster_slug: str | None = None,
) -> tuple[dict | None, str]:
    try:
        mapped = await _rows(
            session,
            """SELECT operations_tournament_id
                 FROM tournament_operations_links
                WHERE tournament_id = CAST(:tournament_id AS uuid)""",
            {"tournament_id": tournament["id"]},
        )
    except SQLAlchemyError:
        mapped = []

    project_id = _uuid(tournament["id"])
    mapped_id = (
        _uuid(mapped[0]["operations_tournament_id"]) if len(mapped) == 1 else None
    )
    attested_id = _uuid(operations_edition_id)
    if operations_edition_id is not None and (
        attested_id is None or attested_id not in {project_id, mapped_id}
    ):
        return None, "needs_link"
    source_id = attested_id or mapped_id or project_id
    if source_id is None or len(mapped) > 1:
        return None, "needs_link"
    try:
        client = SupabaseRestClient(load_tournaments_v2_config())
        metadata = None
        if roster_slug and not attested_id:
            metadata = await _soul_edition_metadata(
                client, None, roster_slug=roster_slug
            )
            if metadata and metadata.get("slug") != roster_slug:
                metadata = None
        slug_verified = metadata is not None
        if metadata is not None:
            source_id = _uuid(metadata["id"])
        else:
            metadata = await _soul_edition_metadata(client, source_id)
        if not metadata or not source_id:
            return None, "needs_link"
        declared = {
            str(value)
            for key in ("edition", "edition_year")
            if (value := metadata.get(key)) is not None and str(value).strip()
        }
        if (declared and declared != {str(edition_year)}) or (
            not declared and not attested_id and not slug_verified
        ):
            return None, "needs_link"
        snapshot = await build_tournament_soul_snapshot(
            tournament_key="all",
            tournament_slug=source_id,
            include_communications=False,
            include_media=False,
            include_registration=include_registration,
            limit=MATCH_LIMIT,
            client=client,
        )
    except (TournamentsV2Error, ValueError):
        return None, "unavailable"
    if _source_tournament_id(snapshot) != source_id:
        return None, "needs_link"
    if slug_verified:
        if snapshot["tournaments"][0].get("slug") != roster_slug:
            return None, "needs_link"
    return snapshot, "available"


def _source_tournament_id(snapshot: dict) -> str | None:
    tournaments = snapshot.get("tournaments") or []
    return _uuid(tournaments[0].get("id")) if len(tournaments) == 1 else None


async def _category_catalog(
    client: SupabaseRestClient, source_id: str
) -> tuple[dict[str, str | None], bool]:
    categories = await client.fetch_all_rows(
        table="categories",
        select_expr="id,name,tournament_id",
        filters={"tournament_id": f"eq.{source_id}"},
        order="id.asc",
        max_rows=1000,
    )
    return {
        key: row.get("name")
        for row in categories
        if _uuid(row.get("tournament_id")) == source_id
        and (key := _uuid(row.get("id"))) is not None
    }, len(categories) == 1000


async def _sport_relationships(
    snapshot: dict,
) -> tuple[dict[str, str | None], dict[str, tuple[str | None, str | None]], dict]:
    """Resolve participants by foreign key inside the verified SOUL edition."""
    source_id = _source_tournament_id(snapshot)
    categories: dict[str, str | None] = {}
    match_teams: dict[str, tuple[str | None, str | None]] = {}
    teams: dict[str, dict] = {}
    if source_id is None:
        return categories, match_teams, teams

    operations = snapshot.get("operations") or {}
    unresolved = []
    for match in operations.get("matches") or []:
        key = _uuid(match.get("match_id"))
        if not key:
            continue
        home = _uuid(match.get("home_team_id"))
        away = _uuid(match.get("away_team_id"))
        if home or away:
            match_teams[key] = (home, away)
        else:
            unresolved.append(key)

    try:
        client = SupabaseRestClient(load_tournaments_v2_config())
        categories, _ = await _category_catalog(client, source_id)
        for offset in range(0, len(unresolved), 100):
            end = offset + 100
            requested = set(unresolved[offset:end])
            rows = await client.fetch_all_rows(
                table="matches",
                select_expr="id,category_id,home_team_id,away_team_id",
                filters={"id": _in_filter(sorted(requested))},
                order="id.asc",
                max_rows=len(requested),
            )
            for row in rows:
                key = _uuid(row.get("id"))
                if (
                    key is not None
                    and key in requested
                    and (_uuid(row.get("category_id")) in categories)
                ):
                    match_teams[key] = (
                        _uuid(row.get("home_team_id")),
                        _uuid(row.get("away_team_id")),
                    )
        team_ids = sorted(
            {key for pair in match_teams.values() for key in pair if key is not None}
            | {
                key
                for row in operations.get("standings") or []
                if (key := _uuid(row.get("team_id"))) is not None
            }
        )
        for offset in range(0, len(team_ids), 100):
            end = offset + 100
            requested = set(team_ids[offset:end])
            rows = await client.fetch_all_rows(
                table="teams",
                select_expr="id,team_name,state,tournament_id",
                filters={
                    "id": _in_filter(sorted(requested)),
                    "tournament_id": f"eq.{source_id}",
                },
                order="id.asc",
                max_rows=len(requested),
            )
            teams.update(
                {
                    key: row
                    for row in rows
                    if _uuid(row.get("tournament_id")) == source_id
                    and (key := _uuid(row.get("id"))) is not None
                    and key in requested
                }
            )
    except (TournamentsV2Error, ValueError):
        # Incomplete enrichment must not suppress the verified match results.
        pass
    return categories, match_teams, teams


def _sport_rows(
    snapshot: dict,
    match_teams: dict,
    teams: dict,
) -> list[dict]:
    matches = (snapshot.get("operations") or {}).get("matches") or []
    rows = []
    for match in matches:
        phase = _phase(match.get("phase"))
        score = match.get("score") or {}
        home_id, away_id = match_teams.get(_uuid(match.get("match_id")), (None, None))
        home = teams.get(home_id, {})
        away = teams.get(away_id, {})
        home_entity, away_entity = home.get("state"), away.get("state")
        entities = sorted({name for name in (home_entity, away_entity) if name})
        rows.append(
            {
                "id": match.get("match_id"),
                "label": "Partido",
                "source": OPERATIONS,
                "phase": phase,
                "phase_label": match.get("phase"),
                "category": match.get("category"),
                "status": "available" if phase is not None else "needs_link",
                "match_status": match.get("status"),
                "date": match.get("match_date"),
                "home_team": home.get("team_name") or match.get("home_team"),
                "away_team": away.get("team_name") or match.get("away_team"),
                "home_entity": home_entity,
                "away_entity": away_entity,
                "entities": entities,
                "home_score": score.get("home"),
                "away_score": score.get("away"),
                "field_number": match.get("field_number"),
                "updated_at": None,
            }
        )
    # This source has category standings without a phase identifier. Preserve
    # the record at edition level until a phase association is confirmed.
    for standing in (snapshot.get("operations") or {}).get("standings") or []:
        team = teams.get(_uuid(standing.get("team_id")), {})
        rows.append(
            {
                "label": "Tabla de posiciones por categoría",
                "source": OPERATIONS,
                "phase": None,
                "category": standing.get("category"),
                "status": "needs_link",
                "team": team.get("team_name") or standing.get("team_name"),
                "entity": team.get("state"),
                "played": standing.get("played"),
                "points": standing.get("points"),
                "goal_difference": standing.get("goal_difference"),
                "updated_at": None,
            }
        )
    return rows


def _legacy_registration(snapshot: dict, *, contacts_allowed: bool) -> dict:
    source = OPERATIONS
    coverage = (snapshot.get("summary") or {}).get("registrations_count")
    if snapshot.get("registration_status") == "handled_elsewhere" or coverage is None:
        return _section(source, "needs_link")
    rows = []
    for entity in (snapshot.get("breakdowns") or {}).get("entities") or []:
        display = entity.get("entity_name")
        if not display or normalize_text(display) == "sin entidad":
            display = None
        rows.append(
            {
                "label": "Padrón operativo de la entidad",
                "source": source,
                "status": "partial",
                "entity": display,
                "teams": entity.get("teams_count"),
                "registrations": None,
                "player_records": entity.get("players_count"),
                "unique_people_identified": None,
                "updated_at": None,
            }
        )
        if contacts_allowed:
            for team in entity.get("teams") or []:
                contact = team.get("primary_manager") or {}
                if contact:
                    rows.append(
                        {
                            "label": "Contacto declarado del equipo",
                            "source": source,
                            "status": "available",
                            "entity": display,
                            "category": team.get("category"),
                            "team": team.get("team_name"),
                            "name": contact.get("name"),
                            "email": contact.get("email"),
                            "phone": contact.get("phone"),
                            "updated_at": None,
                        }
                    )
    return _section(
        source,
        "partial" if rows else "empty",
        rows,
        registration_records=coverage,
        edition_verified=True,
    )


def _uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


async def _gallery_rows(
    client: SupabaseRestClient,
    tournament_id: str,
    *,
    photo_id: str | None = None,
) -> tuple[list[dict], bool]:
    filters = {"id": f"eq.{photo_id}"} if photo_id else {}
    fields = "id,title,category_id,photo_date,created_at"
    if photo_id:
        fields += ",image_url"
    limit = 1 if photo_id else PHOTO_LIMIT + 1
    category_scope, catalog_partial = await _category_catalog(client, tournament_id)
    # Current gallery writes carry tournament_id. Use an exact category foreign
    # key for old schemas that do not expose the tournament column.
    try:
        photos = await client.fetch_all_rows(
            table="gallery_photos",
            select_expr=f"{fields},tournament_id",
            filters={**filters, "tournament_id": f"eq.{tournament_id}"},
            order="photo_date.desc,id.asc",
            max_rows=limit,
        )
        scoped = [
            row
            for row in photos
            if _uuid(row.get("tournament_id")) == tournament_id
            and (
                not row.get("category_id")
                or _uuid(row["category_id"]) in category_scope
            )
        ]
    except TournamentsV2Error as exc:
        detail = str(exc)
        if "42703" not in detail or "tournament_id" not in detail:
            raise
        if not category_scope:
            return [], catalog_partial
        photos = await client.fetch_all_rows(
            table="gallery_photos",
            select_expr=fields,
            filters={**filters, "category_id": _in_filter(sorted(category_scope))},
            order="photo_date.desc,id.asc",
            max_rows=limit,
        )
        scoped = [
            row for row in photos if _uuid(row.get("category_id")) in category_scope
        ]
    return [
        {**row, "category": category_scope.get(_uuid(row.get("category_id")) or "")}
        for row in scoped
    ], (len(photos) == limit and photo_id is None) or catalog_partial


async def _gallery(snapshot: dict) -> dict:
    """Return gallery association candidates without transmitting media URLs."""
    tournament_id = _source_tournament_id(snapshot)
    if not tournament_id:
        return _section(MARKETING, "needs_link")
    try:
        client = SupabaseRestClient(load_tournaments_v2_config())
        photos, partial = await _gallery_rows(client, tournament_id)
    except (TournamentsV2Error, ValueError):
        return _section(MARKETING, "unavailable")

    candidates = []
    for photo in photos[:PHOTO_LIMIT]:
        key = _uuid(photo.get("id"))
        if key:
            candidates.append(
                {
                    "id": key,
                    "label": photo.get("title") or "Fotografía de galería",
                    "category_id": photo.get("category_id"),
                    "category": photo.get("category"),
                    "date": photo.get("photo_date"),
                    "updated_at": None,
                }
            )
    return _section(
        MARKETING,
        "needs_link" if candidates else "partial" if partial else "empty",
        candidates=candidates,
        partial=partial,
        edition_verified=True,
    )


def _approved_photo_url(value: Any, configured_origin: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        source = urlsplit(value)
        trusted = urlsplit(configured_origin)
        source_host = (source.hostname, source.port)
        trusted_host = (trusted.hostname, trusted.port)
    except ValueError:
        return None
    secure_scheme = source.scheme == "https" or (
        source.scheme == "http" and source.hostname in {"127.0.0.1", "::1", "localhost"}
    )
    if (
        not secure_scheme
        or source.scheme != trusted.scheme
        or source_host != trusted_host
        or source.username is not None
        or source.password is not None
        or source.query
        or source.fragment
    ):
        return None
    decoded = unquote(source.path)
    parts = decoded.strip("/").split("/")
    if (
        len(parts) < 6
        or parts[:3] != ["storage", "v1", "object"]
        or parts[3] not in {"public", "authenticated"}
        or parts[4] not in PHOTO_BUCKETS
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 for character in decoded)
        or "\\" in decoded
        or "%" in decoded
    ):
        return None
    return value


def _verified_image(data: bytes) -> str | None:
    formats = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    try:
        with Image.open(BytesIO(data), formats=list(formats)) as picture:
            if picture.width * picture.height > PHOTO_MAX_PIXELS:
                return None
            content_type = formats.get(picture.format or "")
            picture.verify()
            return content_type
    except (OSError, SyntaxError, Image.DecompressionBombError):
        return None


async def resolve_dossier_photo(
    session: Any,
    tournament: dict,
    edition_year: int,
    photo_id: str,
    *,
    expected_category: str | None = None,
    operations_edition_id: str | None = None,
) -> tuple[bytes, str] | None:
    """Resolve bytes for a previously authorized and scope-linked gallery photo."""
    key = _uuid(photo_id)
    if not key:
        return None
    scope = await probe_registration_scope(
        session, tournament_key=tournament["id"], edition_year=edition_year
    )
    snapshot, _ = await _soul_snapshot(
        session,
        tournament,
        edition_year,
        include_registration=False,
        operations_edition_id=operations_edition_id,
        roster_slug=(
            scope.get("roster_slug") if scope["status"] == "CONFIGURED" else None
        ),
    )
    if not snapshot:
        return None
    source_id = _source_tournament_id(snapshot)
    if not source_id:
        return None
    try:
        config = load_tournaments_v2_config()
        client = SupabaseRestClient(config)
        matches, _ = await _gallery_rows(client, source_id, photo_id=key)
        photo_row = next((row for row in matches if _uuid(row.get("id")) == key), {})
        if photo_row.get("category") != expected_category:
            return None
        if expected_category is None and photo_row.get("category_id"):
            return None
        url = _approved_photo_url(photo_row.get("image_url"), config.supabase_url)
        if not url:
            return None
        headers = client._headers(include_json=False)
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=config.request_timeout_sec, trust_env=False
        ) as transport:
            async with transport.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(data) + len(chunk) > PHOTO_MAX_BYTES:
                        return None
                    data.extend(chunk)
        content = bytes(data)
        content_type = _verified_image(content)
        return (content, content_type) if content_type else None
    except (httpx.HTTPError, TournamentsV2Error, ValueError):
        return None


def _money(value: Any) -> str | None:
    if value is None:
        return None
    amount = Decimal(str(value))
    return format(amount, "f") if amount.is_finite() else None


def _finance_row(row: dict) -> dict:
    kind = row["tipo"]
    state = row["estado"]
    paid = kind == "SOLICITUD" and (state == "pagado" or row.get("pagado_en"))
    proven = bool(paid and row.get("proof_exists"))
    canonical_amount = (
        row.get("amount_for_payment") if kind == "SOLICITUD" else row.get("monto_total")
    )
    return {
        "id": str(row["id"]),
        "kind": kind,
        "label": row["numero_referencia"],
        "reference": row["numero_referencia"],
        "operations_reference": row.get("referencia_operaciones"),
        "source": FINANCE,
        "status": (
            "payment_confirmed"
            if proven
            else "payment_without_proof" if paid else state
        ),
        "document_status": state,
        "edition_year": row.get("edition_year"),
        "category": row.get("category"),
        "categories": (
            [
                value
                for value in row["categories"]
                if isinstance(value, str) and value.strip()
            ]
            if isinstance(row.get("categories"), list)
            else []
        ),
        "phase": _phase(row.get("fase")),
        "phase_label": row.get("fase"),
        "entity": None,
        "concept": row.get("concepto_pago"),
        "supplier": row.get("supplier"),
        "amount_requested": _money(row.get("monto_solicitado")),
        "amount_document": _money(canonical_amount),
        "amount_paid": _money(canonical_amount) if proven else None,
        "currency": row.get("currency"),
        "requested_at": _iso(row.get("creado_en")),
        "effective_payment_date": (
            _iso(row.get("fecha_pago_efectiva")) if proven else None
        ),
        "payment_recorded_at": _iso(row.get("pagado_en")) if paid else None,
        "payment_method": row.get("metodo_pago") if proven else None,
        "proof_available": proven,
        "updated_at": _iso(row.get("last_recorded_event_at")),
        "supplier_advance": row.get("is_supplier_advance"),
        "expense_dates": row.get("expense_dates"),
        "expenses_by_currency": row.get("expenses_by_currency"),
    }


_DOCUMENT_EDITION = """(
    d.edicion = :edition_year OR (
        d.edicion IS NULL AND d.id = ANY(CAST(:linked_ids AS uuid[]))
    )
)"""


async def _finance(
    session: Any,
    tournament_id: str,
    edition_year: int,
    linked_document_ids: tuple[str, ...],
) -> dict:
    linked_ids = sorted(
        {key for value in linked_document_ids if (key := _uuid(value)) is not None}
    )
    try:
        documents = await _rows(
            session,
            f"""WITH canonical_payment AS (
                  {_PAYMENT_RUN_SOURCE_SQL}
                  WHERE d.torneo_id = CAST(:tournament_id AS uuid)
                    AND {_DOCUMENT_EDITION} AND d.tipo = 'SOLICITUD'
                )
                SELECT d.id, d.tipo, d.estado, d.numero_referencia,
                       d.referencia_operaciones, d.fase, d.edicion AS edition_year,
                       d.categorias AS categories,
                       CASE WHEN JSONB_TYPEOF(d.categorias) = 'array' THEN
                           CASE WHEN JSONB_ARRAY_LENGTH(d.categorias) = 1
                                THEN d.categorias ->> 0 END
                       END AS category,
                       d.monto_total, d.monto_solicitado, d.concepto_pago, d.currency,
                       d.is_supplier_advance, d.creado_en, d.pagado_en,
                       d.fecha_pago_efectiva, d.metodo_pago,
                       GREATEST(d.creado_en, d.enviado_en, d.aprobado_en, d.pagado_en,
                                expenses.latest_update) AS last_recorded_event_at,
                       expenses.expense_dates, expenses.expenses_by_currency,
                       supplier.nombre AS supplier,
                       p.monto AS amount_for_payment,
                       EXISTS (
                           SELECT 1 FROM adjuntos a
                            WHERE a.documento_id = d.id
                              AND a.categoria = 'comprobante_pago'
                              AND a.activo AND a.eliminado_en IS NULL
                       ) AS proof_exists
                  FROM documentos d
                  LEFT JOIN canonical_payment p ON p.id = d.id
                  LEFT JOIN proveedores_clientes supplier
                         ON supplier.id = COALESCE(
                             d.beneficiario_proveedor_cliente_id, d.proveedor_cliente_id
                         )
                  LEFT JOIN LATERAL (
                      WITH scoped_expenses AS (
                          SELECT er.currency, er.fecha, er.gasto_cantidad, er.updated_at
                            FROM expense_reports er
                           WHERE d.tipo = 'INFORME'
                             AND (
                                 er.informe_documento_id = d.id
                                 OR (er.informe_documento_id IS NULL
                                     AND er.documento_id = d.id)
                             )
                             AND (er.edicion IS NULL OR er.edicion = :edition_year)
                             AND NULLIF(LOWER(BTRIM(er.fase_torneo)), '') =
                                 NULLIF(LOWER(BTRIM(d.fase)), '')
                             AND COALESCE(er.estado_gasto, 'activo') = 'activo'
                      ), totals AS (
                          SELECT currency, COUNT(*) AS expenses,
                                 SUM(gasto_cantidad::numeric)::text AS amount
                            FROM scoped_expenses
                           GROUP BY currency
                      )
                      SELECT (
                                 SELECT JSONB_AGG(TO_JSONB(totals) ORDER BY currency)
                                   FROM totals
                             ) AS expenses_by_currency,
                             JSONB_AGG(DISTINCT fecha::date ORDER BY fecha::date)
                                 FILTER (WHERE fecha IS NOT NULL) AS expense_dates,
                             MAX(updated_at) AS latest_update
                        FROM scoped_expenses
                  ) expenses ON d.tipo = 'INFORME'
                 WHERE d.torneo_id = CAST(:tournament_id AS uuid)
                   AND {_DOCUMENT_EDITION} AND d.tipo IN ('SOLICITUD', 'INFORME')
                 ORDER BY (d.id = ANY(CAST(:linked_ids AS uuid[]))) DESC,
                          d.creado_en DESC NULLS LAST, d.id
                 LIMIT :row_limit""",
            {
                "tournament_id": tournament_id,
                "edition_year": edition_year,
                "row_limit": FINANCE_LIMIT + 1,
                "linked_ids": linked_ids,
                "reimbursement_prefix": f"{EMPLOYEE_REIMBURSEMENT_PREFIX}%",
            },
        )
    except SQLAlchemyError:
        return _section(FINANCE, "unavailable")
    status = (
        "partial"
        if len(documents) > FINANCE_LIMIT
        else ("available" if documents else "empty")
    )
    return _section(
        FINANCE, status, [_finance_row(row) for row in documents[:FINANCE_LIMIT]]
    )


async def _project_categories(session: Any, tournament_id: str) -> set[str]:
    try:
        rows = await _rows(
            session,
            """SELECT categorias FROM tournaments
                WHERE id = CAST(:tournament_id AS uuid) AND active""",
            {"tournament_id": tournament_id},
        )
    except SQLAlchemyError:
        return set()
    values = rows[0].get("categorias") if rows else None
    return (
        {value.strip() for value in values if isinstance(value, str) and value.strip()}
        if isinstance(values, list)
        else set()
    )


async def load_dossier_sources(
    session: Any,
    *,
    tournament: dict,
    edition_year: int,
    finance_allowed: bool,
    contacts_allowed: bool,
    media_allowed: bool,
    linked_document_ids: tuple[str, ...] = (),
    operations_allowed: bool = True,
    marketing_allowed: bool = True,
    operations_edition_id: str | None = None,
) -> dict:
    """Assemble allowlisted facts after the route has authorized this exact edition."""
    # A configured but failing PostgreSQL source never falls back to a separate
    # roster. An exact legacy operational edition can supply its own counts only
    # if PostgreSQL has no binding for the tournament.
    legacy_roster = False
    registration = _section(REGISTRATION, "denied")
    show_gallery = marketing_allowed and media_allowed
    scope = (
        await probe_registration_scope(
            session, tournament_key=tournament["id"], edition_year=edition_year
        )
        if operations_allowed or show_gallery
        else {}
    )
    if operations_allowed:
        registration = await _registration(session, scope)
        legacy_roster = scope["status"] == "NOT_CONFIGURED"

    snapshot, status = (
        await _soul_snapshot(
            session,
            tournament,
            edition_year,
            include_registration=legacy_roster,
            operations_edition_id=operations_edition_id,
            roster_slug=(
                scope.get("roster_slug") if scope["status"] == "CONFIGURED" else None
            ),
        )
        if operations_allowed or show_gallery
        else (None, "denied")
    )
    operations = _section(OPERATIONS, status if operations_allowed else "denied")
    marketing = _section(MARKETING, status if show_gallery else "denied")
    sport_categories: dict[str, str | None] = {}
    if snapshot and operations_allowed:
        sport_categories, match_teams, teams = await _sport_relationships(snapshot)
        sport = _sport_rows(snapshot, match_teams, teams)
        optional = snapshot.get("optional_sources") or {}
        matches_available = (optional.get("matches") or {}).get("available")
        positions_available = (optional.get("team_standings") or {}).get("available")
        partial = (
            matches_available is False
            or positions_available is False
            or any(
                (optional.get(table) or {}).get("rows", 0) >= MATCH_LIMIT
                for table in ("matches", "team_standings")
            )
        )
        operations = _section(
            OPERATIONS,
            (
                "partial"
                if partial
                else (
                    "available"
                    if sport
                    else (
                        "empty"
                        if matches_available and positions_available
                        else "needs_link"
                    )
                )
            ),
            sport,
            edition_verified=True,
        )
        if legacy_roster:
            registration = _legacy_registration(
                snapshot, contacts_allowed=contacts_allowed
            )
    if snapshot and show_gallery:
        marketing = await _gallery(snapshot)

    finance = (
        await _finance(session, tournament["id"], edition_year, linked_document_ids)
        if finance_allowed
        else _section(FINANCE, "denied")
    )
    entities = sorted(
        {row["entity"] for row in registration["rows"] if row.get("entity")}
        | {entity for row in operations["rows"] for entity in row.get("entities", [])}
        | {row["entity"] for row in operations["rows"] if row.get("entity")}
    )
    categories = {name for name in sport_categories.values() if name} | {
        row["category"] for row in registration["rows"] if row.get("category")
    }
    if operations_allowed and not categories:
        categories = await _project_categories(session, tournament["id"])
    return {
        "registration": registration,
        "operations": operations,
        "finance": finance,
        "marketing": marketing,
        "entities": entities,
        "categories": sorted(categories),
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }
