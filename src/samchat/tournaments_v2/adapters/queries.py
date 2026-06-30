from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import re
from typing import Any, Dict, Iterable, List, Optional

from ..config import load_tournaments_v2_config
from ..supabase_client import SupabaseRestClient, TournamentsV2Error


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("Invalid date format; use YYYY-MM-DD or DD/MM/YYYY")


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text(value: Any) -> str:
    text = _safe_str(value).lower()
    text = (
        text.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    return re.sub(r"\s+", " ", text)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalize_text(value)).strip("-") or "torneo"


def _in_filter(values: Iterable[str]) -> str:
    normalized = [str(v).strip() for v in values if str(v).strip()]
    if not normalized:
        raise ValueError("Cannot build in-filter from empty values")
    return "in.(" + ",".join(normalized) + ")"


def _scope_patterns(tournament_key: str) -> Dict[str, list[str]]:
    key = _safe_str(tournament_key).lower()
    if key in {
        "beisbol",
        "beis",
        "liga_telmex_beisbol",
        "liga-telmex-beisbol",
        "liga_telmex_telcel",
        "liga-telmex-telcel",
        "telmex",
    }:
        return {"include": ["beis", "liga telmex"], "exclude": []}
    return {"include": [], "exclude": []}


def _infer_gender(*, tournament_name: str, tournament_slug: str, category_name: str) -> Optional[str]:
    haystack = " ".join(
        [
            _normalize_text(tournament_name),
            _normalize_text(tournament_slug),
            _normalize_text(category_name),
        ]
    )
    if "femen" in haystack:
        return "femenil"
    if "varon" in haystack or "mascul" in haystack:
        return "varonil"
    return None


def _is_missing_column_error(exc: Exception, *columns: str) -> bool:
    message = _normalize_text(exc)
    if "column" not in message or "does not exist" not in message:
        return False
    return any(_normalize_text(column) in message for column in columns)


async def _select_rows_with_fallback(
    client: SupabaseRestClient,
    *,
    table: str,
    primary_select_expr: str,
    fallback_select_expr: str,
    filters: Optional[Dict[str, str]] = None,
    order: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    missing_columns: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    try:
        return await client.select_rows(
            table=table,
            select_expr=primary_select_expr,
            filters=filters,
            order=order,
            limit=limit,
            offset=offset,
        )
    except TournamentsV2Error as exc:
        if not _is_missing_column_error(exc, *(missing_columns or [])):
            raise
        return await client.select_rows(
            table=table,
            select_expr=fallback_select_expr,
            filters=filters,
            order=order,
            limit=limit,
            offset=offset,
        )


async def _fetch_all_rows_with_fallback(
    client: SupabaseRestClient,
    *,
    table: str,
    primary_select_expr: str,
    fallback_select_expr: str,
    filters: Optional[Dict[str, str]] = None,
    order: Optional[str] = None,
    max_rows: Optional[int] = None,
    missing_columns: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    try:
        return await client.fetch_all_rows(
            table=table,
            select_expr=primary_select_expr,
            filters=filters,
            order=order,
            max_rows=max_rows,
        )
    except TournamentsV2Error as exc:
        if not _is_missing_column_error(exc, *(missing_columns or [])):
            raise
        return await client.fetch_all_rows(
            table=table,
            select_expr=fallback_select_expr,
            filters=filters,
            order=order,
            max_rows=max_rows,
        )


def infer_tournament_key_from_slug(tournament_slug: Optional[str]) -> str:
    slug = _normalize_text(tournament_slug)
    if "beis" in slug:
        return "beisbol"
    if "liga_telmex_telcel" in slug or "liga telmex telcel" in slug:
        return "beisbol"
    if "telmex" in slug:
        return "beisbol"
    return "all"


async def resolve_tournaments_for_scope(
    client: SupabaseRestClient,
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
) -> List[Dict[str, Any]]:
    tournaments = await client.fetch_all_rows(
        table="tournaments",
        select_expr="id,name,slug,is_active,start_date,end_date",
        order="is_active.desc,created_at.desc",
        max_rows=500,
    )
    slug_hint = _normalize_text(tournament_slug)
    patterns = _scope_patterns(tournament_key)
    matched: List[Dict[str, Any]] = []
    for row in tournaments:
        row_id = _normalize_text(row.get("id"))
        slug = _normalize_text(row.get("slug"))
        name = _normalize_text(row.get("name"))
        combined = f"{slug} {name}"
        if slug_hint:
            slug_token = _slugify(slug_hint)
            if (
                row_id == slug_hint
                or slug == slug_hint
                or slug == slug_token
                or slug_hint in combined
            ):
                matched.append(row)
            continue
        include = patterns["include"]
        exclude = patterns["exclude"]
        if include and not any(token in combined for token in include):
            continue
        if exclude and any(token in combined for token in exclude):
            continue
        if not include and not bool(row.get("is_active", True)):
            continue
        matched.append(row)
    if slug_hint and not matched:
        raise TournamentsV2Error(f"No Supabase tournament matched tournament_slug={tournament_slug}")
    if not matched and _safe_str(tournament_key).lower() != "all":
        raise TournamentsV2Error(f"No Supabase tournaments matched scope={tournament_key}")
    return matched


async def resolve_primary_tournament(
    client: SupabaseRestClient,
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
) -> Dict[str, Any]:
    tournaments = await resolve_tournaments_for_scope(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug or tournament_name,
    )
    if tournaments:
        return tournaments[0]
    if tournament_name:
        tournaments = await resolve_tournaments_for_scope(
            client,
            tournament_key=tournament_key,
            tournament_slug=_slugify(tournament_name),
        )
        if tournaments:
            return tournaments[0]
    raise TournamentsV2Error("Tournament not found in Supabase")


async def resolve_category_for_tournament(
    client: SupabaseRestClient,
    *,
    tournament_id: str,
    category_id: Optional[str] = None,
    category_name: Optional[str] = None,
    default_first: bool = True,
) -> Dict[str, Any]:
    category_rows: List[Dict[str, Any]] = []
    if _safe_str(category_id):
        category_rows = await _select_rows_with_fallback(
            client,
            table="categories",
            primary_select_expr="id,name,tournament_id,branch",
            fallback_select_expr="id,name,tournament_id",
            filters={"id": f"eq.{_safe_str(category_id)}"},
            limit=1,
            missing_columns=["branch"],
        )
    elif _safe_str(category_name):
        category_rows = await _select_rows_with_fallback(
            client,
            table="categories",
            primary_select_expr="id,name,tournament_id,branch",
            fallback_select_expr="id,name,tournament_id",
            filters={
                "tournament_id": f"eq.{_safe_str(tournament_id)}",
                "name": f"ilike.*{_safe_str(category_name)}*",
            },
            limit=1,
            missing_columns=["branch"],
        )
    if not category_rows and default_first:
        category_rows = await _select_rows_with_fallback(
            client,
            table="categories",
            primary_select_expr="id,name,tournament_id,branch",
            fallback_select_expr="id,name,tournament_id",
            filters={"tournament_id": f"eq.{_safe_str(tournament_id)}"},
            order="created_at.asc",
            limit=1,
            missing_columns=["branch"],
        )
    if not category_rows:
        raise TournamentsV2Error("No category found for tournament")
    return category_rows[0]


async def resolve_team_for_tournament(
    client: SupabaseRestClient,
    *,
    tournament_id: str,
    team_id: Optional[str] = None,
    team_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    filters = {"tournament_id": f"eq.{_safe_str(tournament_id)}"}
    if _safe_str(team_id):
        filters["id"] = f"eq.{_safe_str(team_id)}"
    elif _safe_str(team_name):
        filters["team_name"] = f"eq.{_safe_str(team_name)}"
    else:
        raise TournamentsV2Error("Provide team_id or team_name")
    if _safe_str(user_id):
        filters["user_id"] = f"eq.{_safe_str(user_id)}"
    rows = await _select_rows_with_fallback(
        client,
        table="teams",
        primary_select_expr="id,team_name,user_id,tournament_id,state,municipality,created_at",
        fallback_select_expr="id,team_name,user_id,tournament_id,state,created_at",
        filters=filters,
        order="created_at.desc",
        limit=1,
        missing_columns=["municipality"],
    )
    if not rows and _safe_str(team_name):
        fuzzy_filters = {"tournament_id": f"eq.{_safe_str(tournament_id)}", "team_name": f"ilike.*{_safe_str(team_name)}*"}
        if _safe_str(user_id):
            fuzzy_filters["user_id"] = f"eq.{_safe_str(user_id)}"
        rows = await _select_rows_with_fallback(
            client,
            table="teams",
            primary_select_expr="id,team_name,user_id,tournament_id,state,municipality,created_at",
            fallback_select_expr="id,team_name,user_id,tournament_id,state,created_at",
            filters=fuzzy_filters,
            order="created_at.desc",
            limit=1,
            missing_columns=["municipality"],
        )
    if not rows and _safe_str(user_id):
        fallback_filters = dict(filters)
        fallback_filters.pop("user_id", None)
        rows = await _select_rows_with_fallback(
            client,
            table="teams",
            primary_select_expr="id,team_name,user_id,tournament_id,state,municipality,created_at",
            fallback_select_expr="id,team_name,user_id,tournament_id,state,created_at",
            filters=fallback_filters,
            order="created_at.desc",
            limit=1,
            missing_columns=["municipality"],
        )
    if not rows:
        raise TournamentsV2Error("Team not found in Supabase")
    return rows[0]


async def _load_scope_dataset(
    client: SupabaseRestClient,
    *,
    tournament_key: str,
    tournament_slug: Optional[str],
) -> Dict[str, Any]:
    tournaments = await resolve_tournaments_for_scope(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
    )
    if not tournaments:
        return {"tournaments": [], "categories": [], "teams": [], "registrations": [], "players": []}

    tournament_ids = [str(row["id"]) for row in tournaments if row.get("id")]
    categories = await _fetch_all_rows_with_fallback(
        client,
        table="categories",
        primary_select_expr="id,name,year_born,tournament_id,max_players_per_team,branch",
        fallback_select_expr="id,name,year_born,tournament_id,max_players_per_team",
        filters={"tournament_id": _in_filter(tournament_ids)},
        order="name.asc",
        missing_columns=["branch"],
    )
    teams = await _fetch_all_rows_with_fallback(
        client,
        table="teams",
        primary_select_expr=(
            "id,team_name,state,country,phone_country_code,phone_number,status,"
            "academy_name,municipality,shield_url,facebook_url,instagram_url,"
            "created_at,updated_at,tournament_id,user_id"
        ),
        fallback_select_expr=(
            "id,team_name,state,country,phone_country_code,phone_number,status,"
            "academy_name,created_at,updated_at,tournament_id,user_id"
        ),
        filters={"tournament_id": _in_filter(tournament_ids)},
        order="created_at.desc",
        missing_columns=["municipality", "shield_url", "facebook_url", "instagram_url"],
    )
    team_ids = [str(row["id"]) for row in teams if row.get("id")]
    if not team_ids:
        return {
            "tournaments": tournaments,
            "categories": categories,
            "teams": [],
            "registrations": [],
            "players": [],
        }
    registrations = await client.fetch_all_rows(
        table="registrations",
        select_expr=(
            "id,team_id,category_id,payment_status,registration_date,notes,"
            "payment_amount,payment_date,payment_reference"
        ),
        filters={"team_id": _in_filter(team_ids)},
        order="registration_date.desc",
    )
    registration_ids = [str(row["id"]) for row in registrations if row.get("id")]
    players: List[Dict[str, Any]] = []
    if registration_ids:
        players = await _fetch_all_rows_with_fallback(
            client,
            table="players",
            primary_select_expr=(
                "id,registration_id,first_name,last_name,paternal_surname,maternal_surname,"
                "birth_date,curp,parent_name,parent_email,parent_phone,email,jersey_number,"
                "position,documents_complete,documents_verified,verification_notes"
            ),
            fallback_select_expr=(
                "id,registration_id,first_name,last_name,paternal_surname,maternal_surname,"
                "birth_date,curp,parent_name,parent_email,parent_phone,jersey_number,"
                "position,documents_complete,documents_verified,verification_notes"
            ),
            filters={"registration_id": _in_filter(registration_ids)},
            order="created_at.asc",
            missing_columns=["email"],
        )
    return {
        "tournaments": tournaments,
        "categories": categories,
        "teams": teams,
        "registrations": registrations,
        "players": players,
    }


def _matches_optional_filter(value: Any, expected: Optional[str]) -> bool:
    query = _normalize_text(expected)
    if not query:
        return True
    return query in _normalize_text(value)


def _matches_date_window(row_date: Optional[str], date_from: Optional[date], date_to: Optional[date]) -> bool:
    if not row_date:
        return date_from is None and date_to is None
    raw = _safe_str(row_date)
    dt: Optional[date] = None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            dt = None
    if dt is None:
        return date_from is None and date_to is None
    if date_from and dt < date_from:
        return False
    if date_to and dt > date_to:
        return False
    return True


async def tournament_ops_query_v2(
    *,
    tournament_key: str,
    question: Optional[str] = None,
    state: Optional[str] = None,
    municipality: Optional[str] = None,
    category: Optional[str] = None,
    gender: Optional[str] = None,
    team_name: Optional[str] = None,
    tournament_slug: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    client = SupabaseRestClient(config)
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    max_limit = max(1, min(int(limit or 50), 200))
    dataset = await _load_scope_dataset(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
    )

    tournaments = {str(row["id"]): row for row in dataset["tournaments"]}
    categories = {str(row["id"]): row for row in dataset["categories"]}
    teams = {str(row["id"]): row for row in dataset["teams"]}
    players_by_registration: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for player in dataset["players"]:
        registration_id = _safe_str(player.get("registration_id"))
        if registration_id:
            players_by_registration[registration_id].append(player)

    contexts: List[Dict[str, Any]] = []
    for reg in dataset["registrations"]:
        team = teams.get(_safe_str(reg.get("team_id")))
        category_row = categories.get(_safe_str(reg.get("category_id")))
        if not team or not category_row:
            continue
        tournament = tournaments.get(_safe_str(team.get("tournament_id")))
        category_name = _safe_str(category_row.get("name"))
        branch_value = _safe_str(category_row.get("branch")) or None
        inferred_gender = branch_value or _infer_gender(
            tournament_name=_safe_str((tournament or {}).get("name")),
            tournament_slug=_safe_str((tournament or {}).get("slug")),
            category_name=category_name,
        )
        if not _matches_optional_filter(team.get("state"), state):
            continue
        if not _matches_optional_filter(team.get("team_name"), team_name):
            continue
        if not _matches_optional_filter(category_name, category):
            continue
        municipality_value = _safe_str(team.get("municipality")) or _safe_str(team.get("academy_name"))
        if not _matches_optional_filter(municipality_value, municipality):
            if _normalize_text(municipality):
                continue
        if not _matches_optional_filter(inferred_gender, gender):
            continue
        row_date = reg.get("registration_date") or team.get("created_at")
        if not _matches_date_window(_safe_str(row_date), df, dt):
            continue
        contexts.append(
            {
                "team": team,
                "registration": reg,
                "category": category_row,
                "tournament": tournament or {},
                "gender": inferred_gender,
                "players": players_by_registration.get(_safe_str(reg.get("id")), []),
            }
        )

    unique_team_ids = {str(ctx["team"]["id"]) for ctx in contexts}
    total_players = sum(len(ctx["players"]) for ctx in contexts)

    by_municipality: Dict[str, Dict[str, Any]] = {}
    by_category: Dict[str, Dict[str, Any]] = {}
    by_gender: Dict[str, Dict[str, Any]] = {}
    teams_agg: Dict[str, Dict[str, Any]] = {}
    players_rows: List[Dict[str, Any]] = []

    for ctx in contexts:
        team = ctx["team"]
        reg = ctx["registration"]
        cat = ctx["category"]
        tournament = ctx["tournament"]
        category_label = _safe_str(cat.get("name")) or "(sin categoria)"
        municipality_label = _safe_str(team.get("municipality")) or _safe_str(team.get("academy_name")) or "(sin municipio)"
        gender_label = _safe_str(ctx.get("gender")) or "(sin rama)"
        team_id = _safe_str(team.get("id"))

        muni_bucket = by_municipality.setdefault(
            municipality_label,
            {"municipio": municipality_label, "equipos_ids": set(), "jugadores": 0},
        )
        muni_bucket["equipos_ids"].add(team_id)
        muni_bucket["jugadores"] += len(ctx["players"])

        cat_bucket = by_category.setdefault(
            category_label,
            {"categoria": category_label, "equipos_ids": set(), "jugadores": 0},
        )
        cat_bucket["equipos_ids"].add(team_id)
        cat_bucket["jugadores"] += len(ctx["players"])

        gender_bucket = by_gender.setdefault(
            gender_label,
            {"rama": gender_label, "equipos_ids": set(), "jugadores": 0},
        )
        gender_bucket["equipos_ids"].add(team_id)
        gender_bucket["jugadores"] += len(ctx["players"])

        team_entry = teams_agg.setdefault(
            team_id,
            {
                "team_id": team_id,
                "equipo": team.get("team_name"),
                "estado": team.get("state"),
                "municipio": municipality_label,
                "categorias": [],
                "rama": gender_label if gender_label != "(sin rama)" else None,
                "tournament_slug": tournament.get("slug"),
                "tournament_name": tournament.get("name"),
                "jugadores": 0,
                "created_at": team.get("created_at"),
                "payment_statuses": set(),
            },
        )
        if category_label not in team_entry["categorias"]:
            team_entry["categorias"].append(category_label)
        payment_status = _safe_str(reg.get("payment_status"))
        if payment_status:
            team_entry["payment_statuses"].add(payment_status)
        team_entry["jugadores"] += len(ctx["players"])

        for player in ctx["players"]:
            players_rows.append(
            {
                "player_id": _safe_str(player.get("id")),
                "nombre": " ".join(
                        part
                        for part in [
                            _safe_str(player.get("first_name")),
                            _safe_str(player.get("last_name")),
                        ]
                        if part
                    ).strip(),
                "birth_date": player.get("birth_date"),
                "curp": player.get("curp"),
                "email": player.get("email"),
                "jersey_number": player.get("jersey_number"),
                "equipo": team.get("team_name"),
                "categoria": category_label,
                    "rama": gender_label if gender_label != "(sin rama)" else None,
                    "estado": team.get("state"),
                    "municipio": municipality_label,
                    "tournament_slug": tournament.get("slug"),
                }
            )

    municipality_breakdown = sorted(
        (
            {
                "municipio": row["municipio"],
                "equipos": len(row["equipos_ids"]),
                "jugadores": int(row["jugadores"]),
            }
            for row in by_municipality.values()
        ),
        key=lambda item: (item["jugadores"], item["equipos"]),
        reverse=True,
    )[:max_limit]
    category_breakdown = sorted(
        (
            {
                "categoria": row["categoria"],
                "equipos": len(row["equipos_ids"]),
                "jugadores": int(row["jugadores"]),
            }
            for row in by_category.values()
        ),
        key=lambda item: (item["jugadores"], item["equipos"]),
        reverse=True,
    )[:max_limit]
    gender_breakdown = sorted(
        (
            {
                "rama": row["rama"],
                "equipos": len(row["equipos_ids"]),
                "jugadores": int(row["jugadores"]),
            }
            for row in by_gender.values()
        ),
        key=lambda item: (item["jugadores"], item["equipos"]),
        reverse=True,
    )[:max_limit]
    teams_rows = sorted(
        (
            {
                **row,
                "payment_statuses": sorted(row["payment_statuses"]),
            }
            for row in teams_agg.values()
        ),
        key=lambda item: (item["jugadores"], _safe_str(item["equipo"])),
        reverse=True,
    )[:max_limit]

    return {
        "tournament_key": tournament_key,
        "question": _safe_str(question) or None,
        "filters": {
            "state": _safe_str(state) or None,
            "municipality": _safe_str(municipality) or None,
            "category": _safe_str(category) or None,
            "gender": _safe_str(gender) or None,
            "team_name": _safe_str(team_name) or None,
            "tournament_slug": _safe_str(tournament_slug) or None,
            "date_from": df.isoformat() if df else None,
            "date_to": dt.isoformat() if dt else None,
        },
        "resolved_tournaments": [
            {
                "id": _safe_str(row.get("id")),
                "name": row.get("name"),
                "slug": row.get("slug"),
                "is_active": row.get("is_active"),
            }
            for row in dataset["tournaments"]
        ],
        "totals": {
            "equipos": len(unique_team_ids),
            "jugadores": int(total_players),
            "registros": len(contexts),
        },
        "breakdowns": {
            "por_municipio": municipality_breakdown,
            "por_categoria": category_breakdown,
            "por_rama": gender_breakdown,
        },
        "teams": teams_rows,
        "players": players_rows[:max_limit],
        "limit": max_limit,
        "source": "supabase_tournaments_v2",
        "nota": (
            "Consulta universal de operaciones sobre Supabase "
            "(tournaments/categories/teams/registrations/players). "
            "Municipio usa teams.municipality cuando existe y cae a academy_name como compatibilidad."
        ),
    }


async def team_roster_query_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    team_name: str,
    category_name: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    team_name_value = _safe_str(team_name)
    if not team_name_value:
        raise ValueError("team_name is required")
    config = load_tournaments_v2_config()
    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))
    team = await resolve_team_for_tournament(
        client,
        tournament_id=tournament_id,
        team_name=team_name_value,
    )
    registrations = await client.fetch_all_rows(
        table="registrations",
        select_expr="id,category_id,registration_date,payment_status",
        filters={"team_id": f"eq.{_safe_str(team.get('id'))}"},
        order="registration_date.desc",
    )
    categories = {
        _safe_str(row.get("id")): row
        for row in await _fetch_all_rows_with_fallback(
            client,
            table="categories",
            primary_select_expr="id,name,tournament_id,branch",
            fallback_select_expr="id,name,tournament_id",
            filters={"tournament_id": f"eq.{tournament_id}"},
            order="name.asc",
            missing_columns=["branch"],
        )
    }
    selected_registration = None
    if _safe_str(category_name):
        for reg in registrations:
            cat = categories.get(_safe_str(reg.get("category_id"))) or {}
            if _normalize_text(cat.get("name")) == _normalize_text(category_name) or _normalize_text(category_name) in _normalize_text(cat.get("name")):
                selected_registration = reg
                break
    if selected_registration is None and registrations:
        selected_registration = registrations[0]
    if selected_registration is None:
        return {
            "source": "supabase_tournaments_v2",
            "tournament": {"id": tournament_id, "name": tournament.get("name"), "slug": tournament.get("slug")},
            "team": {"id": _safe_str(team.get("id")), "team_name": team.get("team_name")},
            "players": [],
            "players_count": 0,
            "nota": "El equipo existe en Supabase, pero no tiene registros/categoria asociados.",
        }

    reg_id = _safe_str(selected_registration.get("id"))
    category = categories.get(_safe_str(selected_registration.get("category_id"))) or {}
    players = await _fetch_all_rows_with_fallback(
        client,
        table="players",
        primary_select_expr="id,first_name,last_name,paternal_surname,maternal_surname,birth_date,curp,email,jersey_number",
        fallback_select_expr="id,first_name,last_name,paternal_surname,maternal_surname,birth_date,curp,jersey_number",
        filters={"registration_id": f"eq.{reg_id}"},
        order="jersey_number.asc,created_at.asc",
        max_rows=max(1, min(int(limit or 50), 200)),
        missing_columns=["email"],
    )
    player_rows = [
        {
            "player_id": _safe_str(row.get("id")),
            "nombre": " ".join(
                part
                for part in [
                    _safe_str(row.get("first_name")),
                    _safe_str(row.get("last_name")),
                ]
                if part
            ).strip(),
            "birth_date": row.get("birth_date"),
            "curp": row.get("curp"),
            "email": row.get("email"),
            "jersey_number": row.get("jersey_number"),
        }
        for row in players
    ]
    return {
        "source": "supabase_tournaments_v2",
        "tournament": {"id": tournament_id, "name": tournament.get("name"), "slug": tournament.get("slug")},
        "category": {
            "id": _safe_str(category.get("id")),
            "name": category.get("name"),
            "branch": category.get("branch"),
        },
        "team": {
            "id": _safe_str(team.get("id")),
            "team_name": team.get("team_name"),
            "state": team.get("state"),
            "municipality": team.get("municipality"),
        },
        "registration": {"id": reg_id},
        "players": player_rows,
        "players_count": len(player_rows),
    }


async def team_summary_query_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    team_name: str,
) -> Dict[str, Any]:
    roster = await team_roster_query_v2(
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
        team_name=team_name,
        limit=200,
    )
    team = roster.get("team") or {}
    category = roster.get("category") or {}
    tournament = roster.get("tournament") or {}
    players = roster.get("players") or []
    return {
        "source": "supabase_tournaments_v2",
        "tournament": tournament,
        "category": category,
        "team": team,
        "players_count": len(players),
        "sample_players": players[:8],
    }


async def teams_list_query_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))
    teams = await _fetch_all_rows_with_fallback(
        client,
        table="teams",
        primary_select_expr="id,team_name,state,municipality,created_at",
        fallback_select_expr="id,team_name,state,created_at",
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="created_at.desc",
        max_rows=max(1, min(int(limit or 50), 200)),
        missing_columns=["municipality"],
    )
    team_ids = [_safe_str(row.get("id")) for row in teams if _safe_str(row.get("id"))]
    registrations_by_team: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    players_by_registration: Dict[str, int] = defaultdict(int)
    categories: Dict[str, Dict[str, Any]] = {}
    if team_ids:
        registrations = await client.fetch_all_rows(
            table="registrations",
            select_expr="id,team_id,category_id,registration_date",
            filters={"team_id": _in_filter(team_ids)},
            order="registration_date.desc",
        )
        registration_ids = [_safe_str(row.get("id")) for row in registrations if _safe_str(row.get("id"))]
        for reg in registrations:
            registrations_by_team[_safe_str(reg.get("team_id"))].append(reg)
        if registrations:
            category_ids = sorted(
                {
                    _safe_str(row.get("category_id"))
                    for row in registrations
                    if _safe_str(row.get("category_id"))
                }
            )
            if category_ids:
                category_rows = await _fetch_all_rows_with_fallback(
                    client,
                    table="categories",
                    primary_select_expr="id,name,tournament_id,branch",
                    fallback_select_expr="id,name,tournament_id",
                    filters={"id": _in_filter(category_ids)},
                    order="name.asc",
                    missing_columns=["branch"],
                )
                categories = {_safe_str(row.get("id")): row for row in category_rows}
        if registration_ids:
            players = await client.fetch_all_rows(
                table="players",
                select_expr="id,registration_id",
                filters={"registration_id": _in_filter(registration_ids)},
                order="created_at.asc",
            )
            for row in players:
                reg_id = _safe_str(row.get("registration_id"))
                if reg_id:
                    players_by_registration[reg_id] += 1
    rows: List[Dict[str, Any]] = []
    for team in teams:
        regs = registrations_by_team.get(_safe_str(team.get("id")), [])
        reg = regs[0] if regs else {}
        cat = categories.get(_safe_str(reg.get("category_id"))) or {}
        player_count = sum(players_by_registration.get(_safe_str(r.get("id")), 0) for r in regs)
        rows.append(
            {
                "team_id": _safe_str(team.get("id")),
                "team_name": team.get("team_name"),
                "state": team.get("state"),
                "municipality": team.get("municipality"),
                "category": cat.get("name"),
                "branch": cat.get("branch") or _infer_gender(
                    tournament_name=_safe_str(tournament.get("name")),
                    tournament_slug=_safe_str(tournament.get("slug")),
                    category_name=_safe_str(cat.get("name")),
                ),
                "players_count": player_count,
            }
        )
    return {
        "source": "supabase_tournaments_v2",
        "tournament": {"id": tournament_id, "name": tournament.get("name"), "slug": tournament.get("slug")},
        "teams": rows,
        "teams_count": len(rows),
    }


async def registration_breakdown_v2(
    *,
    tournament_key: str,
    state: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    tournament_slug: Optional[str] = None,
) -> Dict[str, Any]:
    state_value = _safe_str(state)
    if not state_value:
        raise ValueError("state is required")
    result = await tournament_ops_query_v2(
        tournament_key=tournament_key,
        state=state_value,
        tournament_slug=tournament_slug,
        date_from=date_from,
        date_to=date_to,
        limit=200,
    )
    return {
        "tournament_key": tournament_key,
        "state_query": state_value,
        "date_from": result["filters"]["date_from"],
        "date_to": result["filters"]["date_to"],
        "resolved_tournaments": result.get("resolved_tournaments", []),
        "total_equipos": int((result.get("totals") or {}).get("equipos") or 0),
        "total_jugadores": int((result.get("totals") or {}).get("jugadores") or 0),
        "desglose_por_municipio": ((result.get("breakdowns") or {}).get("por_municipio") or []),
        "source": "supabase_tournaments_v2",
        "nota": (
            "Conteo basado en Supabase (teams/registrations/players) "
            "para el alcance de torneos resuelto."
        ),
    }


async def _optional_rows(
    client: SupabaseRestClient,
    *,
    table: str,
    select_expr: str,
    filters: Optional[Dict[str, str]] = None,
    order: Optional[str] = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        rows = await client.fetch_all_rows(
            table=table,
            select_expr=select_expr,
            filters=filters,
            order=order,
            max_rows=max(1, min(int(limit or 100), 1000)),
        )
        return rows, None
    except TournamentsV2Error as exc:
        return [], str(exc)


def _index_many(rows: Iterable[dict[str, Any]], key: str) -> Dict[str, list[dict[str, Any]]]:
    indexed: Dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _safe_str(row.get(key))
        if value:
            indexed[value].append(row)
    return indexed


def _category_id_filter(categories: Iterable[dict[str, Any]]) -> Optional[str]:
    ids = [_safe_str(row.get("id")) for row in categories if _safe_str(row.get("id"))]
    return _in_filter(ids) if ids else None


def _team_id_filter(teams: Iterable[dict[str, Any]]) -> Optional[str]:
    ids = [_safe_str(row.get("id")) for row in teams if _safe_str(row.get("id"))]
    return _in_filter(ids) if ids else None


def _tournament_id_filter(tournaments: Iterable[dict[str, Any]]) -> Optional[str]:
    ids = [_safe_str(row.get("id")) for row in tournaments if _safe_str(row.get("id"))]
    return _in_filter(ids) if ids else None


async def tournament_soul_snapshot_v2(
    *,
    tournament_key: str = "all",
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    include_communications: bool = True,
    include_media: bool = True,
    limit: int = 250,
    client: Optional[SupabaseRestClient] = None,
) -> Dict[str, Any]:
    """Build the read-only tournament soul snapshot used by folders/assistant.

    The snapshot intentionally aggregates canonical tournament data but does not
    write anything. Missing optional copatelmex tables are reported in
    ``optional_sources`` instead of breaking the core operations snapshot.
    """

    max_limit = max(1, min(int(limit or 250), 1000))
    if client is None:
        client = SupabaseRestClient(load_tournaments_v2_config())

    if tournament_name and not tournament_slug:
        tournament_slug = tournament_name

    dataset = await _load_scope_dataset(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
    )
    tournaments = dataset.get("tournaments") or []
    categories = dataset.get("categories") or []
    teams = dataset.get("teams") or []
    registrations = dataset.get("registrations") or []
    players = dataset.get("players") or []

    tournaments_by_id = {_safe_str(row.get("id")): row for row in tournaments}
    categories_by_id = {_safe_str(row.get("id")): row for row in categories}
    teams_by_id = {_safe_str(row.get("id")): row for row in teams}
    players_by_registration = _index_many(players, "registration_id")

    team_ids_filter = _team_id_filter(teams)
    category_ids_filter = _category_id_filter(categories)
    tournament_ids_filter = _tournament_id_filter(tournaments)
    optional_sources: Dict[str, Dict[str, Any]] = {}

    managers: list[dict[str, Any]] = []
    if team_ids_filter:
        managers, error = await _optional_rows(
            client,
            table="team_managers",
            select_expr="id,team_id,first_name,last_name,email,phone,position,is_primary",
            filters={"team_id": team_ids_filter},
            order="is_primary.desc,created_at.asc",
            limit=max_limit,
        )
        optional_sources["team_managers"] = {
            "available": error is None,
            "rows": len(managers),
            "error": error,
        }
    managers_by_team = _index_many(managers, "team_id")

    matches: list[dict[str, Any]] = []
    if category_ids_filter:
        matches, error = await _optional_rows(
            client,
            table="matches",
            select_expr=(
                "id,category_id,home_team_id,away_team_id,match_date,field_number,"
                "phase,home_score,away_score,status,created_at"
            ),
            filters={"category_id": category_ids_filter},
            order="match_date.asc",
            limit=max_limit,
        )
        optional_sources["matches"] = {
            "available": error is None,
            "rows": len(matches),
            "error": error,
        }

    standings: list[dict[str, Any]] = []
    if category_ids_filter:
        standings, error = await _optional_rows(
            client,
            table="team_standings",
            select_expr=(
                "id,team_id,category_id,played,won,drawn,lost,goals_for,"
                "goals_against,goal_difference,points,updated_at"
            ),
            filters={"category_id": category_ids_filter},
            order="points.desc,goal_difference.desc,goals_for.desc",
            limit=max_limit,
        )
        optional_sources["team_standings"] = {
            "available": error is None,
            "rows": len(standings),
            "error": error,
        }

    cedulas: list[dict[str, Any]] = []
    match_ids = [_safe_str(row.get("id")) for row in matches if _safe_str(row.get("id"))]
    if match_ids:
        cedulas, error = await _optional_rows(
            client,
            table="match_cedulas",
            select_expr="id,match_id,referee_name,file_url,status,notes,created_at,updated_at",
            filters={"match_id": _in_filter(match_ids)},
            order="created_at.desc",
            limit=max_limit,
        )
        optional_sources["match_cedulas"] = {
            "available": error is None,
            "rows": len(cedulas),
            "error": error,
        }

    media: Dict[str, Any] = {
        "photos_count": 0,
        "videos_count": 0,
        "streams_count": 0,
        "recent_photos": [],
        "recent_videos": [],
        "upcoming_streams": [],
    }
    if include_media:
        photo_filters = {"category_id": category_ids_filter} if category_ids_filter else None
        photos, error = await _optional_rows(
            client,
            table="gallery_photos",
            select_expr="id,title,description,image_url,category_id,photo_date,created_at",
            filters=photo_filters,
            order="photo_date.desc",
            limit=50,
        )
        optional_sources["gallery_photos"] = {
            "available": error is None,
            "rows": len(photos),
            "error": error,
        }
        video_filters = {"category_id": category_ids_filter} if category_ids_filter else None
        videos, error = await _optional_rows(
            client,
            table="featured_videos",
            select_expr="id,title,description,video_url,thumbnail_url,video_type,category_id,created_at",
            filters=video_filters,
            order="created_at.desc",
            limit=50,
        )
        optional_sources["featured_videos"] = {
            "available": error is None,
            "rows": len(videos),
            "error": error,
        }
        streams, error = await _optional_rows(
            client,
            table="live_streams",
            select_expr="id,title,description,match_id,stream_url,platform,scheduled_time,status,created_at",
            order="scheduled_time.asc",
            limit=50,
        )
        optional_sources["live_streams"] = {
            "available": error is None,
            "rows": len(streams),
            "error": error,
        }
        media = {
            "photos_count": len(photos),
            "videos_count": len(videos),
            "streams_count": len(streams),
            "recent_photos": photos[:10],
            "recent_videos": videos[:10],
            "upcoming_streams": streams[:10],
        }

    communications: Dict[str, Any] = {
        "email_inbox_unread": 0,
        "emails_sent_recent": 0,
        "scheduled_emails_pending": 0,
        "whatsapp_unread": 0,
        "whatsapp_recent_messages": 0,
        "whatsapp_active_templates": 0,
    }
    if include_communications:
        inbox, error = await _optional_rows(
            client,
            table="email_inbox",
            select_expr="id,is_read,created_at",
            order="created_at.desc",
            limit=200,
        )
        optional_sources["email_inbox"] = {
            "available": error is None,
            "rows": len(inbox),
            "error": error,
        }
        sent, error = await _optional_rows(
            client,
            table="email_send_log",
            select_expr="id,status,recipient_count,created_at,tournament_id",
            filters={"tournament_id": tournament_ids_filter} if tournament_ids_filter else None,
            order="created_at.desc",
            limit=200,
        )
        optional_sources["email_send_log"] = {
            "available": error is None,
            "rows": len(sent),
            "error": error,
        }
        scheduled, error = await _optional_rows(
            client,
            table="scheduled_emails",
            select_expr="id,status,scheduled_at,created_at",
            order="scheduled_at.asc",
            limit=200,
        )
        optional_sources["scheduled_emails"] = {
            "available": error is None,
            "rows": len(scheduled),
            "error": error,
        }
        whatsapp_filters = {"team_id": team_ids_filter} if team_ids_filter else None
        if not whatsapp_filters and tournament_ids_filter:
            whatsapp_filters = {"tournament_id": tournament_ids_filter}
        whatsapp, error = await _optional_rows(
            client,
            table="whatsapp_message_log",
            select_expr="id,team_id,tournament_id,direction,status,is_read,sent_at,created_at",
            filters=whatsapp_filters,
            order="sent_at.desc",
            limit=200,
        )
        optional_sources["whatsapp_message_log"] = {
            "available": error is None,
            "rows": len(whatsapp),
            "error": error,
        }
        templates, error = await _optional_rows(
            client,
            table="whatsapp_templates",
            select_expr="id,is_active,approval_status,template_type,created_at",
            filters={"is_active": "eq.true"},
            order="created_at.desc",
            limit=100,
        )
        optional_sources["whatsapp_templates"] = {
            "available": error is None,
            "rows": len(templates),
            "error": error,
        }
        communications = {
            "email_inbox_unread": sum(1 for row in inbox if not bool(row.get("is_read"))),
            "emails_sent_recent": len(sent),
            "scheduled_emails_pending": sum(
                1 for row in scheduled if _safe_str(row.get("status")) == "pending"
            ),
            "whatsapp_unread": sum(
                1
                for row in whatsapp
                if not bool(row.get("is_read")) and _safe_str(row.get("direction")) == "incoming"
            ),
            "whatsapp_recent_messages": len(whatsapp),
            "whatsapp_active_templates": len(templates),
        }

    cedulas_by_match = _index_many(cedulas, "match_id")

    entity_rows: Dict[str, Dict[str, Any]] = {}
    category_rows: Dict[str, Dict[str, Any]] = {}
    branch_rows: Dict[str, Dict[str, Any]] = {}
    incomplete_document_teams = 0
    verified_players = 0
    complete_document_players = 0

    for reg in registrations:
        team = teams_by_id.get(_safe_str(reg.get("team_id")))
        category = categories_by_id.get(_safe_str(reg.get("category_id"))) or {}
        if not team:
            continue
        players_for_reg = players_by_registration.get(_safe_str(reg.get("id")), [])
        category_name = _safe_str(category.get("name")) or "(sin categoria)"
        branch = _safe_str(category.get("branch")) or _infer_gender(
            tournament_name=_safe_str(
                (tournaments_by_id.get(_safe_str(team.get("tournament_id"))) or {}).get("name")
            ),
            tournament_slug=_safe_str(
                (tournaments_by_id.get(_safe_str(team.get("tournament_id"))) or {}).get("slug")
            ),
            category_name=category_name,
        ) or "(sin rama)"
        entity_name = _safe_str(team.get("state")) or "(sin entidad)"
        player_count = len(players_for_reg)
        complete_docs = sum(1 for row in players_for_reg if bool(row.get("documents_complete")))
        verified_docs = sum(1 for row in players_for_reg if bool(row.get("documents_verified")))
        complete_document_players += complete_docs
        verified_players += verified_docs
        if player_count and complete_docs < player_count:
            incomplete_document_teams += 1

        entity = entity_rows.setdefault(
            entity_name,
            {
                "entity_name": entity_name,
                "teams_count": 0,
                "players_count": 0,
                "documents_complete_players": 0,
                "documents_verified_players": 0,
                "categories": set(),
                "branches": set(),
                "teams": [],
            },
        )
        entity["teams_count"] += 1
        entity["players_count"] += player_count
        entity["documents_complete_players"] += complete_docs
        entity["documents_verified_players"] += verified_docs
        entity["categories"].add(category_name)
        entity["branches"].add(branch)
        team_managers = managers_by_team.get(_safe_str(team.get("id")), [])
        primary_manager = next((row for row in team_managers if bool(row.get("is_primary"))), None)
        entity["teams"].append(
            {
                "team_id": _safe_str(team.get("id")),
                "team_name": team.get("team_name"),
                "status": team.get("status"),
                "category": category_name,
                "branch": branch if branch != "(sin rama)" else None,
                "players_count": player_count,
                "documents_complete_players": complete_docs,
                "documents_verified_players": verified_docs,
                "instagram_url": team.get("instagram_url"),
                "facebook_url": team.get("facebook_url"),
                "shield_url": team.get("shield_url"),
                "primary_manager": (
                    {
                        "name": " ".join(
                            part
                            for part in [
                                _safe_str(primary_manager.get("first_name")),
                                _safe_str(primary_manager.get("last_name")),
                            ]
                            if part
                        ).strip(),
                        "email": primary_manager.get("email"),
                        "phone": primary_manager.get("phone"),
                    }
                    if primary_manager
                    else None
                ),
            }
        )

        category_bucket = category_rows.setdefault(
            category_name,
            {"category": category_name, "teams_count": 0, "players_count": 0},
        )
        category_bucket["teams_count"] += 1
        category_bucket["players_count"] += player_count

        branch_bucket = branch_rows.setdefault(
            branch,
            {"branch": branch, "teams_count": 0, "players_count": 0},
        )
        branch_bucket["teams_count"] += 1
        branch_bucket["players_count"] += player_count

    entities = []
    for entity in entity_rows.values():
        entities.append(
            {
                **entity,
                "categories": sorted(entity["categories"]),
                "branches": sorted(entity["branches"]),
                "teams": sorted(
                    entity["teams"],
                    key=lambda row: _normalize_text(row.get("team_name")),
                )[:25],
            }
        )
    entities.sort(key=lambda row: (row["players_count"], row["teams_count"]), reverse=True)

    match_rows = []
    for match in matches[:max_limit]:
        home = teams_by_id.get(_safe_str(match.get("home_team_id"))) or {}
        away = teams_by_id.get(_safe_str(match.get("away_team_id"))) or {}
        category = categories_by_id.get(_safe_str(match.get("category_id"))) or {}
        match_rows.append(
            {
                "match_id": _safe_str(match.get("id")),
                "category": category.get("name"),
                "home_team": home.get("team_name"),
                "away_team": away.get("team_name"),
                "match_date": match.get("match_date"),
                "field_number": match.get("field_number"),
                "phase": match.get("phase"),
                "status": match.get("status"),
                "score": {
                    "home": match.get("home_score"),
                    "away": match.get("away_score"),
                },
                "cedula_status": (
                    cedulas_by_match.get(_safe_str(match.get("id")), [{}])[0].get("status")
                    if cedulas_by_match.get(_safe_str(match.get("id")))
                    else None
                ),
            }
        )

    standings_rows = []
    for row in standings[:max_limit]:
        team = teams_by_id.get(_safe_str(row.get("team_id"))) or {}
        category = categories_by_id.get(_safe_str(row.get("category_id"))) or {}
        standings_rows.append(
            {
                "team_id": _safe_str(row.get("team_id")),
                "team_name": team.get("team_name"),
                "category": category.get("name"),
                "played": row.get("played"),
                "won": row.get("won"),
                "drawn": row.get("drawn"),
                "lost": row.get("lost"),
                "points": row.get("points"),
                "goal_difference": row.get("goal_difference"),
            }
        )

    tournament_rows = [
        {
            "id": _safe_str(row.get("id")),
            "name": row.get("name"),
            "slug": row.get("slug"),
            "is_active": row.get("is_active"),
            "start_date": row.get("start_date"),
            "end_date": row.get("end_date"),
        }
        for row in tournaments
    ]

    return {
        "ok": True,
        "source": "supabase_tournaments_v2",
        "snapshot_type": "tournament_soul",
        "filters": {
            "tournament_key": tournament_key,
            "tournament_slug": tournament_slug,
            "tournament_name": tournament_name,
            "include_communications": include_communications,
            "include_media": include_media,
            "limit": max_limit,
        },
        "tournaments": tournament_rows,
        "summary": {
            "tournaments_count": len(tournaments),
            "entities_count": len(entities),
            "teams_count": len({_safe_str(row.get("id")) for row in teams}),
            "registrations_count": len(registrations),
            "players_count": len(players),
            "categories_count": len(categories),
            "matches_count": len(matches),
            "standings_rows_count": len(standings),
            "document_players_complete": complete_document_players,
            "document_players_verified": verified_players,
            "teams_with_incomplete_documents": incomplete_document_teams,
        },
        "breakdowns": {
            "entities": entities[:max_limit],
            "categories": sorted(
                category_rows.values(),
                key=lambda row: (row["players_count"], row["teams_count"]),
                reverse=True,
            ),
            "branches": sorted(
                branch_rows.values(),
                key=lambda row: (row["players_count"], row["teams_count"]),
                reverse=True,
            ),
        },
        "operations": {
            "matches": match_rows,
            "standings": standings_rows,
            "cedulas_count": len(cedulas),
        },
        "marketing": {
            "media": media,
            "team_marketing_profiles_count": sum(
                1
                for row in teams
                if _safe_str(row.get("instagram_url"))
                or _safe_str(row.get("facebook_url"))
                or _safe_str(row.get("shield_url"))
            ),
        },
        "communications": communications,
        "optional_sources": optional_sources,
        "notes": [
            "Snapshot read-only para articular paginas publicas por torneo, carpetas, operaciones y asistente.",
            "La UI publica puede separarse por slug; el backend permanece compartido por tournament_id/scope.",
            "Las fuentes opcionales no bloquean el snapshot si la tabla aun no existe o no tiene columnas esperadas.",
        ],
    }
