"""Build a safe, read-only view model for canonical CTT review output."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, Mapping, Optional, Sequence
from urllib.parse import quote

CANONICAL_REVIEW_SCHEMA = "ctt.canonical_review.v1"
FIELD_LABELS = {
    "name": "nombre",
    "category": "categoría",
    "gender": "rama",
    "league": "liga",
    "municipality": "municipio",
    "state": "estado",
    "birth_date": "fecha de nacimiento",
    "curp": "CURP",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _confidence_pct(value: Any) -> str:
    try:
        confidence = float(value or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return f"{max(0.0, min(1.0, confidence)) * 100:.0f}%"


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return max(1, default)


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalized_text(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _different_fields(
    canonical: Mapping[str, Any],
    legacy: Mapping[str, Any],
    fields: Sequence[str],
) -> list[str]:
    return [
        field
        for field in fields
        if _normalized_text(canonical.get(field)) != _normalized_text(legacy.get(field))
    ]


def _difference_rows(
    canonical: Mapping[str, Any],
    legacy: Mapping[str, Any],
    fields: Sequence[str],
) -> list[Dict[str, str]]:
    return [
        {
            "field": field,
            "label": FIELD_LABELS.get(field, field),
            "legacy_value": _text(legacy.get(field)),
            "canonical_value": _text(canonical.get(field)),
        }
        for field in _different_fields(canonical, legacy, fields)
    ]


def _private_preview_url(preview: Mapping[str, Any]) -> Optional[str]:
    relative_path = _text(preview.get("relative_path")).replace("\\", "/")
    if not relative_path:
        return None
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 4
        or path.parts[0] != "review_sessions"
        or path.parts[2] != "canonical_shadow"
    ):
        return None
    return f"/photos/{quote(path.as_posix(), safe='/')}"


def build_canonical_review_view(
    raw_payload: Any,
    legacy_extraction: Any = None,
) -> Optional[Dict[str, Any]]:
    """Return a defensive presentation model for an accepted shadow sidecar.

    The returned model never exposes a write action and only accepts preview
    paths inside the private canonical-shadow directory of a review session.
    """

    sidecar = _mapping(_mapping(raw_payload).get("canonical_shadow"))
    if (
        sidecar.get("schema_version") != CANONICAL_REVIEW_SCHEMA
        or sidecar.get("accepted") is not True
        or sidecar.get("authoritative") is not False
    ):
        return None

    legacy = _mapping(legacy_extraction)
    canonical_team = _mapping(sidecar.get("team"))
    legacy_team = _mapping(legacy.get("team"))
    team_difference_fields = _different_fields(
        canonical_team,
        legacy_team,
        ("name", "category", "gender", "league", "municipality", "state"),
    )
    team_differences = _difference_rows(
        canonical_team,
        legacy_team,
        ("name", "category", "gender", "league", "municipality", "state"),
    )

    legacy_players = legacy.get("players")
    if not isinstance(legacy_players, list):
        legacy_players = []

    raw_players = sidecar.get("players")
    if not isinstance(raw_players, list):
        raw_players = []

    canonical_players_by_slot: Dict[int, Mapping[str, Any]] = {}
    for index, raw_player in enumerate(raw_players, 1):
        player = _mapping(raw_player)
        slot = _positive_int(player.get("slot"), index)
        canonical_players_by_slot.setdefault(slot, player)

    canonical_slots = set(canonical_players_by_slot)
    legacy_slots = set(range(1, len(legacy_players) + 1))
    roster_difference_slots = canonical_slots.symmetric_difference(legacy_slots)
    comparison_slots = sorted(canonical_slots | legacy_slots)

    players: list[Dict[str, Any]] = []
    player_difference_count = 0
    for slot in comparison_slots:
        player = canonical_players_by_slot.get(slot, {})
        legacy_player = (
            _mapping(legacy_players[slot - 1]) if slot - 1 < len(legacy_players) else {}
        )
        missing_from_canonical = slot not in canonical_slots
        missing_from_legacy = slot not in legacy_slots
        roster_difference = slot in roster_difference_slots
        difference_fields = _different_fields(
            player,
            legacy_player,
            ("name", "birth_date", "curp"),
        )
        differences = _difference_rows(
            player,
            legacy_player,
            ("name", "birth_date", "curp"),
        )
        player_difference_count += len(difference_fields) + int(roster_difference)
        validation_codes = player.get("validation_codes")
        if not isinstance(validation_codes, list):
            validation_codes = []
        players.append(
            {
                "slot": slot,
                "source_page": _positive_int(player.get("source_page"), 1),
                "source_slot": _positive_int(player.get("source_slot"), slot),
                "name": _text(player.get("name")),
                "birth_date": _text(player.get("birth_date")),
                "curp": _text(player.get("curp")),
                "confidence_pct": _confidence_pct(player.get("confidence")),
                "requires_review": bool(player.get("requires_review"))
                or roster_difference,
                "validation_codes": [
                    _text(code) for code in validation_codes if _text(code)
                ],
                "photo_url": _private_preview_url(
                    _mapping(player.get("photo_preview"))
                ),
                "legacy": {
                    key: _text(legacy_player.get(key))
                    for key in ("name", "birth_date", "curp")
                },
                "differences": differences,
                "difference_fields": difference_fields,
                "difference_labels": [
                    FIELD_LABELS.get(field, field) for field in difference_fields
                ],
                "roster_difference": roster_difference,
                "missing_from_canonical": missing_from_canonical,
                "missing_from_legacy": missing_from_legacy,
                "roster_difference_label": (
                    "Ausente en lectura canónica"
                    if missing_from_canonical
                    else "Ausente en borrador actual" if missing_from_legacy else ""
                ),
                "matches_legacy": not difference_fields and not roster_difference,
            }
        )

    report = _mapping(sidecar.get("report"))
    manager = _mapping(sidecar.get("manager"))
    return {
        "schema_version": CANONICAL_REVIEW_SCHEMA,
        "authoritative": False,
        "canonical_hash": _text(sidecar.get("canonical_hash")),
        "team": {
            key: _text(canonical_team.get(key))
            for key in (
                "name",
                "category",
                "gender",
                "league",
                "municipality",
                "state",
            )
        },
        "legacy_team": {
            key: _text(legacy_team.get(key))
            for key in (
                "name",
                "category",
                "gender",
                "league",
                "municipality",
                "state",
            )
        },
        "manager": {
            "name": _text(manager.get("name")),
            "email": _text(manager.get("email")),
            "requires_review": bool(manager.get("requires_review")),
        },
        "players": players,
        "player_count": len(canonical_slots),
        "legacy_player_count": len(legacy_players),
        "comparison_player_count": len(players),
        "roster_difference_count": len(roster_difference_slots),
        "review_count": _nonnegative_int(report.get("review_count")),
        "team_differences": team_differences,
        "team_difference_fields": team_difference_fields,
        "team_difference_labels": [
            FIELD_LABELS.get(field, field) for field in team_difference_fields
        ],
        "difference_count": len(team_difference_fields) + player_difference_count,
        "difference_player_count": sum(
            1 for player in players if not player["matches_legacy"]
        ),
        "matching_player_count": sum(
            1 for player in players if player["matches_legacy"]
        ),
        "matches_legacy": not team_difference_fields and not player_difference_count,
    }
