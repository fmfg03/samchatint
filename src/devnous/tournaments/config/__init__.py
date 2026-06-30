from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml


ACTIVE_TOURNAMENT_ID = "liga_telmex_telcel"
ACTIVE_TOURNAMENT_SCOPE = "beisbol"
ACTIVE_TOURNAMENT_CONFIG_PATH = Path(__file__).resolve().parent / "liga_telmex_telcel.yaml"


@lru_cache(maxsize=1)
def load_active_tournament_config() -> Dict[str, Any]:
    """Load the single canonical tournament configuration."""
    with ACTIVE_TOURNAMENT_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("Active tournament config must be a mapping")
    return payload


def active_tournament_stage_names() -> List[str]:
    """Return ordered stage names from the canonical tournament config."""
    config = load_active_tournament_config()
    raw_stages = config.get("stages") or []
    stage_names: List[str] = []
    for stage in raw_stages:
        if not isinstance(stage, dict):
            continue
        name = str(stage.get("name") or "").strip()
        if name:
            stage_names.append(name)
    return stage_names


def active_tournament_catalog_payload() -> Dict[str, Any]:
    """Return the fields that should be mirrored into the gastos catalog."""
    config = load_active_tournament_config()
    return {
        "tournament_id": str(config.get("tournament_id") or ACTIVE_TOURNAMENT_ID).strip()
        or ACTIVE_TOURNAMENT_ID,
        "scope": ACTIVE_TOURNAMENT_SCOPE,
        "name": str(config.get("name") or "Liga Telmex Telcel 2026").strip()
        or "Liga Telmex Telcel 2026",
        "description": str(config.get("description") or "").strip() or None,
        "display_order": 0,
        "etapas": active_tournament_stage_names(),
        "cuenta_contable_relacionada": None,
    }


__all__ = [
    "ACTIVE_TOURNAMENT_CONFIG_PATH",
    "ACTIVE_TOURNAMENT_ID",
    "ACTIVE_TOURNAMENT_SCOPE",
    "active_tournament_catalog_payload",
    "active_tournament_stage_names",
    "load_active_tournament_config",
]
