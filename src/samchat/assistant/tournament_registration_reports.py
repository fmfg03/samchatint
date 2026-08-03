from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set


MUNICIPALITY_DENOMINATORS_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "mexico_municipalities_2026.json"
)


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    )
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def calculate_age(birth_date: Any, as_of: date) -> Optional[int]:
    born = parse_date(birth_date)
    if born is None:
        return None
    return as_of.year - born.year - (
        (as_of.month, as_of.day) < (born.month, born.day)
    )


def normalize_branch(category: Any, gender: Any = None, branch: Any = None) -> str:
    haystack = " ".join(
        normalize_text(item) for item in (branch, gender, category) if item
    )
    if "femen" in haystack:
        return "femenil"
    if "juvenil" in haystack or re.search(r"\b(?:u?1[567]|15|16|17)\b", haystack):
        return "juvenil"
    if "varon" in haystack or "mascul" in haystack:
        return "varonil"
    return "sin clasificar"


def load_municipality_denominators(
    path: Path = MUNICIPALITY_DENOMINATORS_PATH,
) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "source": payload.get("source"),
        "national_total": int(payload.get("national_total") or 0),
        "states": {
            normalize_text(key): int(value)
            for key, value in dict(payload.get("states") or {}).items()
        },
    }


def _pct(part: int, total: Optional[int]) -> Optional[float]:
    if not total:
        return None
    return round((int(part) / int(total)) * 100, 2)


def _valid_curp(value: Any) -> bool:
    raw = str(value or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", raw))


def _sorted_rows(rows: Iterable[Dict[str, Any]], *keys: str) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: tuple(str(row.get(key) or "") for key in keys),
    )


def _team_id(team: Mapping[str, Any]) -> str:
    return str(team.get("id") or team.get("team_id") or "").strip()


def _registration_id(registration: Mapping[str, Any]) -> str:
    return str(registration.get("id") or registration.get("registration_id") or "").strip()


def _player_registration_id(player: Mapping[str, Any]) -> str:
    return str(player.get("registration_id") or "").strip()


def _category_label(category: Mapping[str, Any]) -> str:
    return str(category.get("name") or category.get("category") or "(sin categoria)").strip()


def _state_label(team: Mapping[str, Any]) -> str:
    return str(team.get("state") or "(sin estado)").strip()


def _municipality_label(team: Mapping[str, Any]) -> str:
    return str(
        team.get("municipality")
        or team.get("academy_name")
        or "(sin municipio)"
    ).strip()


def _index_by_id(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    indexed: Dict[str, Mapping[str, Any]] = {}
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        if row_id:
            indexed[row_id] = row
    return indexed


def build_registration_executive_reports(
    *,
    dataset: Mapping[str, Any],
    tournament_key: str,
    tournament_slug: Optional[str] = None,
    as_of_date: Optional[str] = None,
    municipality_denominators: Optional[Mapping[str, Any]] = None,
    source: str = "supabase_tournaments_v2",
) -> Dict[str, Any]:
    as_of = parse_date(as_of_date) or date.today()
    denominators = dict(municipality_denominators or load_municipality_denominators())
    state_denominators = dict(denominators.get("states") or {})
    national_denominator = int(denominators.get("national_total") or 0)
    denominator_source = str(denominators.get("source") or "")

    tournaments = list(dataset.get("tournaments") or [])
    categories = _index_by_id(dataset.get("categories") or [])
    teams = _index_by_id(dataset.get("teams") or [])
    registrations = list(dataset.get("registrations") or [])
    players = list(dataset.get("players") or [])
    players_by_registration: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for player in players:
        players_by_registration[_player_registration_id(player)].append(player)

    participation: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    municipality_sets_by_state: Dict[str, Set[str]] = defaultdict(set)
    municipality_sets_national: Set[tuple[str, str]] = set()
    femenil: Dict[tuple[str, str], Dict[str, Any]] = {}
    juvenil: Dict[tuple[str, str], Dict[str, Any]] = {}
    varonil: Dict[tuple[str, str], Dict[str, Any]] = {}
    quality: Dict[tuple[str, str], Dict[str, Any]] = {}
    validation: Dict[tuple[str, str], Dict[str, Any]] = {}
    total_team_ids: Set[str] = set()
    total_players = 0

    for registration in registrations:
        team = teams.get(str(registration.get("team_id") or "").strip())
        category = categories.get(
            str(registration.get("category_id") or "").strip(),
            {},
        )
        if not team:
            continue
        team_id = _team_id(team)
        total_team_ids.add(team_id)
        state = _state_label(team)
        municipality = _municipality_label(team)
        category_name = _category_label(category)
        branch = normalize_branch(
            category_name,
            team.get("gender"),
            category.get("branch"),
        )
        row_players = players_by_registration.get(_registration_id(registration), [])
        total_players += len(row_players)
        municipality_key = normalize_text(municipality)
        state_key = normalize_text(state)
        if municipality_key and municipality != "(sin municipio)":
            municipality_sets_by_state[state_key].add(municipality_key)
            municipality_sets_national.add((state_key, municipality_key))

        part_key = (state, municipality, category_name, branch)
        part = participation.setdefault(
            part_key,
            {
                "estado": state,
                "municipio": municipality,
                "categoria": category_name,
                "rama": branch,
                "equipos_ids": set(),
                "jugadores": 0,
                "fuente": source,
            },
        )
        part["equipos_ids"].add(team_id)
        part["jugadores"] += len(row_players)

        q_key = (state, municipality)
        q_row = quality.setdefault(
            q_key,
            {
                "estado": state,
                "municipio": municipality,
                "jugadores": 0,
                "sin_fecha_nacimiento": 0,
                "curp_faltante": 0,
                "curp_invalida": 0,
                "fuente": source,
            },
        )
        v_row = validation.setdefault(
            q_key,
            {
                "estado": state,
                "municipio": municipality,
                "jugadores_fmf_requeridos": 0,
                "fmf_status": "unavailable",
                "jugadores_renapo_requeridos": 0,
                "renapo_status": "unavailable",
                "fuente": source,
            },
        )

        for player in row_players:
            age = calculate_age(player.get("birth_date"), as_of)
            curp = player.get("curp")
            q_row["jugadores"] += 1
            if age is None:
                q_row["sin_fecha_nacimiento"] += 1
            if not str(curp or "").strip():
                q_row["curp_faltante"] += 1
            elif not _valid_curp(curp):
                q_row["curp_invalida"] += 1
            v_row["jugadores_renapo_requeridos"] += 1

            if branch == "femenil":
                row = femenil.setdefault(
                    q_key,
                    {
                        "estado": state,
                        "municipio": municipality,
                        "menores_17": 0,
                        "mayores_18": 0,
                        "sin_fecha_nacimiento": 0,
                        "fuente": source,
                    },
                )
                if age is None:
                    row["sin_fecha_nacimiento"] += 1
                elif age <= 17:
                    row["menores_17"] += 1
                else:
                    row["mayores_18"] += 1
            elif branch == "juvenil":
                v_row["jugadores_fmf_requeridos"] += 1
                row = juvenil.setdefault(
                    q_key,
                    {
                        "estado": state,
                        "municipio": municipality,
                        "edad_15": 0,
                        "edad_16": 0,
                        "edad_17": 0,
                        "otras_edades": 0,
                        "sin_fecha_nacimiento": 0,
                        "fuente": source,
                    },
                )
                if age is None:
                    row["sin_fecha_nacimiento"] += 1
                elif age in (15, 16, 17):
                    row[f"edad_{age}"] += 1
                else:
                    row["otras_edades"] += 1
            elif branch == "varonil":
                v_row["jugadores_fmf_requeridos"] += 1
                row = varonil.setdefault(
                    q_key,
                    {
                        "estado": state,
                        "municipio": municipality,
                        "edad_18": 0,
                        "edad_19_24": 0,
                        "edad_25_29": 0,
                        "edad_30_mas": 0,
                        "otras_edades": 0,
                        "sin_fecha_nacimiento": 0,
                        "fuente": source,
                    },
                )
                if age is None:
                    row["sin_fecha_nacimiento"] += 1
                elif age == 18:
                    row["edad_18"] += 1
                elif 19 <= age <= 24:
                    row["edad_19_24"] += 1
                elif 25 <= age <= 29:
                    row["edad_25_29"] += 1
                elif age >= 30:
                    row["edad_30_mas"] += 1
                else:
                    row["otras_edades"] += 1

    participation_rows = []
    for row in participation.values():
        output = dict(row)
        output["equipos"] = len(output.pop("equipos_ids"))
        participation_rows.append(output)

    municipal_rows: List[Dict[str, Any]] = []
    for state_key, municipal_set in municipality_sets_by_state.items():
        denominator = state_denominators.get(state_key)
        state_label = next(
            (
                row["estado"]
                for row in participation_rows
                if normalize_text(row["estado"]) == state_key
            ),
            state_key,
        )
        municipal_rows.append(
            {
                "nivel": "estatal",
                "estado": state_label,
                "municipios_participantes": len(municipal_set),
                "municipios_totales": denominator,
                "porcentaje": _pct(len(municipal_set), denominator),
                "fuente": denominator_source,
            }
        )
    municipal_rows.append(
        {
            "nivel": "nacional",
            "estado": "Nacional",
            "municipios_participantes": len(municipality_sets_national),
            "municipios_totales": national_denominator,
            "porcentaje": _pct(len(municipality_sets_national), national_denominator),
            "fuente": denominator_source,
        }
    )

    caveats: List[str] = []
    if any(row.get("municipios_totales") is None for row in municipal_rows):
        caveats.append(
            "Hay estados sin denominador municipal; el porcentaje queda sin calcular."
        )
    if not registrations:
        caveats.append("No se encontraron registros de cedulas para el torneo seleccionado.")
    caveats.append(
        "FMF/Liga MX y RENAPO se reportan como unavailable hasta conectar una fuente verificable."
    )
    if as_of_date is None:
        caveats.append(
            "Edad calculada con fecha de corte "
            f"{as_of.isoformat()} porque no se proporciono as_of_date."
        )

    primary_tournament = tournaments[0] if tournaments else {}
    return {
        "title": "Reportes de cedulas por torneo",
        "tournament_key": tournament_key,
        "tournament": {
            "id": str(primary_tournament.get("id") or ""),
            "name": primary_tournament.get("name"),
            "slug": primary_tournament.get("slug") or tournament_slug,
        },
        "as_of_date": as_of.isoformat(),
        "summary": {
            "equipos": len(total_team_ids),
            "jugadores": int(total_players),
            "estados": len(
                {
                    normalize_text(row["estado"])
                    for row in participation_rows
                    if row.get("estado")
                }
            ),
            "municipios_participantes": len(municipality_sets_national),
        },
        "reports": {
            "participacion_general": _sorted_rows(
                participation_rows, "estado", "municipio", "categoria", "rama"
            ),
            "cobertura_municipal": _sorted_rows(municipal_rows, "nivel", "estado"),
            "femenil_edad": _sorted_rows(femenil.values(), "estado", "municipio"),
            "juvenil_edades": _sorted_rows(juvenil.values(), "estado", "municipio"),
            "varonil_rangos": _sorted_rows(varonil.values(), "estado", "municipio"),
            "calidad_datos": _sorted_rows(quality.values(), "estado", "municipio"),
            "validaciones_externas": _sorted_rows(validation.values(), "estado", "municipio"),
        },
        "caveats": caveats,
        "source": source,
    }
