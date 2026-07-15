"""Apply explicitly selected canonical CTT fields to a legacy review draft."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

CANONICAL_REVIEW_SCHEMA = "ctt.canonical_review.v1"
MAX_CANONICAL_SELECTIONS = 100
TEAM_FIELDS = frozenset(
    {"name", "category", "gender", "league", "municipality", "state"}
)
MANAGER_FIELDS = frozenset({"name", "email"})
PLAYER_FIELDS = frozenset({"name", "birth_date", "curp"})
PLAYER_EVIDENCE_FIELDS = {
    "name": ("given_names", "paternal_surname", "maternal_surname"),
    "birth_date": ("birth_date",),
    "curp": ("curp",),
}
TEAM_EVIDENCE_FIELDS = {
    "name": ("team_name",),
    "category": ("category",),
    "gender": ("gender",),
    "league": ("league",),
    "municipality": ("municipality",),
    "state": ("state",),
}
MANAGER_EVIDENCE_FIELDS = {
    "name": ("representative_name",),
    "email": ("email",),
}
TEAM_DRAFT_FIELD_ATTRIBUTES = {
    "name": "name",
    "category": "category",
    "gender": "gender",
    "league": "league",
    "municipality": "municipality",
    "state": "state",
}
MANAGER_DRAFT_FIELD_ATTRIBUTES = {
    "name": "representative_name",
    "email": "email",
}


class CanonicalPromotionError(ValueError):
    """Reject an unsafe or stale canonical field promotion request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CanonicalPromotionResult:
    """Updated extraction plus immutable audit facts for applied fields."""

    extraction: Dict[str, Any]
    field_events: Tuple[Dict[str, Any], ...]
    canonical_hash: str
    document_sha256: str


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical_scalar(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CanonicalPromotionError(
            "canonical_value_invalid",
            "El sidecar contiene un valor canónico no escalar.",
        )
    cleaned = value.strip()
    return cleaned or None


def _selected_evidence(
    raw_evidence: Any,
    allowed_keys: Sequence[str],
) -> Dict[str, Any]:
    evidence = _mapping(raw_evidence)
    return {
        key: copy.deepcopy(evidence[key]) for key in allowed_keys if key in evidence
    }


def _canonical_draft_team_evidence(
    sidecar: Mapping[str, Any],
    *,
    attribute: str,
    evidence_key: str,
) -> Dict[str, Any]:
    canonical_draft = _mapping(sidecar.get("canonical_draft"))
    team = _mapping(canonical_draft.get("team"))
    fields = _mapping(team.get("fields"))
    observation = _mapping(fields.get(attribute))
    evidence = _mapping(observation.get("evidence"))
    return {evidence_key: copy.deepcopy(dict(evidence))} if evidence else {}


def _canonical_draft_player_evidence(
    sidecar: Mapping[str, Any],
    *,
    slot: int,
    allowed_keys: Sequence[str],
) -> Dict[str, Any]:
    canonical_draft = _mapping(sidecar.get("canonical_draft"))
    raw_slots = canonical_draft.get("slots")
    if not isinstance(raw_slots, list):
        return {}
    for raw_slot in raw_slots:
        slot_payload = _mapping(raw_slot)
        try:
            current_slot = int(slot_payload.get("slot") or 0)
        except (TypeError, ValueError):
            continue
        if current_slot != slot:
            continue
        fields = _mapping(slot_payload.get("fields"))
        result = {}
        for key in allowed_keys:
            evidence = _mapping(_mapping(fields.get(key)).get("evidence"))
            if evidence:
                result[key] = copy.deepcopy(dict(evidence))
        return result
    return {}


def _validated_sidecar(
    raw_payload: Any,
    expected_hash: str,
) -> Mapping[str, Any]:
    sidecar = _mapping(_mapping(raw_payload).get("canonical_shadow"))
    if (
        sidecar.get("schema_version") != CANONICAL_REVIEW_SCHEMA
        or sidecar.get("accepted") is not True
        or sidecar.get("authoritative") is not False
    ):
        raise CanonicalPromotionError(
            "canonical_sidecar_unavailable",
            "No existe un sidecar canónico aceptado para esta revisión.",
        )

    canonical_hash = str(sidecar.get("canonical_hash") or "").strip()
    if not canonical_hash:
        raise CanonicalPromotionError(
            "canonical_hash_missing",
            "El sidecar canónico no tiene una huella verificable.",
        )
    if not expected_hash or expected_hash != canonical_hash:
        raise CanonicalPromotionError(
            "canonical_sidecar_changed",
            "La lectura canónica cambió; recarga la revisión antes de aplicarla.",
        )
    return sidecar


def _unique_selections(selections: Iterable[str]) -> Tuple[str, ...]:
    unique = tuple(dict.fromkeys(str(item or "").strip() for item in selections))
    unique = tuple(item for item in unique if item)
    if not unique:
        raise CanonicalPromotionError(
            "canonical_fields_required",
            "Selecciona al menos un campo canónico.",
        )
    if len(unique) > MAX_CANONICAL_SELECTIONS:
        raise CanonicalPromotionError(
            "canonical_fields_limit",
            "La selección canónica excede el límite permitido.",
        )
    return unique


def _canonical_players_by_slot(
    sidecar: Mapping[str, Any],
) -> Dict[int, Mapping[str, Any]]:
    raw_players = sidecar.get("players")
    if not isinstance(raw_players, list):
        return {}
    players: Dict[int, Mapping[str, Any]] = {}
    for raw_player in raw_players:
        player = _mapping(raw_player)
        try:
            slot = int(player.get("slot") or 0)
        except (TypeError, ValueError):
            continue
        if slot > 0:
            players.setdefault(slot, player)
    return players


def _field_event(
    *,
    selection: str,
    path: str,
    before: Any,
    after: Any,
    canonical_hash: str,
    document_sha256: str,
    evidence: Mapping[str, Any],
    actor: Mapping[str, Optional[str]],
    promoted_at: str,
) -> Dict[str, Any]:
    return {
        "path": path,
        "selection": selection,
        "before": copy.deepcopy(before),
        "after": copy.deepcopy(after),
        "changed_by": actor.get("user_id"),
        "changed_role": actor.get("role"),
        "changed_at": promoted_at,
        "source": "canonical_promotion",
        "canonical_hash": canonical_hash,
        "document_sha256": document_sha256,
        "evidence": copy.deepcopy(dict(evidence)),
    }


def promote_canonical_fields(
    raw_payload: Any,
    legacy_extraction: Any,
    selections: Iterable[str],
    *,
    expected_hash: str,
    actor: Mapping[str, Optional[str]],
    promoted_at: str,
) -> CanonicalPromotionResult:
    """Apply allowlisted sidecar values without trusting values from the client."""

    sidecar = _validated_sidecar(raw_payload, expected_hash)
    canonical_hash = str(sidecar.get("canonical_hash") or "").strip()
    document_sha256 = str(sidecar.get("document_sha256") or "").strip()
    requested_fields = _unique_selections(selections)
    extraction = copy.deepcopy(dict(_mapping(legacy_extraction)))
    team = dict(_mapping(extraction.get("team")))
    manager = dict(_mapping(extraction.get("manager")))
    raw_legacy_players = extraction.get("players")
    legacy_players = (
        [dict(_mapping(player)) for player in raw_legacy_players]
        if isinstance(raw_legacy_players, list)
        else []
    )
    canonical_team = _mapping(sidecar.get("team"))
    canonical_manager = _mapping(sidecar.get("manager"))
    canonical_players = _canonical_players_by_slot(sidecar)
    events = []

    for selection in requested_fields:
        parts = selection.split(".")
        if len(parts) == 2 and parts[0] == "team" and parts[1] in TEAM_FIELDS:
            field = parts[1]
            target = team
            canonical_source = canonical_team
            path = f"team.{field}"
            evidence = _selected_evidence(
                canonical_source.get("field_evidence"),
                TEAM_EVIDENCE_FIELDS[field],
            )
            if not evidence:
                evidence = _canonical_draft_team_evidence(
                    sidecar,
                    attribute=TEAM_DRAFT_FIELD_ATTRIBUTES[field],
                    evidence_key=TEAM_EVIDENCE_FIELDS[field][0],
                )
        elif len(parts) == 2 and parts[0] == "manager" and parts[1] in MANAGER_FIELDS:
            field = parts[1]
            target = manager
            canonical_source = canonical_manager
            path = f"manager.{field}"
            evidence = _selected_evidence(
                canonical_source.get("field_evidence"),
                MANAGER_EVIDENCE_FIELDS[field],
            )
            if not evidence:
                evidence = _canonical_draft_team_evidence(
                    sidecar,
                    attribute=MANAGER_DRAFT_FIELD_ATTRIBUTES[field],
                    evidence_key=MANAGER_EVIDENCE_FIELDS[field][0],
                )
        elif len(parts) == 3 and parts[0] == "player" and parts[2] in PLAYER_FIELDS:
            try:
                slot = int(parts[1])
            except (TypeError, ValueError) as exc:
                raise CanonicalPromotionError(
                    "canonical_field_invalid",
                    "La selección contiene una ruta canónica inválida.",
                ) from exc
            if slot < 1 or slot > len(legacy_players):
                raise CanonicalPromotionError(
                    "canonical_player_slot_missing",
                    "La selección apunta a un jugador que no existe en el borrador.",
                )
            canonical_player = canonical_players.get(slot)
            if canonical_player is None:
                raise CanonicalPromotionError(
                    "canonical_player_missing",
                    "La lectura canónica ya no contiene el jugador seleccionado.",
                )
            field = parts[2]
            target = legacy_players[slot - 1]
            canonical_source = canonical_player
            path = f"players[{slot - 1}].{field}"
            evidence = _selected_evidence(
                canonical_source.get("field_evidence"),
                PLAYER_EVIDENCE_FIELDS[field],
            )
            if not evidence:
                evidence = _canonical_draft_player_evidence(
                    sidecar,
                    slot=slot,
                    allowed_keys=PLAYER_EVIDENCE_FIELDS[field],
                )
        else:
            raise CanonicalPromotionError(
                "canonical_field_invalid",
                "La selección contiene una ruta canónica no permitida.",
            )

        before = target.get(field)
        after = _canonical_scalar(canonical_source.get(field))
        if before == after:
            continue
        if not evidence:
            raise CanonicalPromotionError(
                "canonical_evidence_missing",
                "El campo canónico seleccionado no conserva evidencia verificable.",
            )
        target[field] = after
        events.append(
            _field_event(
                selection=selection,
                path=path,
                before=before,
                after=after,
                canonical_hash=canonical_hash,
                document_sha256=document_sha256,
                evidence=evidence,
                actor=actor,
                promoted_at=promoted_at,
            )
        )

    if not events:
        raise CanonicalPromotionError(
            "canonical_no_changes",
            "Los campos seleccionados ya coinciden con el borrador.",
        )

    extraction["team"] = team
    extraction["manager"] = manager or None
    extraction["players"] = legacy_players
    return CanonicalPromotionResult(
        extraction=extraction,
        field_events=tuple(events),
        canonical_hash=canonical_hash,
        document_sha256=document_sha256,
    )
