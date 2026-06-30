from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from typing import Any, Dict, List, Optional

from devnous.tournaments.core.supabase_sync import (
    SupabaseAdminClient,
    SupabaseConfig,
)

from ..config import load_tournaments_v2_config
from ..supabase_client import SupabaseRestClient, TournamentsV2Error
from .queries import (
    _safe_str,
    resolve_category_for_tournament,
    resolve_primary_tournament,
    resolve_team_for_tournament,
)


def _parse_date_text(value: Any) -> Optional[str]:
    raw = _safe_str(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    raw = _safe_str(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("Invalid date format; use YYYY-MM-DD or DD/MM/YYYY")


def _parse_time(value: Optional[str]) -> time:
    raw = (value or "").strip() or "09:00"
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError("Invalid time format; use HH:MM")


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _safe_str(value).lower()).strip("-") or "torneo"


def _is_missing_column_error(exc: Exception, *columns: str) -> bool:
    message = _safe_str(exc).lower()
    if "column" not in message or "does not exist" not in message:
        return False
    return any(str(column or "").strip().lower() in message for column in columns)


def _normalize_field_numbers(
    *,
    field_number: Optional[str],
    field_numbers: Optional[List[str]],
) -> List[str]:
    result: List[str] = []
    if field_numbers:
        for raw in field_numbers:
            value = str(raw or "").strip()
            if value and value not in result:
                result.append(value)
    single = (field_number or "").strip()
    if single and single not in result:
        result.insert(0, single)
    if not result:
        result = ["1"]
    return result


def _matches_window_scope(
    *,
    window: Dict[str, Any],
    category_id: str,
    category_name: Optional[str],
    category_gender: Optional[str] = None,
) -> bool:
    wid = str(window.get("category_id") or "").strip()
    wname = str(window.get("category_name") or "").strip().lower()
    wgender = str(window.get("gender") or "").strip().lower()
    if wid and wid != category_id:
        return False
    if wname:
        current = (category_name or "").strip().lower()
        if wname not in current and current not in wname:
            return False
    if wgender:
        current_gender = (category_gender or "").strip().lower()
        if current_gender and wgender != current_gender:
            return False
    return True


def _normalize_windows(
    *,
    daily_start_time: time,
    daily_end_time: Optional[time],
    category_windows: Optional[List[Dict[str, Any]]],
    category_id: str,
    category_name: Optional[str],
) -> List[tuple[time, Optional[time]]]:
    windows: List[tuple[time, Optional[time]]] = []
    for raw in category_windows or []:
        if not isinstance(raw, dict):
            continue
        if not _matches_window_scope(
            window=raw,
            category_id=category_id,
            category_name=category_name,
        ):
            continue
        start_raw = str(raw.get("start_time") or "").strip()
        if not start_raw:
            continue
        start_t = _parse_time(start_raw)
        end_raw = str(raw.get("end_time") or "").strip()
        end_t = _parse_time(end_raw) if end_raw else None
        if end_t and end_t <= start_t:
            raise ValueError("Each category window must satisfy end_time > start_time")
        windows.append((start_t, end_t))
    if not windows:
        windows = [(daily_start_time, daily_end_time)]
    return windows


def _window_slot_capacity(
    *,
    start_t: time,
    end_t: Optional[time],
    interval_minutes: int,
) -> Optional[int]:
    if end_t is None:
        return None
    start_m = start_t.hour * 60 + start_t.minute
    end_m = end_t.hour * 60 + end_t.minute
    if end_m < start_m:
        return 0
    return int((end_m - start_m) // interval_minutes) + 1


def _build_slot_generator(
    *,
    start_date: date,
    fields: List[str],
    games_per_day: int,
    interval_minutes: int,
    windows: List[tuple[time, Optional[time]]],
):
    cursor_day = 0
    cursor_slot = 0

    def _next_slot() -> tuple[datetime, str]:
        nonlocal cursor_day, cursor_slot
        while True:
            slot_idx = cursor_slot
            field_idx = slot_idx % len(fields)
            round_idx = slot_idx // len(fields)
            remaining = round_idx
            target_dt: Optional[datetime] = None
            for start_t, end_t in windows:
                cap = _window_slot_capacity(
                    start_t=start_t,
                    end_t=end_t,
                    interval_minutes=interval_minutes,
                )
                if cap is None:
                    target_dt = datetime.combine(
                        start_date + timedelta(days=cursor_day),
                        start_t,
                    ) + timedelta(minutes=remaining * interval_minutes)
                    break
                if remaining < cap:
                    target_dt = datetime.combine(
                        start_date + timedelta(days=cursor_day),
                        start_t,
                    ) + timedelta(minutes=remaining * interval_minutes)
                    break
                remaining -= cap
            if target_dt is None:
                cursor_day += 1
                cursor_slot = 0
                continue

            field_value = fields[field_idx]
            cursor_slot += 1
            if cursor_slot >= games_per_day:
                cursor_day += 1
                cursor_slot = 0
            return target_dt, field_value

    return _next_slot


def _round_robin_pairings(team_ids: List[str]) -> List[List[str]]:
    teams = list(team_ids)
    if len(teams) < 2:
        return []
    if len(teams) % 2 == 1:
        teams.append("__BYE__")
    n = len(teams)
    rounds = n - 1
    half = n // 2
    pairings: List[List[str]] = []
    rotating = teams[:]
    for _ in range(rounds):
        left = rotating[:half]
        right = list(reversed(rotating[half:]))
        for a, b in zip(left, right):
            if a == "__BYE__" or b == "__BYE__":
                continue
            pairings.append([a, b])
        rotating = [rotating[0], rotating[-1], *rotating[1:-1]]
    return pairings


def _pick_value(row: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in {None, ""}:
            return row.get(key)
    return None


def _player_identity_key(
    *,
    first_name: Any,
    last_name: Any,
    birth_date: Any,
) -> tuple[str, str, str]:
    return (
        _safe_str(first_name).strip().lower(),
        _safe_str(last_name).strip().lower(),
        _safe_str(birth_date),
    )


def _normalize_player_update_payload(
    updates: Dict[str, Any],
) -> tuple[Dict[str, Any], List[str]]:
    payload: Dict[str, Any] = {}
    local_only: List[str] = []
    for key, value in dict(updates or {}).items():
        if value in (None, ""):
            continue
        if key == "full_name":
            full_name = _safe_str(value)
            parts = [part for part in full_name.split() if part]
            if len(parts) < 2:
                raise ValueError("full_name requires at least name and surname")
            payload["first_name"] = parts[0]
            payload["last_name"] = " ".join(parts[1:])
            continue
        if key == "birth_date":
            parsed = _parse_date_text(value)
            if not parsed:
                raise ValueError("birth_date must use YYYY-MM-DD or DD/MM/YYYY")
            payload["birth_date"] = parsed
            continue
        if key == "curp":
            payload["curp"] = _safe_str(value).upper() or None
            continue
        if key == "email":
            payload["email"] = _safe_str(value) or None
            continue
        if key == "documents_complete":
            payload["documents_complete"] = bool(value)
            continue
        if key == "documents_verified":
            payload["documents_verified"] = bool(value)
            continue
        local_only.append(str(key))
    return payload, local_only


def _normalize_branch(value: Any) -> Optional[str]:
    raw = _safe_str(value).lower()
    if not raw:
        return None
    if "fem" in raw:
        return "femenil"
    if "mix" in raw:
        return "mixto"
    if "var" in raw or "masc" in raw:
        return "varonil"
    return raw or None


def _normalize_category_base(value: Any) -> str:
    text = _safe_str(value).lower()
    for token in ("femenil", "femenino", "varonil", "masculino", "mixto", "rama"):
        text = text.replace(token, " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _category_match_score(
    *,
    candidate_name: Any,
    target_name: Optional[str],
    current_name: Optional[str],
    target_branch: Optional[str],
    candidate_branch: Optional[str],
) -> int:
    candidate_norm = _safe_str(candidate_name).lower()
    target_norm = _safe_str(target_name).lower()
    current_norm = _safe_str(current_name).lower()
    score = 0
    if target_branch and _normalize_branch(candidate_branch) == _normalize_branch(
        target_branch
    ):
        score += 30
    if target_norm:
        if candidate_norm == target_norm:
            score += 100
        elif target_norm in candidate_norm or candidate_norm in target_norm:
            score += 60
    current_base = _normalize_category_base(current_norm)
    candidate_base = _normalize_category_base(candidate_norm)
    target_base = _normalize_category_base(target_norm)
    if target_base and candidate_base == target_base:
        score += 80
    elif current_base and candidate_base == current_base:
        score += 70
    elif target_base and target_base in candidate_base:
        score += 35
    elif current_base and current_base in candidate_base:
        score += 25
    return score


async def _resolve_registration_context(
    client: SupabaseRestClient,
    *,
    team_id: str,
    tournament_id: str,
    current_category_id: Optional[str] = None,
    current_category_name: Optional[str] = None,
) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    registrations = await client.fetch_all_rows(
        table="registrations",
        select_expr="id,team_id,category_id,registration_date,payment_status",
        filters={"team_id": f"eq.{team_id}"},
        order="registration_date.desc",
    )
    if not registrations:
        raise ValueError("Registration not found for team in Supabase")
    category_rows = await client.select_rows(
        table="categories",
        select_expr="id,name,tournament_id,branch",
        filters={"tournament_id": f"eq.{tournament_id}"},
        order="name.asc",
        limit=200,
    )
    categories_by_id = {_safe_str(row.get("id")): row for row in category_rows}
    selected = None
    current_category_id_value = _safe_str(current_category_id)
    current_category_name_value = _safe_str(current_category_name)
    if current_category_id_value:
        for reg in registrations:
            if _safe_str(reg.get("category_id")) == current_category_id_value:
                selected = reg
                break
    if selected is None and current_category_name_value:
        current_name_norm = current_category_name_value.lower()
        for reg in registrations:
            category = categories_by_id.get(_safe_str(reg.get("category_id"))) or {}
            category_name_norm = _safe_str(category.get("name")).lower()
            if (
                category_name_norm == current_name_norm
                or current_name_norm in category_name_norm
            ):
                selected = reg
                break
    if selected is None and len(registrations) == 1:
        selected = registrations[0]
    if selected is None:
        raise ValueError(
            "Multiple registrations found; current category is required to change category/rama"
        )
    return selected, categories_by_id, registrations


def _resolve_target_category_from_candidates(
    *,
    categories: List[Dict[str, Any]],
    current_category: Dict[str, Any],
    target_category_name: Optional[str],
    target_branch: Optional[str],
) -> Dict[str, Any]:
    normalized_branch = _normalize_branch(target_branch)
    if target_category_name:
        target_name_norm = _safe_str(target_category_name).lower()
        exact_matches = [
            row
            for row in categories
            if _safe_str(row.get("name")).lower() == target_name_norm
        ]
        if normalized_branch:
            exact_matches = [
                row
                for row in exact_matches
                if _normalize_branch(row.get("branch")) == normalized_branch
            ] or exact_matches
        if exact_matches:
            return exact_matches[0]

    filtered = list(categories)
    if normalized_branch:
        branch_matches = [
            row
            for row in filtered
            if _normalize_branch(row.get("branch")) == normalized_branch
        ]
        if branch_matches:
            filtered = branch_matches
    if not filtered:
        raise ValueError("No target category found for the requested rama/categoria")

    ranked = sorted(
        filtered,
        key=lambda row: (
            _category_match_score(
                candidate_name=row.get("name"),
                target_name=target_category_name,
                current_name=current_category.get("name"),
                target_branch=normalized_branch,
                candidate_branch=row.get("branch"),
            ),
            _safe_str(row.get("name")),
        ),
        reverse=True,
    )
    best = ranked[0]
    if (
        _category_match_score(
            candidate_name=best.get("name"),
            target_name=target_category_name,
            current_name=current_category.get("name"),
            target_branch=normalized_branch,
            candidate_branch=best.get("branch"),
        )
        <= 0
    ):
        raise ValueError(
            "Could not infer an equivalent target category for the requested rama/categoria"
        )
    return best


async def register_team_from_roster_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    category_name: Optional[str] = None,
    team_name: str,
    state: Optional[str] = None,
    country: str = "Mexico",
    phone_country_code: str = "+52",
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    payment_status: str = "pending",
    notes: Optional[str] = None,
    representative_name: Optional[str] = None,
    representative_email: Optional[str] = None,
    representative_phone: Optional[str] = None,
    municipality: Optional[str] = None,
    players: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")

    team_name_value = _safe_str(team_name)
    roster = list(players or [])
    if not team_name_value:
        raise ValueError("team_name is required")
    if not roster:
        raise ValueError("players is required and must contain at least one player")

    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))

    category_row = await resolve_category_for_tournament(
        client,
        tournament_id=tournament_id,
        category_id=category_id,
        category_name=category_name,
    )
    category_id_value = _safe_str(category_row.get("id"))
    category_name_value = _safe_str(category_row.get("name"))

    admin = SupabaseAdminClient(
        SupabaseConfig(
            url=config.supabase_url,
            service_role_key=config.service_role_key,
        ),
        cache_dir="data",
    )
    import_user_id = _safe_str(user_id) or await admin.ensure_import_user()

    representative_phone_value = (
        _safe_str(representative_phone or phone_number) or "5500000000"
    )
    representative_email_value = _safe_str(representative_email) or "pendiente@sam.chat"
    team_rows: List[Dict[str, Any]] = []
    try:
        team_rows = [
            await resolve_team_for_tournament(
                client,
                tournament_id=tournament_id,
                team_name=team_name_value,
                user_id=import_user_id,
            )
        ]
    except Exception:
        team_rows = []
    team_payload = {
        "user_id": import_user_id,
        "team_name": team_name_value,
        "academy_name": None,
        "municipality": _safe_str(municipality) or None,
        "state": _safe_str(state) or "No especificado",
        "country": _safe_str(country) or "Mexico",
        "phone_country_code": _safe_str(phone_country_code) or "+52",
        "phone_number": _safe_str(phone_number or representative_phone) or "5500000000",
        "status": "pending",
        "tournament_id": tournament_id,
    }

    registration_payload = {
        "team_id": None,
        "category_id": category_id_value,
        "payment_status": _safe_str(payment_status) or "pending",
        "notes": _safe_str(notes) or None,
    }

    player_payloads: List[Dict[str, Any]] = []
    for idx, raw in enumerate(roster, start=1):
        if not isinstance(raw, dict):
            continue
        first_name = (
            _safe_str(_pick_value(raw, ["first_name", "nombre", "nombres", "name"]))
            or "Jugador"
        )
        last_name = (
            _safe_str(_pick_value(raw, ["last_name", "apellido", "apellidos"]))
            or f"#{idx}"
        )
        parent_name = (
            _safe_str(
                _pick_value(
                    raw, ["parent_name", "tutor", "nombre_tutor", "representante"]
                )
            )
            or _safe_str(representative_name)
            or "Tutor pendiente"
        )
        parent_email = (
            _safe_str(
                _pick_value(
                    raw, ["parent_email", "correo", "email", "correo_electronico"]
                )
            )
            or representative_email_value
        )
        parent_phone = (
            _safe_str(_pick_value(raw, ["parent_phone", "telefono", "celular"]))
            or representative_phone_value
        )
        birth_date = (
            _safe_str(
                _pick_value(
                    raw, ["birth_date", "fecha_nacimiento", "nacimiento", "fecha"]
                )
            )
            or "2012-01-01"
        )
        player_payloads.append(
            {
                "registration_id": None,
                "first_name": first_name,
                "last_name": last_name,
                "birth_date": birth_date,
                "parent_name": parent_name,
                "parent_email": parent_email,
                "parent_phone": parent_phone,
                "curp": _safe_str(_pick_value(raw, ["curp"])) or None,
                "paternal_surname": _safe_str(
                    _pick_value(raw, ["paternal_surname", "apellido_paterno"])
                )
                or None,
                "maternal_surname": _safe_str(
                    _pick_value(raw, ["maternal_surname", "apellido_materno"])
                )
                or None,
                "jersey_number": _pick_value(raw, ["jersey_number", "numero", "dorsal"])
                or idx,
                "position": _safe_str(_pick_value(raw, ["position", "posicion"]))
                or None,
                "documents_complete": False,
                "documents_verified": False,
            }
        )

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "resolved_tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "resolved_category": {
                "id": category_id_value,
                "name": category_name_value,
            },
            "team_payload": team_payload,
            "registration_payload": registration_payload,
            "players_preview": player_payloads[:5],
            "players_count": len(player_payloads),
        }

    if team_rows:
        team = team_rows[0]
    else:
        try:
            inserted_teams = await client.insert_rows(
                table="teams", payload=team_payload
            )
        except TournamentsV2Error as exc:
            if not _is_missing_column_error(exc, "municipality"):
                raise
            fallback_team_payload = dict(team_payload)
            fallback_team_payload.pop("municipality", None)
            inserted_teams = await client.insert_rows(
                table="teams", payload=fallback_team_payload
            )
        if not inserted_teams:
            raise ValueError("Supabase team insert returned empty response")
        team = inserted_teams[0]

    registration_payload["team_id"] = _safe_str(team.get("id"))
    registrations = await client.insert_rows(
        table="registrations",
        payload=registration_payload,
        on_conflict="team_id,category_id",
        merge_duplicates=True,
    )
    if not registrations:
        raise ValueError("Supabase registration upsert returned empty response")
    registration = registrations[0]

    existing_managers = await client.select_rows(
        table="team_managers",
        select_expr="id,email,is_primary",
        filters={"team_id": f"eq.{_safe_str(team.get('id'))}"},
        limit=20,
    )
    if (
        representative_name
        and representative_email_value
        and not any(
            _safe_str(row.get("email")).lower() == representative_email_value.lower()
            for row in existing_managers
        )
    ):
        first_name = representative_name.strip().split()[0]
        last_name = " ".join(representative_name.strip().split()[1:]) or "Representante"
        await client.insert_rows(
            table="team_managers",
            payload={
                "team_id": _safe_str(team.get("id")),
                "first_name": first_name,
                "last_name": last_name,
                "email": representative_email_value,
                "phone": representative_phone_value,
                "position": "Representante",
                "is_primary": not existing_managers,
            },
        )

    registration_id = _safe_str(registration.get("id"))
    created_payloads: List[Dict[str, Any]] = []
    skipped = 0
    for player_payload in player_payloads:
        curp = _safe_str(player_payload.get("curp"))
        if curp:
            existing = await client.select_rows(
                table="players",
                select_expr="id,curp",
                filters={"curp": f"eq.{curp}"},
                limit=1,
            )
            if existing:
                skipped += 1
                continue
        row = dict(player_payload)
        row["registration_id"] = registration_id
        created_payloads.append(row)

    inserted_players = []
    if created_payloads:
        inserted_players = await client.insert_rows(
            table="players", payload=created_payloads
        )

    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {
            "id": category_id_value,
            "name": category_name_value,
        },
        "team": {
            "id": _safe_str(team.get("id")),
            "team_name": team.get("team_name"),
        },
        "registration": {"id": registration_id},
        "players_created": len(inserted_players),
        "players_skipped": skipped,
        "player_ids": [
            _safe_str(row.get("id"))
            for row in inserted_players
            if _safe_str(row.get("id"))
        ][:100],
        "team_preexisting": bool(team_rows),
    }


async def append_players_to_team_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    category_name: Optional[str] = None,
    team_id: Optional[str] = None,
    team_name: Optional[str] = None,
    representative_name: Optional[str] = None,
    representative_email: Optional[str] = None,
    representative_phone: Optional[str] = None,
    players: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")

    roster = list(players or [])
    if not roster:
        raise ValueError("players is required and must contain at least one player")

    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))

    category_row = await resolve_category_for_tournament(
        client,
        tournament_id=tournament_id,
        category_id=category_id,
        category_name=category_name,
    )
    category_id_value = _safe_str(category_row.get("id"))
    category_name_value = _safe_str(category_row.get("name"))

    team = await resolve_team_for_tournament(
        client,
        tournament_id=tournament_id,
        team_id=team_id,
        team_name=team_name,
    )
    team_id_value = _safe_str(team.get("id"))

    registrations = await client.insert_rows(
        table="registrations",
        payload={
            "team_id": team_id_value,
            "category_id": category_id_value,
            "payment_status": "pending",
            "notes": "Actualizado via Telegram OCR (back side)",
        },
        on_conflict="team_id,category_id",
        merge_duplicates=True,
    )
    if not registrations:
        raise ValueError("Supabase registration upsert returned empty response")
    registration = registrations[0]
    registration_id = _safe_str(registration.get("id"))

    representative_phone_value = (
        re.sub(r"\D", "", _safe_str(representative_phone)) or "0000000000"
    )
    representative_phone_value = representative_phone_value[:10].rjust(10, "0")
    representative_email_value = _safe_str(representative_email) or "no-email@sam.chat"
    representative_name_value = _safe_str(representative_name) or "Tutor pendiente"

    existing_players = await client.fetch_all_rows(
        table="players",
        select_expr="id,curp,first_name,last_name,birth_date,registration_id",
        filters={"registration_id": f"eq.{registration_id}"},
        order="created_at.asc",
    )
    existing_curps = {
        _safe_str(row.get("curp")).upper()
        for row in existing_players
        if _safe_str(row.get("curp"))
    }
    existing_identities = {
        _player_identity_key(
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            birth_date=row.get("birth_date"),
        )
        for row in existing_players
    }

    player_payloads: List[Dict[str, Any]] = []
    skipped = 0
    for idx, raw in enumerate(roster, start=1):
        if not isinstance(raw, dict):
            continue
        first_name = (
            _safe_str(_pick_value(raw, ["first_name", "nombre", "nombres", "name"]))
            or "Jugador"
        )
        last_name = _safe_str(_pick_value(raw, ["last_name", "apellido", "apellidos"]))
        paternal = _safe_str(_pick_value(raw, ["paternal_surname", "apellido_paterno"]))
        maternal = _safe_str(_pick_value(raw, ["maternal_surname", "apellido_materno"]))
        if not last_name and (paternal or maternal):
            last_name = (paternal or maternal).strip()
        if not last_name:
            last_name = f"#{idx}"
        birth_date = (
            _parse_date_text(
                _pick_value(
                    raw, ["birth_date", "fecha_nacimiento", "nacimiento", "fecha"]
                )
            )
            or "2012-01-01"
        )
        curp = _safe_str(_pick_value(raw, ["curp"])).upper() or None
        identity = _player_identity_key(
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
        )
        if curp and curp in existing_curps:
            skipped += 1
            continue
        if identity in existing_identities:
            skipped += 1
            continue
        existing_identities.add(identity)
        if curp:
            existing_curps.add(curp)

        jersey_number_raw = _pick_value(raw, ["jersey_number", "numero", "dorsal"])
        try:
            jersey_number = (
                int(str(jersey_number_raw).strip())
                if jersey_number_raw not in (None, "")
                else idx
            )
        except ValueError:
            jersey_number = idx

        player_payloads.append(
            {
                "registration_id": registration_id,
                "first_name": first_name,
                "last_name": last_name,
                "birth_date": birth_date,
                "parent_name": _safe_str(
                    _pick_value(
                        raw, ["parent_name", "tutor", "nombre_tutor", "representante"]
                    )
                )
                or representative_name_value,
                "parent_email": _safe_str(
                    _pick_value(
                        raw, ["parent_email", "correo", "email", "correo_electronico"]
                    )
                )
                or representative_email_value,
                "parent_phone": _safe_str(
                    _pick_value(raw, ["parent_phone", "telefono", "celular"])
                )
                or representative_phone_value,
                "curp": curp,
                "paternal_surname": paternal or None,
                "maternal_surname": maternal or None,
                "jersey_number": jersey_number,
                "position": _safe_str(_pick_value(raw, ["position", "posicion"]))
                or None,
                "documents_complete": False,
                "documents_verified": False,
            }
        )

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": category_id_value, "name": category_name_value},
            "team": {"id": team_id_value, "team_name": team.get("team_name")},
            "registration": {"id": registration_id},
            "players_count": len(player_payloads),
            "players_preview": player_payloads[:5],
            "players_skipped": skipped,
        }

    inserted_players: List[Dict[str, Any]] = []
    if player_payloads:
        inserted_players = await client.insert_rows(
            table="players", payload=player_payloads
        )

    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": category_id_value, "name": category_name_value},
        "team": {"id": team_id_value, "team_name": team.get("team_name")},
        "registration": {"id": registration_id},
        "players_created": len(inserted_players),
        "players_skipped": skipped,
        "player_ids": [
            _safe_str(row.get("id"))
            for row in inserted_players
            if _safe_str(row.get("id"))
        ][:100],
    }


async def schedule_create_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    phase: str = "Fase estatal",
    start_date: str,
    kickoff_time: str = "09:00",
    games_per_day: int = 4,
    interval_minutes: int = 90,
    field_number: Optional[str] = None,
    field_numbers: Optional[List[str]] = None,
    infinite_fields: bool = False,
    daily_start_time: Optional[str] = None,
    daily_end_time: Optional[str] = None,
    category_windows: Optional[List[Dict[str, Any]]] = None,
    status: str = "scheduled",
    replace_existing_phase: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")
    if games_per_day < 1 or games_per_day > 20:
        raise ValueError("games_per_day must be between 1 and 20")
    if interval_minutes < 30 or interval_minutes > 600:
        raise ValueError("interval_minutes must be between 30 and 600")

    phase_value = (phase or "").strip() or "Fase estatal"
    status_value = (status or "scheduled").strip().lower()
    if status_value not in {
        "scheduled",
        "in_progress",
        "live",
        "finished",
        "completed",
    }:
        raise ValueError(
            "status must be one of: scheduled, in_progress, live, finished, completed"
        )

    start_d = _parse_date(start_date)
    if not start_d:
        raise ValueError("start_date is required")
    kickoff_t = _parse_time(kickoff_time)
    day_start_t = _parse_time(daily_start_time or kickoff_time)
    day_end_t = _parse_time(daily_end_time) if _safe_str(daily_end_time) else None
    if day_end_t and day_end_t <= day_start_t:
        raise ValueError("daily_end_time must be later than daily_start_time")

    fields = _normalize_field_numbers(
        field_number=field_number, field_numbers=field_numbers
    )
    if infinite_fields:
        fields = [f"INF-{i+1}" for i in range(max(1, games_per_day))]
    elif not (field_number or (field_numbers or [])):
        raise ValueError(
            "Necesito canchas disponibles (field_numbers/field_number) "
            "o indica infinite_fields=true."
        )
    if games_per_day < len(fields):
        raise ValueError("games_per_day must be >= number of fields")

    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))

    cat_id = _safe_str(category_id)
    category_row = await resolve_category_for_tournament(
        client,
        tournament_id=tournament_id,
        category_id=cat_id,
    )
    cat_id = _safe_str(category_row.get("id"))
    category_name_value = category_row.get("name") or None
    windows = _normalize_windows(
        daily_start_time=day_start_t,
        daily_end_time=day_end_t,
        category_windows=category_windows,
        category_id=cat_id,
        category_name=category_name_value,
    )

    regs = await client.fetch_all_rows(
        table="registrations",
        select_expr="team_id,category_id",
        filters={"category_id": f"eq.{cat_id}"},
    )
    reg_team_ids = sorted(
        {
            str((r or {}).get("team_id") or "").strip()
            for r in regs
            if (r or {}).get("team_id")
        }
    )
    team_ids: List[str] = []
    if reg_team_ids:
        for i in range(0, len(reg_team_ids), 200):
            chunk = reg_team_ids[i : i + 200]
            teams = await client.select_rows(
                table="teams",
                select_expr="id,tournament_id,team_name",
                filters={
                    "id": f"in.({','.join(chunk)})",
                    "tournament_id": f"eq.{tournament_id}",
                },
                limit=200,
            )
            team_ids.extend([str(t.get("id")) for t in teams if t.get("id")])
    else:
        teams = await client.fetch_all_rows(
            table="teams",
            select_expr="id",
            filters={"tournament_id": f"eq.{tournament_id}"},
            order="created_at.asc",
        )
        team_ids = [str(t.get("id")) for t in teams if t.get("id")]
    team_ids = list(dict.fromkeys(team_ids))
    if len(team_ids) < 2:
        raise ValueError("Need at least 2 teams to generate schedule")

    pairings = _round_robin_pairings(team_ids)
    rows: List[Dict[str, Any]] = []
    _next_slot = _build_slot_generator(
        start_date=start_d,
        fields=fields,
        games_per_day=games_per_day,
        interval_minutes=interval_minutes,
        windows=windows,
    )
    for home_id, away_id in pairings:
        dt, field_value = _next_slot()
        match_dt = (
            dt.replace(hour=kickoff_t.hour, minute=kickoff_t.minute)
            if day_start_t == kickoff_t
            else dt
        )
        rows.append(
            {
                "tournament_id": tournament_id,
                "category_id": cat_id,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "match_date": match_dt.isoformat(),
                "field_number": field_value,
                "phase": phase_value,
                "status": status_value,
                "home_score": 0 if status_value in {"finished", "completed"} else None,
                "away_score": 0 if status_value in {"finished", "completed"} else None,
            }
        )

    existing_count = 0
    if replace_existing_phase:
        existing = await client.fetch_all_rows(
            table="matches",
            select_expr="id",
            filters={
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
                "phase": f"eq.{phase_value}",
            },
        )
        existing_count = len(existing or [])

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": cat_id, "name": category_name_value},
            "phase": phase_value,
            "status": status_value,
            "replace_existing_phase": replace_existing_phase,
            "existing_matches_in_phase": existing_count,
            "teams_count": len(team_ids),
            "fields": fields,
            "infinite_fields": bool(infinite_fields),
            "daily_start_time": day_start_t.strftime("%H:%M"),
            "daily_end_time": day_end_t.strftime("%H:%M") if day_end_t else None,
            "category_windows_applied": [
                {
                    "start_time": s.strftime("%H:%M"),
                    "end_time": e.strftime("%H:%M") if e else None,
                }
                for s, e in windows
            ],
            "matches_planned": len(rows),
            "sample_matches": rows[: min(5, len(rows))],
        }

    if replace_existing_phase:
        await client.request(
            method="DELETE",
            path="matches",
            query={
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
                "phase": f"eq.{phase_value}",
            },
        )

    inserted = await client.insert_rows(table="matches", payload=rows)
    inserted_ids = [str(r.get("id")) for r in (inserted or []) if r.get("id")]
    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": cat_id, "name": category_name_value},
        "phase": phase_value,
        "status": status_value,
        "replace_existing_phase": replace_existing_phase,
        "existing_matches_replaced": existing_count if replace_existing_phase else 0,
        "teams_count": len(team_ids),
        "fields": fields,
        "infinite_fields": bool(infinite_fields),
        "daily_start_time": day_start_t.strftime("%H:%M"),
        "daily_end_time": day_end_t.strftime("%H:%M") if day_end_t else None,
        "category_windows_applied": [
            {
                "start_time": s.strftime("%H:%M"),
                "end_time": e.strftime("%H:%M") if e else None,
            }
            for s, e in windows
        ],
        "matches_created": len(inserted_ids),
        "match_ids": inserted_ids[:100],
    }


async def schedule_regenerate_from_rules_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    category_id: Optional[str] = None,
    start_date: str,
    kickoff_time: str = "09:00",
    games_per_day: int = 4,
    interval_minutes: int = 90,
    field_number: Optional[str] = None,
    field_numbers: Optional[List[str]] = None,
    infinite_fields: bool = False,
    daily_start_time: Optional[str] = None,
    daily_end_time: Optional[str] = None,
    category_windows: Optional[List[Dict[str, Any]]] = None,
    status: str = "scheduled",
    replace_existing: bool = True,
    include_group_stage: bool = True,
    group_phase_name: str = "Fase estatal",
    include_knockout: bool = True,
    knockout_rounds: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")
    if games_per_day < 1 or games_per_day > 20:
        raise ValueError("games_per_day must be between 1 and 20")
    if interval_minutes < 30 or interval_minutes > 600:
        raise ValueError("interval_minutes must be between 30 and 600")
    if not include_group_stage and not include_knockout:
        raise ValueError(
            "At least one of include_group_stage/include_knockout must be true"
        )

    rounds = knockout_rounds or ["Cuartos", "Semifinal", "Final"]
    rounds = [r.strip() for r in rounds if (r or "").strip()]
    if include_knockout and not rounds:
        raise ValueError("knockout_rounds cannot be empty when include_knockout=true")

    start_d = _parse_date(start_date)
    if not start_d:
        raise ValueError("start_date is required")
    kickoff_t = _parse_time(kickoff_time)
    if not (daily_start_time or "").strip() or not (daily_end_time or "").strip():
        raise ValueError(
            "Para regenerar calendario necesito disponibilidad horaria: "
            "daily_start_time y daily_end_time (ej. 08:00 y 18:00)."
        )
    day_start_t = _parse_time(daily_start_time)
    day_end_t = _parse_time(daily_end_time)
    if day_end_t <= day_start_t:
        raise ValueError("daily_end_time must be later than daily_start_time")

    fields = _normalize_field_numbers(
        field_number=field_number, field_numbers=field_numbers
    )
    if infinite_fields:
        fields = [f"INF-{i+1}" for i in range(max(1, games_per_day))]
    elif not (field_number or (field_numbers or [])):
        raise ValueError(
            "Necesito canchas disponibles (field_numbers/field_number) "
            "o indica infinite_fields=true."
        )
    if games_per_day < len(fields):
        raise ValueError("games_per_day must be >= number of fields")
    status_value = (status or "scheduled").strip().lower()
    if status_value not in {
        "scheduled",
        "in_progress",
        "live",
        "finished",
        "completed",
    }:
        raise ValueError(
            "status must be one of: scheduled, in_progress, live, finished, completed"
        )

    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))

    cat_id = _safe_str(category_id)
    category_row = await resolve_category_for_tournament(
        client,
        tournament_id=tournament_id,
        category_id=cat_id,
    )
    cat_id = _safe_str(category_row.get("id"))
    category_name_value = category_row.get("name") or None
    windows = _normalize_windows(
        daily_start_time=day_start_t,
        daily_end_time=day_end_t,
        category_windows=category_windows,
        category_id=cat_id,
        category_name=category_name_value,
    )

    regs = await client.fetch_all_rows(
        table="registrations",
        select_expr="team_id,category_id",
        filters={"category_id": f"eq.{cat_id}"},
    )
    reg_team_ids = sorted(
        {
            str((r or {}).get("team_id") or "").strip()
            for r in regs
            if (r or {}).get("team_id")
        }
    )
    team_ids: List[str] = []
    if reg_team_ids:
        for i in range(0, len(reg_team_ids), 200):
            chunk = reg_team_ids[i : i + 200]
            teams = await client.select_rows(
                table="teams",
                select_expr="id,tournament_id,team_name",
                filters={
                    "id": f"in.({','.join(chunk)})",
                    "tournament_id": f"eq.{tournament_id}",
                },
                limit=200,
            )
            team_ids.extend([str(t.get("id")) for t in teams if t.get("id")])
    else:
        teams = await client.fetch_all_rows(
            table="teams",
            select_expr="id",
            filters={"tournament_id": f"eq.{tournament_id}"},
            order="created_at.asc",
        )
        team_ids = [str(t.get("id")) for t in teams if t.get("id")]

    team_ids = list(dict.fromkeys(team_ids))
    if len(team_ids) < 2:
        raise ValueError("Need at least 2 teams to generate schedule")

    all_rows: List[Dict[str, Any]] = []
    _next_slot = _build_slot_generator(
        start_date=start_d,
        fields=fields,
        games_per_day=games_per_day,
        interval_minutes=interval_minutes,
        windows=windows,
    )

    if include_group_stage:
        pairs = _round_robin_pairings(team_ids)
        for home_id, away_id in pairs:
            dt, field_value = _next_slot()
            match_dt = (
                dt.replace(hour=kickoff_t.hour, minute=kickoff_t.minute)
                if day_start_t == kickoff_t
                else dt
            )
            all_rows.append(
                {
                    "tournament_id": tournament_id,
                    "category_id": cat_id,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "match_date": match_dt.isoformat(),
                    "field_number": field_value,
                    "phase": group_phase_name,
                    "status": status_value,
                    "home_score": (
                        0 if status_value in {"finished", "completed"} else None
                    ),
                    "away_score": (
                        0 if status_value in {"finished", "completed"} else None
                    ),
                }
            )

    if include_knockout:
        placeholders: List[str] = list(team_ids[:])
        if len(placeholders) % 2 == 1:
            placeholders.append("__BYE__")
        for round_name in rounds:
            next_round: List[str] = []
            for idx in range(0, len(placeholders), 2):
                home_id = placeholders[idx]
                away_id = (
                    placeholders[idx + 1] if idx + 1 < len(placeholders) else "__BYE__"
                )
                if "__BYE__" in {home_id, away_id}:
                    winner = home_id if away_id == "__BYE__" else away_id
                    if winner != "__BYE__":
                        next_round.append(winner)
                    continue
                dt, field_value = _next_slot()
                match_dt = (
                    dt.replace(hour=kickoff_t.hour, minute=kickoff_t.minute)
                    if day_start_t == kickoff_t
                    else dt
                )
                all_rows.append(
                    {
                        "tournament_id": tournament_id,
                        "category_id": cat_id,
                        "home_team_id": home_id,
                        "away_team_id": away_id,
                        "match_date": match_dt.isoformat(),
                        "field_number": field_value,
                        "phase": round_name,
                        "status": status_value,
                        "home_score": (
                            0 if status_value in {"finished", "completed"} else None
                        ),
                        "away_score": (
                            0 if status_value in {"finished", "completed"} else None
                        ),
                    }
                )
                next_round.append(f"winner:{round_name}:{idx//2+1}")
            placeholders = next_round
            if len(placeholders) < 2:
                break

    existing_count = 0
    if replace_existing:
        existing = await client.fetch_all_rows(
            table="matches",
            select_expr="id",
            filters={
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
            },
        )
        existing_count = len(existing or [])

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": cat_id, "name": category_name_value},
            "status": status_value,
            "replace_existing": replace_existing,
            "existing_matches": existing_count,
            "teams_count": len(team_ids),
            "fields": fields,
            "infinite_fields": bool(infinite_fields),
            "daily_start_time": day_start_t.strftime("%H:%M"),
            "daily_end_time": day_end_t.strftime("%H:%M"),
            "category_windows_applied": [
                {
                    "start_time": s.strftime("%H:%M"),
                    "end_time": e.strftime("%H:%M") if e else None,
                }
                for s, e in windows
            ],
            "group_stage_enabled": include_group_stage,
            "group_phase_name": group_phase_name,
            "knockout_enabled": include_knockout,
            "knockout_rounds": rounds,
            "matches_planned": len(all_rows),
            "sample_matches": all_rows[: min(8, len(all_rows))],
        }

    if replace_existing:
        await client.request(
            method="DELETE",
            path="matches",
            query={
                "tournament_id": f"eq.{tournament_id}",
                "category_id": f"eq.{cat_id}",
            },
        )

    inserted = await client.insert_rows(table="matches", payload=all_rows)
    inserted_ids = [str(r.get("id")) for r in (inserted or []) if r.get("id")]
    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": cat_id, "name": category_name_value},
        "status": status_value,
        "replace_existing": replace_existing,
        "existing_matches_replaced": existing_count if replace_existing else 0,
        "teams_count": len(team_ids),
        "fields": fields,
        "infinite_fields": bool(infinite_fields),
        "daily_start_time": day_start_t.strftime("%H:%M"),
        "daily_end_time": day_end_t.strftime("%H:%M"),
        "category_windows_applied": [
            {
                "start_time": s.strftime("%H:%M"),
                "end_time": e.strftime("%H:%M") if e else None,
            }
            for s, e in windows
        ],
        "group_stage_enabled": include_group_stage,
        "group_phase_name": group_phase_name,
        "knockout_enabled": include_knockout,
        "knockout_rounds": rounds,
        "matches_created": len(inserted_ids),
        "match_ids": inserted_ids[:100],
    }


async def update_team_fields_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    team_id: Optional[str] = None,
    team_name: Optional[str] = None,
    updates: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")

    requested_updates = {
        str(k): v for k, v in dict(updates or {}).items() if v not in (None, "")
    }
    if not requested_updates:
        raise ValueError("updates is required")

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
        team_id=team_id,
        team_name=team_name,
    )
    team_id_value = _safe_str(team.get("id"))

    team_patch: Dict[str, Any] = {}
    applied_fields: List[str] = []
    local_only_fields: List[str] = []

    if "name" in requested_updates:
        team_patch["team_name"] = _safe_str(requested_updates["name"])
        applied_fields.append("name")
    if "state" in requested_updates:
        team_patch["state"] = _safe_str(requested_updates["state"])
        applied_fields.append("state")
    if "league" in requested_updates:
        team_patch["academy_name"] = _safe_str(requested_updates["league"])
        applied_fields.append("league")
    if "municipality" in requested_updates:
        team_patch["municipality"] = (
            _safe_str(requested_updates["municipality"]) or None
        )
        applied_fields.append("municipality")
    if "status" in requested_updates:
        status_value = _safe_str(requested_updates["status"]).lower()
        if status_value not in {"pending", "approved", "rejected", "paid"}:
            raise ValueError("status must be one of: pending, approved, rejected, paid")
        team_patch["status"] = status_value
        applied_fields.append("status")

    manager_updates_requested = any(
        key in requested_updates for key in {"representative_name", "contact_email"}
    )
    manager_result: Dict[str, Any] = {}
    if manager_updates_requested:
        managers = await client.select_rows(
            table="team_managers",
            select_expr="id,first_name,last_name,email,phone,is_primary,position",
            filters={"team_id": f"eq.{team_id_value}"},
            order="is_primary.desc,created_at.asc",
            limit=5,
        )
        primary = managers[0] if managers else None
        rep_name = _safe_str(requested_updates.get("representative_name"))
        rep_email = _safe_str(requested_updates.get("contact_email"))
        if dry_run:
            manager_result = {
                "action": "patch" if primary else "create",
                "id": _safe_str((primary or {}).get("id")) or None,
                "representative_name": rep_name or None,
                "contact_email": rep_email or None,
            }
        else:
            if primary:
                manager_payload: Dict[str, Any] = {}
                if rep_name:
                    first_name = rep_name.split()[0]
                    last_name = " ".join(rep_name.split()[1:]) or "Representante"
                    manager_payload["first_name"] = first_name
                    manager_payload["last_name"] = last_name
                    applied_fields.append("representative_name")
                if rep_email:
                    manager_payload["email"] = rep_email
                    applied_fields.append("contact_email")
                if manager_payload:
                    updated_rows = await client.request(
                        method="PATCH",
                        path="team_managers",
                        query={"id": f"eq.{_safe_str(primary.get('id'))}"},
                        payload=manager_payload,
                    )
                    manager_result = (
                        (updated_rows or [{}])[0]
                        if isinstance(updated_rows, list)
                        else (updated_rows or {})
                    )
            else:
                payload = {
                    "team_id": team_id_value,
                    "first_name": (
                        rep_name.split()[0] if rep_name else "Representante"
                    ),
                    "last_name": (
                        " ".join(rep_name.split()[1:])
                        if rep_name and len(rep_name.split()) > 1
                        else "Pendiente"
                    ),
                    "email": rep_email or "pendiente@sam.chat",
                    "phone": "0000000000",
                    "position": "Representante",
                    "is_primary": True,
                }
                inserted = await client.insert_rows(
                    table="team_managers", payload=payload
                )
                manager_result = (inserted or [{}])[0] if inserted else {}
                if rep_name:
                    applied_fields.append("representative_name")
                if rep_email:
                    applied_fields.append("contact_email")

    unsupported = {"gender", "category"}
    for field in unsupported:
        if field in requested_updates:
            local_only_fields.append(field)

    patched_team = dict(team)
    if dry_run:
        patched_team.update(team_patch)
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "team": {"id": team_id_value, "team_name": patched_team.get("team_name")},
            "requested_fields": sorted(requested_updates.keys()),
            "applied_fields": sorted(set(applied_fields)),
            "local_only_fields": sorted(set(local_only_fields)),
            "team_patch": team_patch,
            "manager_patch": manager_result,
        }

    if team_patch:
        try:
            updated_rows = await client.request(
                method="PATCH",
                path="teams",
                query={"id": f"eq.{team_id_value}"},
                payload=team_patch,
            )
        except TournamentsV2Error as exc:
            if not (
                _is_missing_column_error(exc, "municipality")
                and "municipality" in team_patch
            ):
                raise
            fallback_patch = dict(team_patch)
            fallback_patch.pop("municipality", None)
            team_patch = fallback_patch
            if "municipality" not in local_only_fields:
                local_only_fields.append("municipality")
            if "municipality" in applied_fields:
                applied_fields.remove("municipality")
            updated_rows = []
            if fallback_patch:
                updated_rows = await client.request(
                    method="PATCH",
                    path="teams",
                    query={"id": f"eq.{team_id_value}"},
                    payload=fallback_patch,
                )
        if isinstance(updated_rows, list) and updated_rows:
            patched_team = updated_rows[0]

    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "team": {"id": team_id_value, "team_name": patched_team.get("team_name")},
        "requested_fields": sorted(requested_updates.keys()),
        "applied_fields": sorted(set(applied_fields)),
        "local_only_fields": sorted(set(local_only_fields)),
        "manager": manager_result or None,
    }


async def update_team_registration_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    team_id: Optional[str] = None,
    team_name: Optional[str] = None,
    current_category_id: Optional[str] = None,
    current_category_name: Optional[str] = None,
    target_category_id: Optional[str] = None,
    target_category_name: Optional[str] = None,
    target_branch: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")

    if not any(
        [
            _safe_str(target_category_id),
            _safe_str(target_category_name),
            _safe_str(target_branch),
        ]
    ):
        raise ValueError(
            "target_category_id, target_category_name or target_branch is required"
        )

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
        team_id=team_id,
        team_name=team_name,
    )
    team_id_value = _safe_str(team.get("id"))
    registration, categories_by_id, registrations = await _resolve_registration_context(
        client,
        team_id=team_id_value,
        tournament_id=tournament_id,
        current_category_id=current_category_id,
        current_category_name=current_category_name,
    )
    current_category = (
        categories_by_id.get(_safe_str(registration.get("category_id"))) or {}
    )

    if _safe_str(target_category_id):
        target_category = await resolve_category_for_tournament(
            client,
            tournament_id=tournament_id,
            category_id=target_category_id,
            default_first=False,
        )
    else:
        target_category = _resolve_target_category_from_candidates(
            categories=list(categories_by_id.values()),
            current_category=current_category,
            target_category_name=target_category_name,
            target_branch=target_branch,
        )

    registration_id = _safe_str(registration.get("id"))
    target_category_id_value = _safe_str(target_category.get("id"))
    current_category_id_value = _safe_str(current_category.get("id"))
    current_branch_value = _normalize_branch(current_category.get("branch"))
    target_branch_value = _normalize_branch(
        target_category.get("branch")
    ) or _normalize_branch(target_branch)
    requested_fields: List[str] = []
    if _safe_str(target_category_name) or _safe_str(target_category_id):
        requested_fields.append("category")
    if _safe_str(target_branch):
        requested_fields.append("gender")

    if current_category_id_value == target_category_id_value:
        return {
            "created": False,
            "dry_run": dry_run,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "team": {"id": team_id_value, "team_name": team.get("team_name")},
            "registration": {
                "id": registration_id,
                "current_category_id": current_category_id_value,
                "current_category_name": current_category.get("name"),
                "target_category_id": target_category_id_value,
                "target_category_name": target_category.get("name"),
                "current_branch": current_branch_value,
                "target_branch": target_branch_value,
                "updated": False,
            },
            "requested_fields": sorted(set(requested_fields)),
            "applied_fields": [],
            "local_only_fields": [],
            "nota": "La inscripcion ya estaba asociada a la categoria/rama solicitada.",
        }

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "team": {"id": team_id_value, "team_name": team.get("team_name")},
            "registration": {
                "id": registration_id,
                "current_category_id": current_category_id_value,
                "current_category_name": current_category.get("name"),
                "target_category_id": target_category_id_value,
                "target_category_name": target_category.get("name"),
                "current_branch": current_branch_value,
                "target_branch": target_branch_value,
                "updated": False,
                "registrations_found": len(registrations),
            },
            "requested_fields": sorted(set(requested_fields)),
            "applied_fields": sorted(set(requested_fields)),
            "local_only_fields": [],
        }

    updated_rows = await client.request(
        method="PATCH",
        path="registrations",
        query={"id": f"eq.{registration_id}"},
        payload={"category_id": target_category_id_value},
    )
    updated_registration = (
        (updated_rows or [{}])[0]
        if isinstance(updated_rows, list) and updated_rows
        else dict(registration)
    )

    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "team": {"id": team_id_value, "team_name": team.get("team_name")},
        "registration": {
            "id": registration_id,
            "current_category_id": current_category_id_value,
            "current_category_name": current_category.get("name"),
            "target_category_id": target_category_id_value,
            "target_category_name": target_category.get("name"),
            "current_branch": current_branch_value,
            "target_branch": target_branch_value,
            "updated": True,
            "category_id": updated_registration.get("category_id"),
        },
        "requested_fields": sorted(set(requested_fields)),
        "applied_fields": sorted(set(requested_fields)),
        "local_only_fields": [],
    }


async def update_player_fields_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    team_id: Optional[str] = None,
    team_name: Optional[str] = None,
    category_id: Optional[str] = None,
    category_name: Optional[str] = None,
    match_curp: Optional[str] = None,
    match_first_name: Optional[str] = None,
    match_last_name: Optional[str] = None,
    match_birth_date: Optional[str] = None,
    updates: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")

    requested_updates = {
        str(k): v for k, v in dict(updates or {}).items() if v not in (None, "")
    }
    if not requested_updates:
        raise ValueError("updates is required")

    patch_payload, local_only_fields = _normalize_player_update_payload(
        requested_updates
    )
    if not patch_payload and not local_only_fields:
        raise ValueError("No supported player fields to update")

    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))
    category_row = await resolve_category_for_tournament(
        client,
        tournament_id=tournament_id,
        category_id=category_id,
        category_name=category_name,
    )
    category_id_value = _safe_str(category_row.get("id"))
    team = await resolve_team_for_tournament(
        client,
        tournament_id=tournament_id,
        team_id=team_id,
        team_name=team_name,
    )
    team_id_value = _safe_str(team.get("id"))

    registrations = await client.fetch_all_rows(
        table="registrations",
        select_expr="id,team_id,category_id,registration_date",
        filters={"team_id": f"eq.{team_id_value}"},
        order="registration_date.desc",
    )
    registration = None
    for row in registrations:
        if _safe_str(row.get("category_id")) == category_id_value:
            registration = row
            break
    if registration is None and registrations:
        registration = registrations[0]
    if registration is None:
        raise ValueError("Registration not found for team/category in Supabase")
    registration_id = _safe_str(registration.get("id"))

    try:
        players = await client.fetch_all_rows(
            table="players",
            select_expr="id,registration_id,first_name,last_name,birth_date,curp,parent_email,email",
            filters={"registration_id": f"eq.{registration_id}"},
            order="created_at.asc",
        )
    except TournamentsV2Error as exc:
        if not _is_missing_column_error(exc, "email"):
            raise
        players = await client.fetch_all_rows(
            table="players",
            select_expr="id,registration_id,first_name,last_name,birth_date,curp,parent_email",
            filters={"registration_id": f"eq.{registration_id}"},
            order="created_at.asc",
        )
    match_curp_value = _safe_str(match_curp).upper()
    target = None
    if match_curp_value:
        for row in players:
            if _safe_str(row.get("curp")).upper() == match_curp_value:
                target = row
                break
    if target is None:
        identity = _player_identity_key(
            first_name=match_first_name,
            last_name=match_last_name,
            birth_date=_parse_date_text(match_birth_date)
            or _safe_str(match_birth_date),
        )
        for row in players:
            if (
                _player_identity_key(
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                    birth_date=row.get("birth_date"),
                )
                == identity
            ):
                target = row
                break
    if target is None:
        raise ValueError("Player not found in Supabase for update")

    player_id = _safe_str(target.get("id"))
    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": {"id": category_id_value, "name": category_row.get("name")},
            "team": {"id": team_id_value, "team_name": team.get("team_name")},
            "player": {
                "id": player_id,
                "first_name": target.get("first_name"),
                "last_name": target.get("last_name"),
                "birth_date": target.get("birth_date"),
                "curp": target.get("curp"),
                "email": target.get("email"),
            },
            "requested_fields": sorted(requested_updates.keys()),
            "applied_fields": sorted(patch_payload.keys()),
            "local_only_fields": sorted(local_only_fields),
            "patch_payload": patch_payload,
        }

    patched_player = dict(target)
    if patch_payload:
        try:
            updated_rows = await client.request(
                method="PATCH",
                path="players",
                query={"id": f"eq.{player_id}"},
                payload=patch_payload,
            )
        except TournamentsV2Error as exc:
            if not (
                _is_missing_column_error(exc, "email") and "email" in patch_payload
            ):
                raise
            fallback_patch = dict(patch_payload)
            fallback_patch.pop("email", None)
            patch_payload = fallback_patch
            if "email" not in local_only_fields:
                local_only_fields.append("email")
            updated_rows = []
            if fallback_patch:
                updated_rows = await client.request(
                    method="PATCH",
                    path="players",
                    query={"id": f"eq.{player_id}"},
                    payload=fallback_patch,
                )
        if isinstance(updated_rows, list) and updated_rows:
            patched_player = updated_rows[0]

    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": {"id": category_id_value, "name": category_row.get("name")},
        "team": {"id": team_id_value, "team_name": team.get("team_name")},
        "player": {
            "id": player_id,
            "first_name": patched_player.get("first_name"),
            "last_name": patched_player.get("last_name"),
            "birth_date": patched_player.get("birth_date"),
            "curp": patched_player.get("curp"),
            "email": patched_player.get("email"),
        },
        "requested_fields": sorted(requested_updates.keys()),
        "applied_fields": sorted(patch_payload.keys()),
        "local_only_fields": sorted(local_only_fields),
    }


async def create_media_asset_v2(
    *,
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    asset_type: str = "photo",
    title: str = "",
    description: Optional[str] = None,
    url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    category_id: Optional[str] = None,
    category_name: Optional[str] = None,
    video_type: str = "highlight",
    platform: str = "youtube",
    scheduled_time: Optional[str] = None,
    status: str = "scheduled",
    dry_run: bool = False,
) -> Dict[str, Any]:
    config = load_tournaments_v2_config()
    if not config.writes_enabled:
        raise ValueError("TOURNAMENTS_V2_WRITES_ENABLED is disabled")

    asset_type_value = _safe_str(asset_type).lower() or "photo"
    if asset_type_value not in {"photo", "video", "stream"}:
        raise ValueError("asset_type must be one of: photo, video, stream")
    title_value = _safe_str(title)
    url_value = _safe_str(url)
    if not title_value:
        raise ValueError("title is required")
    if not url_value:
        raise ValueError("url is required")

    client = SupabaseRestClient(config)
    tournament = await resolve_primary_tournament(
        client,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
    )
    tournament_id = _safe_str(tournament.get("id"))

    category = None
    category_id_value = None
    if _safe_str(category_id) or _safe_str(category_name):
        category = await resolve_category_for_tournament(
            client,
            tournament_id=tournament_id,
            category_id=category_id,
            category_name=category_name,
            default_first=False,
        )
        category_id_value = _safe_str(category.get("id"))

    table = "gallery_photos"
    payload: Dict[str, Any]
    if asset_type_value == "photo":
        payload = {
            "title": title_value,
            "description": _safe_str(description) or None,
            "image_url": url_value,
            "category_id": category_id_value,
            "photo_date": date.today().isoformat(),
            "tournament_id": tournament_id,
        }
    elif asset_type_value == "video":
        table = "featured_videos"
        payload = {
            "title": title_value,
            "description": _safe_str(description) or None,
            "video_url": url_value,
            "thumbnail_url": _safe_str(thumbnail_url) or None,
            "video_type": _safe_str(video_type) or "highlight",
            "category_id": category_id_value,
            "tournament_id": tournament_id,
        }
    else:
        table = "live_streams"
        scheduled = _safe_str(scheduled_time)
        if not scheduled:
            raise ValueError("scheduled_time is required for stream assets")
        payload = {
            "title": title_value,
            "description": _safe_str(description) or None,
            "stream_url": url_value,
            "platform": _safe_str(platform) or "youtube",
            "scheduled_time": scheduled,
            "status": _safe_str(status) or "scheduled",
            "tournament_id": tournament_id,
        }

    if dry_run:
        return {
            "created": False,
            "dry_run": True,
            "source": "supabase_tournaments_v2",
            "table": table,
            "payload": payload,
            "tournament": {
                "id": tournament_id,
                "name": tournament.get("name"),
                "slug": tournament.get("slug"),
            },
            "category": (
                {"id": category_id_value, "name": (category or {}).get("name")}
                if category
                else None
            ),
        }

    inserted = await client.insert_rows(table=table, payload=payload)
    row = (inserted or [{}])[0] if inserted else {}
    return {
        "created": True,
        "dry_run": False,
        "source": "supabase_tournaments_v2",
        "table": table,
        "asset_type": asset_type_value,
        "media_asset": row,
        "tournament": {
            "id": tournament_id,
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
        },
        "category": (
            {"id": category_id_value, "name": (category or {}).get("name")}
            if category
            else None
        ),
    }
