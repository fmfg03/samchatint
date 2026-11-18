"""Scoped CTT reports from committed team and active-player records."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from samchat.assistant.tournament_registration_reports import (
    calculate_age,
    normalize_branch,
    normalize_text,
    parse_date,
)

from .ctt_municipality_catalog import (
    CttGeography,
    MunicipalityCatalog,
    load_ctt_municipality_catalog,
    resolve_ctt_geography,
)
from .registration_read_model import SOURCE, _rows, probe_registration_scope

__all__ = [
    "build_ctt_registration_report",
    "ctt_registration_category",
    "resolve_ctt_geography",
]

_CATEGORIES = {
    "femenil": "Femenil",
    "juvenil": "Juvenil",
    "varonil": "Varonil",
    "sin clasificar": "Sin clasificar",
}
_AGE_REPORTS = {
    "femenil": "femenil_edad",
    "juvenil": "juvenil_edades",
    "varonil": "varonil_rangos",
}
_AGE_RANGES = {
    "femenil": (("menores_17", 0, 17), ("mayores_18", 18, None)),
    "juvenil": (("edad_15", 15, 15), ("edad_16", 16, 16), ("edad_17", 17, 17)),
    "varonil": (
        ("edad_18", 18, 18),
        ("edad_19_24", 19, 24),
        ("edad_25_29", 25, 29),
        ("edad_30_mas", 30, None),
    ),
}
_AGE_ISSUES = ("sin_fecha_nacimiento", "fecha_nacimiento_invalida")
_QUALITY_FIELDS = (
    "equipos",
    "jugadores",
    *_AGE_ISSUES,
    "equipos_sin_municipio_inegi",
    "equipos_sin_estado_inegi",
    "equipos_sin_categoria",
)


@dataclass(frozen=True)
class _Area:
    nivel: str
    estado: str
    municipio: str = ""
    state_code: str = ""
    municipality_code: str = ""


_NATIONAL = _Area("nacional", "Nacional")


def _areas(geo: CttGeography, *, include_national: bool) -> tuple[_Area, ...]:
    return (
        *((_NATIONAL,) if include_national else ()),
        _Area("estatal", geo.state_name, state_code=geo.state_code),
        _Area(
            "municipal",
            geo.state_name,
            geo.municipality_name,
            geo.state_code,
            geo.municipality_code,
        ),
    )


def ctt_registration_category(category: str | None, gender: str | None) -> str:
    """Resolve one of the three CTT categories from declared roster fields."""
    declared_category = normalize_branch(category)
    declared_gender = normalize_branch(None, gender)
    if declared_category == "juvenil":
        return "sin clasificar" if declared_gender == "femenil" else "juvenil"
    if declared_category != "sin clasificar":
        if declared_gender in {declared_category, "sin clasificar"}:
            return declared_category
        return "sin clasificar"
    if normalize_text(category) in {"libre", "unica", "open"}:
        return declared_gender
    if not normalize_text(category) and declared_gender == "femenil":
        return declared_gender
    return "sin clasificar"


def _age_counts(rows: list[dict[str, Any]], branch: str, cutoff: date) -> Counter[str]:
    counts: Counter[str] = Counter()
    ranges = _AGE_RANGES.get(branch, ())
    for row in rows:
        players = int(row["player_count"])
        if not players:
            continue
        counts["jugadores"] += players
        raw_date = row["birth_date"]
        born = parse_date(raw_date)
        if raw_date is None or raw_date == "":
            counts["sin_fecha_nacimiento"] += players
        elif born is None or born > cutoff:
            counts["fecha_nacimiento_invalida"] += players
        elif ranges:
            age = calculate_age(born, cutoff)
            assert age is not None  # ``born`` is already a validated date.
            bucket = next(
                (
                    field
                    for field, low, high in ranges
                    if age >= low and (high is None or age <= high)
                ),
                "otras_edades",
            )
            counts[bucket] += players
    return counts


def _coverage_rows(
    catalog: MunicipalityCatalog,
    participating: set[str],
    state_code: str,
) -> list[dict[str, Any]]:
    by_state = Counter(code[:2] for code in participating)
    rows = []
    if not state_code:
        rows.append(
            _coverage_row(_NATIONAL, len(participating), len(catalog.municipalities))
        )
    for code, name in catalog.states.items():
        if not state_code or code == state_code:
            rows.append(
                _coverage_row(
                    _Area("estatal", name, state_code=code),
                    by_state[code],
                    catalog.state_totals[code],
                )
            )
    return rows


def _coverage_row(area: _Area, participants: int, total: int) -> dict[str, Any]:
    return {
        **asdict(area),
        "municipios_participantes": participants,
        "municipios_totales": total,
        "porcentaje": round(100 * participants / total, 2),
    }


def _filter_options(catalog: MunicipalityCatalog, state_code: str) -> dict[str, Any]:
    return {
        "states": [
            {"value": code, "label": name} for code, name in catalog.states.items()
        ],
        "municipalities": [
            {
                "value": code,
                "label": name if state_code else f"{name} · {catalog.states[code[:2]]}",
            }
            for code, name in catalog.municipalities.items()
            if not state_code or code[:2] == state_code
        ],
        "categories": [
            {"value": code, "label": label} for code, label in _CATEGORIES.items()
        ],
    }


def _sorted_areas(areas: Iterable[_Area]) -> list[_Area]:
    order = {"nacional": 0, "estatal": 1, "municipal": 2}
    return sorted(
        areas,
        key=lambda area: (
            order[area.nivel],
            area.estado,
            area.municipio,
            area.state_code,
            area.municipality_code,
        ),
    )


def _assemble_reports(
    rows: list[dict[str, Any]],
    catalog: MunicipalityCatalog,
    *,
    age_cutoff: date,
    state_code: str,
    municipality_code: str,
    category: str,
) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    teams_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["team_id"] is not None:
            teams_by_id[str(row["team_id"])].append(row)

    participation: dict[_Area, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    quality: dict[_Area, Counter[str]] = defaultdict(Counter)
    ages: dict[str, dict[_Area, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    states: set[str] = set()
    municipalities: set[str] = set()
    total_teams = total_players = 0
    for team_rows in teams_by_id.values():
        team = team_rows[0]
        geo = resolve_ctt_geography(team["state"], team["municipality"])
        branch = ctt_registration_category(team["category"], team["gender"])
        if (
            (state_code and geo.state_code != state_code)
            or (municipality_code and geo.municipality_code != municipality_code)
            or (category and branch != category)
        ):
            continue

        counts = _age_counts(team_rows, branch, age_cutoff)
        total_teams += 1
        total_players += counts["jugadores"]
        if geo.state_code:
            states.add(geo.state_code)
        if geo.municipality_code:
            municipalities.add(geo.municipality_code)
        for area in _areas(geo, include_national=not state_code):
            counts_by_branch = participation[area]
            counts_by_branch[""]["equipos"] += 1
            counts_by_branch[""]["jugadores"] += counts["jugadores"]
            counts_by_branch[branch]["equipos"] += 1
            counts_by_branch[branch]["jugadores"] += counts["jugadores"]
            quality[area].update(
                {
                    "equipos": 1,
                    "jugadores": counts["jugadores"],
                    "sin_fecha_nacimiento": counts["sin_fecha_nacimiento"],
                    "fecha_nacimiento_invalida": counts["fecha_nacimiento_invalida"],
                    "equipos_sin_municipio_inegi": int(not geo.municipality_code),
                    "equipos_sin_estado_inegi": int(not geo.state_code),
                    "equipos_sin_categoria": int(branch == "sin clasificar"),
                }
            )
            if branch in _AGE_REPORTS:
                ages[branch][area].update(counts)

    reports = {
        "participacion_general": [
            {
                **asdict(area),
                "categoria": _CATEGORIES.get(branch, "Todas"),
                "rama": branch,
                **counts,
            }
            for area in _sorted_areas(participation)
            for branch, counts in sorted(participation[area].items())
        ],
        "cobertura_municipal": _coverage_rows(catalog, municipalities, state_code),
        "calidad_datos": [
            {**asdict(area), **{key: quality[area][key] for key in _QUALITY_FIELDS}}
            for area in _sorted_areas(quality)
        ],
    }
    for branch, name in _AGE_REPORTS.items():
        fields = ("jugadores", *_AGE_ISSUES, *(band[0] for band in _AGE_RANGES[branch]))
        if branch != "femenil":
            fields += ("otras_edades",)
        reports[name] = [
            {**asdict(area), **{key: ages[branch][area][key] for key in fields}}
            for area in _sorted_areas(ages[branch])
        ]
    return {
        "equipos": total_teams,
        "jugadores": total_players,
        "estados": len(states),
        "municipios_participantes": len(municipalities),
    }, reports


async def build_ctt_registration_report(
    session: Any,
    *,
    tournament_id: str,
    edition_year: int,
    age_cutoff: date,
    state_code: str = "",
    municipality_code: str = "",
    category: str = "",
) -> dict[str, Any]:
    """Read a single exact CTT edition and return aggregate counts only."""
    report: dict[str, Any] = {
        "available": False,
        "source": SOURCE,
        "edition_year": edition_year,
        "as_of_date": age_cutoff.isoformat(),
        "summary": None,
        "reports": {},
        "options": {},
    }
    try:
        catalog = load_ctt_municipality_catalog()
    except (OSError, ValueError, KeyError, TypeError):
        return {**report, "status": "CATALOG_UNAVAILABLE"}

    category = normalize_text(category)
    report.update(
        catalog_source=catalog.source,
        catalog_date=catalog.captured_on,
        catalog_url=catalog.url,
    )
    if (
        (state_code and state_code not in catalog.states)
        or (municipality_code and municipality_code not in catalog.municipalities)
        or (
            state_code
            and municipality_code
            and not municipality_code.startswith(state_code)
        )
        or (category and category not in _CATEGORIES)
    ):
        return {**report, "status": "FILTER_INVALID"}
    state_code = state_code or municipality_code[:2]
    report["options"] = _filter_options(catalog, state_code)
    report["filters"] = {
        "state_code": state_code,
        "municipality_code": municipality_code,
        "category": category,
    }
    try:
        tournament_id = str(UUID(str(tournament_id)))
    except (ValueError, TypeError, AttributeError):
        return {**report, "status": "SCOPE_MISSING"}
    scope = await probe_registration_scope(
        session, tournament_key=tournament_id, edition_year=edition_year
    )
    if scope["status"] != "CONFIGURED":
        return {**report, "status": scope["status"]}

    try:
        # A LEFT JOIN retains the edition row even with no committed teams.
        # The teams and their active roster counts come from one SQL snapshot.
        rows = await _rows(
            session,
            """SELECT CAST(t.id AS VARCHAR) AS team_id, t.state, t.municipality,
                      t.category, t.gender, p.birth_date,
                      COUNT(p.id) AS player_count
               FROM copa_telmex_tournament_editions e
               JOIN tournaments project ON project.id = e.tournament_id
               LEFT JOIN copa_telmex_teams t ON t.tournament_slug = e.roster_slug
               LEFT JOIN copa_telmex_players p ON p.team_id = t.id
                   AND p.governance_state IN ('ACTIVE', 'LEGACY_ACTIVE')
               WHERE CAST(e.tournament_id AS VARCHAR) = :tournament_id
                   AND e.edition_year = :edition_year AND e.roster_slug = :roster_slug
                   AND e.active AND project.active
               GROUP BY t.id, t.state, t.municipality, t.category,
                        t.gender, p.birth_date""",
            {
                "tournament_id": tournament_id,
                "edition_year": edition_year,
                "roster_slug": scope["roster_slug"],
            },
        )
    except Exception:
        return {**report, "status": "SOURCE_FAILED"}
    if not rows:
        return {**report, "status": "SCOPE_UNAVAILABLE"}
    summary, sections = _assemble_reports(
        rows,
        catalog,
        age_cutoff=age_cutoff,
        state_code=state_code,
        municipality_code=municipality_code,
        category=category,
    )
    return {
        **report,
        "available": True,
        "status": "AVAILABLE" if summary["equipos"] else "EMPTY",
        "tournament_id": tournament_id,
        "tournament_name": scope["tournament_name"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "reports": sections,
    }
