"""Shared helpers for tournament-dependent phase options."""

from typing import Any, List


DEFAULT_TOURNAMENT_ETAPAS = [
    "Colectiva",
    "Estatal",
    "Nacional",
    "Viaje de Campeones",
    "No Aplica",
]


def get_tournament_scope_options(source: Any) -> dict[str, List[str]]:
    """Return etapas and categorias configured on a gastos tournament row."""
    if isinstance(source, dict):
        etapas_raw = source.get("etapas")
        categorias_raw = source.get("categorias")
    else:
        etapas_raw = getattr(source, "etapas", None)
        categorias_raw = getattr(source, "categorias", None)
    return {
        "etapas": _clean_tournament_label_list(etapas_raw),
        "categorias": _clean_tournament_label_list(categorias_raw),
    }


def get_tournament_scope_labels(source: Any) -> List[str]:
    """Ordered etapas + categorias labels for budget partida scope pickers."""
    options = get_tournament_scope_options(source)
    labels: List[str] = []
    for group in ("etapas", "categorias"):
        for label in options[group]:
            if label not in labels:
                labels.append(label)
    return labels


def _clean_tournament_label_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    cleaned: List[str] = []
    for item in values:
        value = str(item).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def tournament_scope_config_changed(
    *,
    previous_etapas: Any,
    previous_categorias: Any,
    next_etapas: Any,
    next_categorias: Any,
) -> bool:
    """True when etapas or categorias labels changed (order-insensitive compare)."""
    before = get_tournament_scope_options(
        {"etapas": previous_etapas, "categorias": previous_categorias}
    )
    after = get_tournament_scope_options(
        {"etapas": next_etapas, "categorias": next_categorias}
    )
    return before != after


def get_tournament_etapas(source: Any) -> List[str]:
    """Return cleaned tournament etapas or the default list when not configured."""
    etapas = source
    if isinstance(source, dict):
        etapas = source.get("etapas")
    elif hasattr(source, "etapas"):
        etapas = getattr(source, "etapas", None)

    if isinstance(etapas, list):
        cleaned: List[str] = []
        for item in etapas:
            value = str(item).strip()
            if value and value not in cleaned:
                cleaned.append(value)
        if cleaned:
            return cleaned

    return DEFAULT_TOURNAMENT_ETAPAS.copy()
