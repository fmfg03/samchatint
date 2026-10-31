"""Canonical pure review incident policy shared by web and protected intake.

Extracted without changing rules from the current registration dashboard.
It does not authenticate reviewers, confer authority or grant eligibility.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from devnous.tournaments.core.ocr_integrity import (
    hamming_distance_hex,
    hashes_look_duplicate,
)

MINIMUM_ELIGIBLE_PLAYERS = 16

REGISTRATION_INCIDENTS_SCHEMA_VERSION = "registration_incidents.v1"

PHOTO_DUPLICATE_MAX_HASH_DISTANCE = 4


def _player_has_usable_signal(player: Dict[str, Any]) -> bool:
    name = (player.get("name") or "").strip()
    birth_date = (player.get("birth_date") or "").strip()
    curp = (player.get("curp") or "").strip()
    return bool(
        name
        or birth_date
        or curp
        or bool(player.get("needs_review"))
        or float(player.get("confidence") or 0.0) > 0.0
    )


def _draft_player_ref(player_index: int) -> str:
    return f"draft-player-{player_index:02d}"


def _new_registration_incident(
    *,
    player_ref: str,
    incident_type: str,
    blocks_player_eligibility: bool,
    message: str,
    evidence: Optional[Dict[str, Any]] = None,
    requires_operations_review: bool = True,
) -> Dict[str, Any]:
    normalized_evidence = evidence or {}
    incident_binding = json.dumps(
        {
            "player_ref": player_ref,
            "incident_type": incident_type,
            "blocks_player_eligibility": bool(blocks_player_eligibility),
            "evidence": normalized_evidence,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "incident_id": "reg-incident-" + hashlib.sha256(incident_binding).hexdigest(),
        "player_ref": player_ref,
        "incident_type": incident_type,
        "blocks_player_eligibility": bool(blocks_player_eligibility),
        "blocks_team_registration": False,
        "requires_operations_review": bool(requires_operations_review),
        "message": message,
        "evidence": normalized_evidence,
        "status": "OPEN",
    }


def _append_player_incident(
    incidents_by_player: Dict[int, List[Dict[str, Any]]],
    player_index: int,
    *,
    incident_type: str,
    blocks_player_eligibility: bool,
    message: str,
    evidence: Optional[Dict[str, Any]] = None,
    requires_operations_review: bool = True,
) -> None:
    incidents_by_player.setdefault(player_index, []).append(
        _new_registration_incident(
            player_ref=_draft_player_ref(player_index),
            incident_type=incident_type,
            blocks_player_eligibility=blocks_player_eligibility,
            message=message,
            evidence=evidence,
            requires_operations_review=requires_operations_review,
        )
    )


def _photo_duplicate_incident_type(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> Optional[Tuple[str, Optional[int]]]:
    left_sha = left.get("photo_sha256")
    right_sha = right.get("photo_sha256")
    if left_sha and right_sha and left_sha == right_sha:
        return "PHOTO_EXACT_DUPLICATE", 0
    distance = hamming_distance_hex(
        left.get("photo_ahash"),
        right.get("photo_ahash"),
    )
    if distance <= PHOTO_DUPLICATE_MAX_HASH_DISTANCE:
        return "PHOTO_PERCEPTUAL_DUPLICATE", distance
    return None


def _build_photo_duplicate_incidents(
    *,
    players_payload: List[Dict[str, Any]],
    photo_artifacts: Optional[Dict[int, Dict[str, Any]]] = None,
    existing_photo_records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[int, List[Dict[str, Any]]]:
    incidents_by_player: Dict[int, List[Dict[str, Any]]] = {}
    artifacts = photo_artifacts or {}
    indexes = sorted(idx for idx in artifacts if artifacts.get(idx))

    for pos, left_idx in enumerate(indexes):
        left = artifacts.get(left_idx) or {}
        for right_idx in indexes[pos + 1 :]:
            right = artifacts.get(right_idx) or {}
            match = _photo_duplicate_incident_type(left, right)
            if not match:
                continue
            incident_type, distance = match
            left_name = (
                players_payload[left_idx - 1].get("name")
                if left_idx - 1 < len(players_payload)
                else None
            ) or ""
            right_name = (
                players_payload[right_idx - 1].get("name")
                if right_idx - 1 < len(players_payload)
                else None
            ) or ""
            _append_player_incident(
                incidents_by_player,
                left_idx,
                incident_type=incident_type,
                blocks_player_eligibility=True,
                message="La fotografia coincide con otro jugador del draft actual.",
                evidence={
                    "matching_player_ref": _draft_player_ref(right_idx),
                    "matching_player_name": right_name,
                    "hash_distance": distance,
                    "scope": "current_draft",
                },
            )
            _append_player_incident(
                incidents_by_player,
                right_idx,
                incident_type=incident_type,
                blocks_player_eligibility=True,
                message="La fotografia coincide con otro jugador del draft actual.",
                evidence={
                    "matching_player_ref": _draft_player_ref(left_idx),
                    "matching_player_name": left_name,
                    "hash_distance": distance,
                    "scope": "current_draft",
                },
            )

    for idx in indexes:
        artifact = artifacts.get(idx) or {}
        for existing in existing_photo_records or []:
            if not hashes_look_duplicate(
                sha256_left=artifact.get("photo_sha256"),
                sha256_right=existing.get("photo_sha256"),
                ahash_left=artifact.get("photo_ahash"),
                ahash_right=existing.get("photo_ahash"),
                max_distance=PHOTO_DUPLICATE_MAX_HASH_DISTANCE,
            ):
                continue
            match = _photo_duplicate_incident_type(artifact, existing)
            if not match:
                continue
            incident_type, distance = match
            _append_player_incident(
                incidents_by_player,
                idx,
                incident_type=incident_type,
                blocks_player_eligibility=True,
                message="La fotografia coincide con otro jugador del torneo.",
                evidence={
                    "matching_player_ref": existing.get("player_ref"),
                    "matching_player_name": existing.get("player_name"),
                    "matching_team_ref": existing.get("team_ref"),
                    "matching_team_name": existing.get("team_name"),
                    "hash_distance": distance,
                    "scope": "tournament_slug",
                    "tournament_slug": existing.get("tournament_slug"),
                },
            )
            break

    return incidents_by_player


def _merge_player_incidents(
    *incident_maps: Dict[int, List[Dict[str, Any]]],
) -> Dict[int, List[Dict[str, Any]]]:
    merged: Dict[int, List[Dict[str, Any]]] = {}
    for incident_map in incident_maps:
        for idx, incidents in (incident_map or {}).items():
            merged.setdefault(idx, []).extend(list(incidents or []))
    return merged


def _build_registration_incident_policy(
    extraction: Dict[str, Any],
    *,
    minimum_roster: int = MINIMUM_ELIGIBLE_PLAYERS,
    photo_artifacts: Optional[Dict[int, Dict[str, Any]]] = None,
    existing_photo_records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    players = [
        player
        for player in list(extraction.get("players") or [])
        if _player_has_usable_signal(player)
    ]
    base_incidents: Dict[int, List[Dict[str, Any]]] = {}

    for idx, player in enumerate(players, 1):
        name = (player.get("name") or "").strip()
        birth_date = (player.get("birth_date") or "").strip()
        raw_curp = (player.get("curp") or "").strip().upper()
        curp, curp_truncated = _normalize_curp_for_storage(raw_curp)
        confidence = float(player.get("confidence") or 0.0)
        needs_review = bool(player.get("needs_review"))

        if not name:
            _append_player_incident(
                base_incidents,
                idx,
                incident_type="PLAYER_MISSING_NAME",
                blocks_player_eligibility=True,
                message=f"El jugador {idx} no tiene nombre usable.",
                evidence={"field": "name"},
            )
        if (
            not birth_date
            or _birth_date_has_two_digit_year(birth_date)
            or _parse_birth_date(birth_date) is None
        ):
            _append_player_incident(
                base_incidents,
                idx,
                incident_type="PLAYER_INVALID_BIRTHDATE",
                blocks_player_eligibility=True,
                message=(
                    f"El jugador {idx} tiene una fecha de nacimiento "
                    "invalida o ambigua."
                ),
                evidence={"field": "birth_date", "observed_value": birth_date},
            )
        if raw_curp and (curp_truncated or len(curp or "") != 18):
            _append_player_incident(
                base_incidents,
                idx,
                incident_type="CURP_TRUNCATED_OR_INVALID_SEVERE",
                blocks_player_eligibility=True,
                message=f"El CURP del jugador {idx} esta truncado o no es confiable.",
                evidence={"field": "curp", "observed_value": raw_curp},
            )
        if confidence < 0.7 or needs_review:
            _append_player_incident(
                base_incidents,
                idx,
                incident_type="LOW_CONFIDENCE_FIELD",
                blocks_player_eligibility=False,
                message=f"El jugador {idx} requiere revision por baja confianza OCR.",
                evidence={"confidence": confidence, "needs_review": needs_review},
            )

    incidents_by_player = _merge_player_incidents(
        base_incidents,
        _build_photo_duplicate_incidents(
            players_payload=players,
            photo_artifacts=photo_artifacts,
            existing_photo_records=existing_photo_records,
        ),
    )

    cleared_players = 0
    pending_nonblocking_players = 0
    pending_blocking_players = 0
    player_results: List[Dict[str, Any]] = []
    for idx, player in enumerate(players, 1):
        incidents = list(incidents_by_player.get(idx) or [])
        blocks_player = any(
            bool(item.get("blocks_player_eligibility")) for item in incidents
        )
        if not incidents:
            status = "CLEARED"
            counts_toward_minimum = True
            cleared_players += 1
        else:
            status = "INCIDENT_PENDING"
            counts_toward_minimum = not blocks_player
            if blocks_player:
                pending_blocking_players += 1
            else:
                pending_nonblocking_players += 1

        player_results.append(
            {
                "player_ref": _draft_player_ref(idx),
                "player_index": idx,
                "name": (player.get("name") or "").strip(),
                "birth_date": (player.get("birth_date") or "").strip(),
                "eligibility_status": status,
                "counts_toward_minimum": counts_toward_minimum,
                "incidents": incidents,
            }
        )

    eligible_players = cleared_players + pending_nonblocking_players
    incident_count = sum(len(item.get("incidents") or []) for item in player_results)
    if eligible_players < minimum_roster:
        team_decision = "PENDING_MINIMUM_ROSTER"
    elif incident_count:
        team_decision = "REGISTERED_WITH_INCIDENTS"
    else:
        team_decision = "REGISTERED"

    return {
        "schema_version": REGISTRATION_INCIDENTS_SCHEMA_VERSION,
        "minimum_roster": minimum_roster,
        "team_decision": team_decision,
        "summary": {
            "total_players": len(players),
            "cleared_players": cleared_players,
            "pending_nonblocking_players": pending_nonblocking_players,
            "pending_blocking_players": pending_blocking_players,
            "rejected_players": 0,
            "eligible_players": eligible_players,
            "incident_count": incident_count,
        },
        "player_results": player_results,
    }


def _split_player_name(full_name: str) -> Tuple[str, str]:
    clean = " ".join((full_name or "").split()).strip()
    if not clean:
        return "", ""
    parts = clean.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _parse_birth_date(value: Optional[str]) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_curp_for_storage(value: Optional[str]) -> Tuple[Optional[str], bool]:
    cleaned = "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())
    if not cleaned:
        return None, False
    truncated = len(cleaned) > 18
    return cleaned[:18], truncated


def _birth_date_has_two_digit_year(value: Optional[str]) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    for separator in ("/", "-", "."):
        parts = text.split(separator)
        if (
            len(parts) == 3
            and len(parts[-1]) == 2
            and all(part.isdigit() for part in parts)
        ):
            return True
    return False
