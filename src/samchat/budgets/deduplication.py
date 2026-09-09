"""Pure helpers for safe budget-concept reconciliation.

The database historically accepted more than one ``concept_key`` convention.
These helpers deliberately use the business identity used in selectors instead
of that implementation-specific key.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from typing import Any, Iterable


_INVALID_IDENTIFIER_VALUES = frozenset({"false", "null", "none", "undefined"})


def normalize_budget_identity_value(value: Any) -> str:
    """Return a comparison key without accepting booleans as identifiers."""
    if isinstance(value, bool) or value is None:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value)).encode(
        "ascii", "ignore"
    ).decode("ascii")
    normalized = normalized.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return "" if normalized in _INVALID_IDENTIFIER_VALUES else normalized


def is_valid_budget_concept_identifier(value: Any) -> bool:
    """Accept an omitted optional ID or UUID; reject booleans and text IDs."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    if isinstance(value, bool):
        return False
    try:
        uuid.UUID(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def budget_concept_phase_identity(metadata: Any) -> str:
    """Extract the first visible phase/subproject from catalog metadata."""
    payload = metadata if isinstance(metadata, dict) else {}
    for key in ("applicable_phase_labels", "applicable_subproject_labels"):
        labels = payload.get(key)
        if isinstance(labels, list):
            for label in labels:
                normalized = normalize_budget_identity_value(label)
                if normalized:
                    return normalized
    return normalize_budget_identity_value(payload.get("ssot_subproyecto"))


def budget_concept_semantic_identity(
    row: dict[str, Any],
) -> tuple[str, ...] | None:
    """Build a stable partida identity, or return ``None`` if unsafe."""
    tournament_id = normalize_budget_identity_value(row.get("tournament_id"))
    direction = (
        normalize_budget_identity_value(row.get("budget_direction"))
        or "expense"
    )
    concept_name = normalize_budget_identity_value(row.get("concept_name"))
    phase = budget_concept_phase_identity(row.get("metadata"))
    account = normalize_budget_identity_value(
        row.get("cuenta_contable_id") or row.get("cuenta_contable_codigo")
    )
    if not tournament_id or not concept_name:
        return None
    return (tournament_id, direction, concept_name, phase, account)


def duplicate_budget_concept_groups(
    rows: Iterable[dict[str, Any]],
) -> list[tuple[tuple[str, ...], list[dict[str, Any]]]]:
    """Group active rows whose business identities are exactly the same."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("active", True):
            continue
        identity = budget_concept_semantic_identity(row)
        if identity is not None:
            groups[identity].append(row)
    return sorted(
        (
            (identity, members)
            for identity, members in groups.items()
            if len(members) > 1
        ),
        key=lambda item: item[0],
    )


def choose_canonical_budget_concept(
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Choose an SSOT-backed row first, preserving the most-linked record."""
    candidates = list(rows)
    if not candidates:
        raise ValueError("Se requiere al menos una partida para consolidar.")

    def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
        source_rank = 0 if row.get("source") == "excel_truth_rebuild" else 1
        reference_count = int(row.get("reference_count") or 0)
        return (source_rank, -reference_count, str(row.get("id") or ""))

    return sorted(candidates, key=sort_key)[0]
