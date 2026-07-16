"""One-shot authority for governed SamChat replicas in Supabase."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional


_ISSUER = object()
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_DIGEST = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")


class SupabaseAuthorityDenied(RuntimeError):
    """A Supabase mutation is not bound to an exact Zaubern decision."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _event_hash(event: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(event)).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _pick(row: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return (
            value.date().isoformat()
            if isinstance(value, datetime)
            else value.isoformat()
        )
    raw = _text(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return "2012-01-01"


def normalize_replica_roster(
    players: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the identity projection that Supabase is allowed to materialize."""
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(players, start=1):
        if not isinstance(raw, Mapping):
            raise SupabaseAuthorityDenied(
                "SUPABASE_REPLICATION_SCOPE_MISMATCH",
                f"player slot {index} is not an object",
            )
        paternal = _text(_pick(raw, ("paternal_surname", "apellido_paterno")))
        maternal = _text(_pick(raw, ("maternal_surname", "apellido_materno")))
        last_name = _text(_pick(raw, ("last_name", "apellido", "apellidos")))
        if not last_name:
            last_name = " ".join(value for value in (paternal, maternal) if value)
        normalized.append(
            {
                "slot": index,
                "first_name": _text(
                    _pick(raw, ("first_name", "nombre", "nombres", "name"))
                )
                or "Jugador",
                "last_name": last_name or f"#{index}",
                "birth_date": _date_text(
                    _pick(
                        raw, ("birth_date", "fecha_nacimiento", "nacimiento", "fecha")
                    )
                ),
                "curp": _text(_pick(raw, ("curp",))).upper() or None,
                "paternal_surname": paternal or None,
                "maternal_surname": maternal or None,
            }
        )
    return normalized


def replica_roster_hash(players: Iterable[Mapping[str, Any]]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(_canonical(normalize_replica_roster(players))).hexdigest()
    )


def _scope(
    *,
    operation: str,
    tournament_key: str,
    tournament_slug: Optional[str],
    tournament_name: Optional[str],
    category_id: Optional[str],
    category_name: Optional[str],
    target_team_id: Optional[str],
    source_team_id: str,
    team_name: str,
) -> Dict[str, str]:
    return {
        "operation": _text(operation),
        "tournament_key": _text(tournament_key),
        "tournament_slug": _text(tournament_slug),
        "tournament_name": _text(tournament_name),
        "category_id": _text(category_id),
        "category_name": _text(category_name),
        "target_team_id": _text(target_team_id),
        "source_team_id": _text(source_team_id),
        "team_name": _text(team_name),
    }


class SupabaseWritePermit:
    """Opaque proof accepted by lower-level legacy mutation helpers."""

    def __init__(self, *, issuer: object, replication_receipt_id: str):
        if issuer is not _ISSUER:
            raise SupabaseAuthorityDenied(
                "SUPABASE_REPLICATION_AUTHORITY_INVALID",
                "write permits can only be derived from a consumed capability",
            )
        self.replication_receipt_id = replication_receipt_id


def require_supabase_write_permit(permit: Optional[SupabaseWritePermit]) -> str:
    if not isinstance(permit, SupabaseWritePermit):
        raise SupabaseAuthorityDenied(
            "SUPABASE_REPLICATION_AUTHORITY_REQUIRED",
            "legacy Supabase mutations require an opaque governed write permit",
        )
    return permit.replication_receipt_id


class SupabaseReplicationCapability:
    """Opaque, one-use capability for one exact replica write."""

    def __init__(
        self,
        *,
        issuer: object,
        tenant_id: str,
        scope: Mapping[str, str],
        roster_hash: str,
        replication_receipt_id: str,
        finality_receipt_ids: Iterable[str],
    ):
        if issuer is not _ISSUER:
            raise SupabaseAuthorityDenied(
                "SUPABASE_REPLICATION_AUTHORITY_INVALID",
                "replication capabilities require a verified Zaubern receipt",
            )
        self.tenant_id = tenant_id
        self.scope = dict(scope)
        self.roster_hash = roster_hash
        self.replication_receipt_id = replication_receipt_id
        self.finality_receipt_ids = tuple(finality_receipt_ids)
        self._active = True

    def consume(
        self,
        *,
        operation: str,
        tournament_key: str,
        tournament_slug: Optional[str],
        tournament_name: Optional[str],
        category_id: Optional[str],
        category_name: Optional[str],
        target_team_id: Optional[str],
        source_team_id: str,
        team_name: str,
        players: Iterable[Mapping[str, Any]],
    ) -> SupabaseWritePermit:
        if not self._active:
            raise SupabaseAuthorityDenied(
                "SUPABASE_REPLICATION_AUTHORITY_CONSUMED",
                "replication authority is one-use and has already been consumed",
            )
        actual_scope = _scope(
            operation=operation,
            tournament_key=tournament_key,
            tournament_slug=tournament_slug,
            tournament_name=tournament_name,
            category_id=category_id,
            category_name=category_name,
            target_team_id=target_team_id,
            source_team_id=source_team_id,
            team_name=team_name,
        )
        actual_roster_hash = replica_roster_hash(players)
        if actual_scope != self.scope or actual_roster_hash != self.roster_hash:
            raise SupabaseAuthorityDenied(
                "SUPABASE_REPLICATION_SCOPE_MISMATCH",
                "Supabase payload differs from the receipt-bound replica event",
            )
        self._active = False
        return SupabaseWritePermit(
            issuer=_ISSUER,
            replication_receipt_id=self.replication_receipt_id,
        )


def issue_supabase_replication_capability(
    governance_result: Mapping[str, Any],
    *,
    operation: str,
    tournament_key: str,
    tournament_slug: Optional[str],
    tournament_name: Optional[str],
    category_id: Optional[str],
    category_name: Optional[str],
    target_team_id: Optional[str],
    source_team_id: str,
    team_name: str,
    players: Iterable[Mapping[str, Any]],
) -> SupabaseReplicationCapability:
    """Issue authority only from an exact post-finality replication receipt."""
    event = governance_result.get("replication_event") or {}
    receipt = governance_result.get("replication_receipt") or {}
    expected_scope = _scope(
        operation=operation,
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
        category_id=category_id,
        category_name=category_name,
        target_team_id=target_team_id,
        source_team_id=source_team_id,
        team_name=team_name,
    )
    expected_hash = replica_roster_hash(players)
    finality_ids = event.get("finality_receipt_ids") or []
    valid_finality = (
        isinstance(finality_ids, list)
        and len(finality_ids) == len(normalize_replica_roster(players))
        and len(set(finality_ids)) == len(finality_ids)
        and all(_DIGEST.fullmatch(_text(value)) for value in finality_ids)
    )
    valid = (
        governance_result.get("authorized") is True
        and event.get("event_type") == "samchat_registration_supabase_replication_v1"
        and event.get("decision") == "AUTHORIZE_REPLICA_WRITE"
        and event.get("scope") == expected_scope
        and event.get("roster_identity_hash") == expected_hash
        and _HMAC_DIGEST.fullmatch(_text(event.get("roster_draft_binding")))
        and valid_finality
        and receipt.get("receipt_type") == "EvidenceReceipt.v1"
        and receipt.get("verified") is True
        and receipt.get("event_hash") == _event_hash(event)
        and receipt.get("event_type") == event.get("event_type")
        and _text(receipt.get("tenant_id")) == _text(event.get("tenant_id"))
        and bool(_text(event.get("tenant_id")))
        and _DIGEST.fullmatch(_text(receipt.get("receipt_id")))
        and expected_scope["operation"] in {"register_team", "append_players"}
        and bool(expected_scope["tournament_key"])
        and bool(expected_scope["source_team_id"])
        and bool(expected_scope["team_name"] or expected_scope["target_team_id"])
        and bool(expected_scope["category_name"] or expected_scope["category_id"])
    )
    if not valid:
        raise SupabaseAuthorityDenied(
            "SUPABASE_REPLICATION_AUTHORITY_INVALID",
            "gate response cannot authorize a Supabase replica write",
        )
    return SupabaseReplicationCapability(
        issuer=_ISSUER,
        tenant_id=_text(event.get("tenant_id")),
        scope=expected_scope,
        roster_hash=expected_hash,
        replication_receipt_id=_text(receipt.get("receipt_id")),
        finality_receipt_ids=finality_ids,
    )


__all__ = [
    "SupabaseAuthorityDenied",
    "SupabaseReplicationCapability",
    "SupabaseWritePermit",
    "issue_supabase_replication_capability",
    "normalize_replica_roster",
    "replica_roster_hash",
    "require_supabase_write_permit",
]
