"""Match cédula locations to the published INEGI geographic codes."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from samchat.assistant.tournament_registration_reports import normalize_text

CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "samchat"
    / "data"
    / "mexico_municipality_catalog_2026-11-18.json"
)

# Common names for the same entity, including historical names present on cédulas.
_STATE_ALIASES = {
    "05": ("Coahuila",),
    "09": ("Distrito Federal", "DF", "Ciudad de Mexico DF"),
    "15": ("Estado de México", "Edo. de México", "Edo. México", "Edomex"),
    "16": ("Michoacán",),
    "30": ("Veracruz",),
}


@dataclass(frozen=True)
class CttGeography:
    state_code: str = ""
    state_name: str = "Sin estado identificado"
    municipality_code: str = ""
    municipality_name: str = "Sin municipio identificado"


@dataclass(frozen=True)
class MunicipalityCatalog:
    source: str
    url: str
    captured_on: str
    states: dict[str, str]
    municipalities: dict[str, str]
    state_totals: dict[str, int]
    state_codes_by_label: dict[str, str]
    municipality_codes_by_label: dict[tuple[str, str], str]


def _state_key(value: str | None) -> str:
    return normalize_text(value).replace(".", "")


def _unambiguous_codes(labels: dict) -> dict:
    return {
        label: next(iter(codes)) for label, codes in labels.items() if len(codes) == 1
    }


@lru_cache(maxsize=1)
def load_ctt_municipality_catalog() -> MunicipalityCatalog:
    """Load the local INEGI snapshot used for both percentage denominators."""
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    states: dict[str, str] = {}
    municipalities: dict[str, str] = {}
    state_labels: dict[str, set[str]] = defaultdict(set)
    municipality_labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for state_code, entry in payload["states"].items():
        states[state_code] = entry["name"]
        for name in (
            entry["name"],
            entry["abbreviation"],
            *_STATE_ALIASES.get(state_code, ()),
        ):
            state_labels[_state_key(name)].add(state_code)
        for code, name in entry["municipalities"].items():
            if (
                len(code) != 5
                or not code.isascii()
                or not code.isdigit()
                or code[:2] != state_code
            ):
                raise ValueError("Clave municipal inválida en el catálogo INEGI.")
            municipalities[code] = name
            municipality_labels[state_code, normalize_text(name)].add(code)
    state_totals = dict(Counter(code[:2] for code in municipalities))
    if (
        set(states) != {f"{code:02}" for code in range(1, 33)}
        or states.keys() != state_totals.keys()
    ):
        raise ValueError(
            "El catálogo INEGI no contiene municipios para las 32 entidades."
        )
    return MunicipalityCatalog(
        source=payload["source"],
        url=payload["url"],
        captured_on=payload["captured_on"],
        states=states,
        municipalities=municipalities,
        state_totals=state_totals,
        state_codes_by_label=_unambiguous_codes(state_labels),
        municipality_codes_by_label=_unambiguous_codes(municipality_labels),
    )


def resolve_ctt_geography(state: str | None, municipality: str | None) -> CttGeography:
    """Resolve a municipality only inside its explicitly identified state."""
    catalog = load_ctt_municipality_catalog()
    state_value = normalize_text(state)
    state_code = (
        state_value
        if state_value in catalog.states
        else catalog.state_codes_by_label.get(_state_key(state_value))
    )
    if not state_code:
        return CttGeography()
    municipal_key = normalize_text(municipality)
    if municipal_key[:2] == state_code and municipal_key in catalog.municipalities:
        municipality_code = municipal_key
    else:
        municipality_code = catalog.municipality_codes_by_label.get(
            (state_code, municipal_key), ""
        )
    if not municipality_code:
        return CttGeography(
            state_code=state_code, state_name=catalog.states[state_code]
        )
    return CttGeography(
        state_code=state_code,
        state_name=catalog.states[state_code],
        municipality_code=municipality_code,
        municipality_name=catalog.municipalities[municipality_code],
    )
