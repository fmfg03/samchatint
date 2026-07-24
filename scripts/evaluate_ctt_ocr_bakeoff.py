#!/usr/bin/env python3
"""Score private CTT OCR candidates without printing personal data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Mapping


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split()).casefold()


def _identity_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value))
    return "".join(
        ch for ch in normalized if ch.isalnum() and not unicodedata.combining(ch)
    )


def _date(value: Any) -> str:
    text = str(value or "").strip()
    for pattern in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%d.%m.%y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return _text(text)


def _field_value(container: Mapping[str, Any], name: str) -> Any:
    field = (container.get("fields") or {}).get(name)
    if isinstance(field, Mapping):
        return field.get("normalized_value") or field.get("raw_text")
    return field


def _players(payload: Mapping[str, Any]) -> Dict[int, Mapping[str, Any]]:
    result = {}
    for index, player in enumerate(payload.get("players") or [], start=1):
        slot = int(
            player.get("slot")
            or player.get("visible_player_number")
            or player.get("continuous_player_number")
            or index
        )
        result[slot] = player
    if result or not payload.get("slots"):
        return result
    for slot_payload in payload.get("slots") or []:
        if not slot_payload.get("occupied"):
            continue
        slot = int(slot_payload["slot"])
        name = " ".join(
            str(value).strip()
            for value in (
                _field_value(slot_payload, "given_names"),
                _field_value(slot_payload, "paternal_surname"),
                _field_value(slot_payload, "maternal_surname"),
            )
            if value
        )
        result[slot] = {
            "slot": slot,
            "name": name,
            "birth_date": _field_value(slot_payload, "birth_date"),
        }
    return result


def _team_name(payload: Mapping[str, Any]) -> Any:
    team = payload.get("team") or {}
    if isinstance(team, Mapping):
        return team.get("name") or _field_value(team, "name")
    return payload.get("team_name")


def _duplicate_identity_groups(
    players: Mapping[int, Mapping[str, Any]],
) -> list[list[int]]:
    identities: Dict[tuple[str, str], list[int]] = {}
    for slot, player in players.items():
        name = _identity_text(player.get("name"))
        birth = _date(player.get("birth_date"))
        if name and birth:
            identities.setdefault((name, birth), []).append(slot)
    return sorted(
        sorted(set(slots)) for slots in identities.values() if len(set(slots)) > 1
    )


def _possible_duplicate_identity_groups(
    players: Mapping[int, Mapping[str, Any]],
) -> list[list[int]]:
    exact = {tuple(group) for group in _duplicate_identity_groups(players)}
    groups = []
    items = sorted(players.items())
    for index, (left_slot, left) in enumerate(items):
        left_name = _identity_text(left.get("name"))
        left_birth = _date(left.get("birth_date"))
        for right_slot, right in items[index + 1 :]:
            right_name = _identity_text(right.get("name"))
            right_birth = _date(right.get("birth_date"))
            pair = (left_slot, right_slot)
            if pair in exact or not left_name or left_birth != right_birth:
                continue
            if SequenceMatcher(None, left_name, right_name).ratio() >= 0.70:
                groups.append([left_slot, right_slot])
    return groups


def score(
    ground_truth: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Dict[str, Any]:
    truth_players = _players(ground_truth)
    candidate_players = _players(candidate)
    duplicate_identity_groups = _duplicate_identity_groups(candidate_players)
    possible_duplicate_identity_groups = _possible_duplicate_identity_groups(
        candidate_players
    )
    expected_slots = set(truth_players)
    actual_slots = set(candidate_players)
    name_matches = 0
    date_matches = 0
    page_matches = {
        "front": {"expected": 0, "names": 0, "dates": 0},
        "back": {"expected": 0, "names": 0, "dates": 0},
    }
    for slot, truth in truth_players.items():
        actual = candidate_players.get(slot) or {}
        page = "front" if slot <= 8 else "back"
        name_match = _text(truth.get("name")) == _text(actual.get("name"))
        date_match = _date(truth.get("birth_date")) == _date(actual.get("birth_date"))
        name_matches += name_match
        date_matches += date_match
        page_matches[page]["expected"] += 1
        page_matches[page]["names"] += name_match
        page_matches[page]["dates"] += date_match
    denominator = max(1, len(truth_players))
    truth_team = _team_name(ground_truth)
    candidate_team = _team_name(candidate)
    empty_tail_materialized = sorted(
        slot for slot in actual_slots - expected_slots if slot in {17, 18, 19, 20}
    )
    return {
        "receipt_version": "ctt.bakeoff.score.v1",
        "ground_truth_sha256": hashlib.sha256(
            json.dumps(ground_truth, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "candidate_sha256": hashlib.sha256(
            json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "team_exact": _text(truth_team) == _text(candidate_team),
        "expected_player_count": len(truth_players),
        "detected_player_count": len(candidate_players),
        "slot_recall": len(expected_slots & actual_slots) / denominator,
        "exact_name_count": name_matches,
        "exact_name_rate": name_matches / denominator,
        "exact_birth_date_count": date_matches,
        "exact_birth_date_rate": date_matches / denominator,
        "page_exact_counts": page_matches,
        "invented_slots": sorted(actual_slots - expected_slots),
        "missing_slots": sorted(expected_slots - actual_slots),
        "empty_tail_materialized": empty_tail_materialized,
        "duplicate_identity_groups": duplicate_identity_groups,
        "possible_duplicate_identity_groups": possible_duplicate_identity_groups,
        "acceptance": {
            "team_exact": _text(truth_team) == _text(candidate_team),
            "sixteen_players": len(candidate_players) == 16,
            "zero_invented_players": not (actual_slots - expected_slots),
            "slots_17_20_not_materialized": not empty_tail_materialized,
            "zero_duplicate_identities": not (
                duplicate_identity_groups or possible_duplicate_identity_groups
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ground_truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    receipt = score(ground_truth, candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{args.output.name}.", dir=args.output.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                receipt,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, args.output)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    print(
        "BAKEOFF_SCORED "
        f"team_exact={receipt['team_exact']} "
        f"players={receipt['detected_player_count']} "
        f"names={receipt['exact_name_count']}/{receipt['expected_player_count']} "
        f"dates={receipt['exact_birth_date_count']}/{receipt['expected_player_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
