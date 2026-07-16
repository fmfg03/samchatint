"""Transaction-scoped authority capabilities for Copa Telmex persistence."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Mapping, Optional, Set

_ISSUER = object()
_TEAM_UPDATE_FIELDS = frozenset(
    {
        "tournament_slug",
        "gender",
        "category",
        "league",
        "representative_name",
        "state",
        "municipality",
        "roster_image_path",
        "contact_phone",
        "contact_email",
        "updated_at",
    }
)
_PLAYER_FINALITY_FIELDS = frozenset(
    {"governance_state", "finality_receipt_id", "updated_at"}
)


class PersistenceAuthorityDenied(RuntimeError):
    """A Team or Player mutation lacks exact, live governance authority."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def semantic_event_hash(event: Mapping[str, Any]) -> str:
    """Bind the exact semantic event returned by the Zaubern gate."""
    return "sha256:" + hashlib.sha256(_canonical(event)).hexdigest()


def _required_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PersistenceAuthorityDenied(
            "PERSISTENCE_AUTHORITY_INVALID", f"missing {field}"
        )
    return text


def _receipt_binds_event(
    receipt: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    tenant_id: str,
) -> bool:
    return (
        receipt.get("receipt_type") == "EvidenceReceipt.v1"
        and receipt.get("verified") is True
        and receipt.get("event_hash") == semantic_event_hash(event)
        and receipt.get("event_type") == event.get("event_type")
        and receipt.get("tenant_id") == tenant_id
        and bool(receipt.get("receipt_id"))
    )


class RegistrationPersistenceCapability:
    """Opaque one-transaction capability issued from a verified gate response."""

    def __init__(
        self,
        *,
        issuer: object,
        tenant_id: str,
        draft_id: str,
        draft_version: int,
        team_id: str,
        decision_id: str,
        roster_draft_binding: str,
        preauthorization_receipt_id: str,
        player_slots: Iterable[int],
    ):
        if issuer is not _ISSUER:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_INVALID",
                "capabilities must be issued from a verified preauthorization",
            )
        self.tenant_id = tenant_id
        self.draft_id = draft_id
        self.draft_version = int(draft_version)
        self.team_id = team_id
        self.decision_id = decision_id
        self.roster_draft_binding = roster_draft_binding
        self.preauthorization_receipt_id = preauthorization_receipt_id
        self.player_slots = frozenset(int(slot) for slot in player_slots)
        self._binding_token: Optional[object] = None
        self._active = True
        self._finality_receipts: Dict[str, str] = {}

    def bind(self, token: object) -> None:
        self._ensure_active()
        if self._binding_token is not None and self._binding_token is not token:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_ALREADY_BOUND",
                "capability cannot be reused in another transaction",
            )
        self._binding_token = token

    def invalidate(self) -> None:
        self._active = False
        self._finality_receipts.clear()

    def _ensure_active(self, token: Optional[object] = None) -> None:
        if not self._active:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_ALREADY_CONSUMED",
                "capability is no longer active",
            )
        if token is not None and self._binding_token is not token:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
                "capability is not bound to this transaction",
            )

    def authorize_team_create(self, team: Any, *, token: object) -> None:
        self._ensure_active(token)
        if str(getattr(team, "id", "") or "") != self.team_id:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
                "team create is outside the preauthorized team",
            )

    def authorize_team_update(
        self, team: Any, changed_fields: Iterable[str], *, token: object
    ) -> None:
        self._ensure_active(token)
        fields = set(changed_fields)
        if str(getattr(team, "id", "") or "") != self.team_id:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
                "team update is outside the preauthorized team",
            )
        unsupported = fields - _TEAM_UPDATE_FIELDS
        if unsupported:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
                "team fields are outside registration authority: "
                + ",".join(sorted(unsupported)),
            )

    def authorize_player_create(self, player: Any, *, token: object) -> None:
        self._ensure_active(token)
        slot = int(getattr(player, "roster_index", 0) or 0)
        expected = {
            "team_id": self.team_id,
            "draft_id": self.draft_id,
            "draft_version": self.draft_version,
            "decision_id": self.decision_id,
            "roster_draft_binding": self.roster_draft_binding,
            "preauthorization_receipt_id": self.preauthorization_receipt_id,
            "governance_state": "PENDING_FINALITY",
        }
        actual = {
            "team_id": str(getattr(player, "team_id", "") or ""),
            "draft_id": str(getattr(player, "governance_draft_id", "") or ""),
            "draft_version": int(getattr(player, "governance_draft_version", 0) or 0),
            "decision_id": str(getattr(player, "governance_decision_id", "") or ""),
            "roster_draft_binding": str(
                getattr(player, "roster_draft_binding", "") or ""
            ),
            "preauthorization_receipt_id": str(
                getattr(player, "preauthorization_receipt_id", "") or ""
            ),
            "governance_state": str(getattr(player, "governance_state", "") or ""),
        }
        if slot not in self.player_slots or actual != expected:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
                "player create is not bound to the preauthorized roster slot",
            )

    def record_player_finality(
        self, player: Any, finality_result: Mapping[str, Any], *, token: object
    ) -> str:
        self._ensure_active(token)
        attestation = finality_result.get("attestation") or {}
        receipt = finality_result.get("finality_receipt") or {}
        player_id = str(getattr(player, "id", "") or "")
        slot = int(getattr(player, "roster_index", 0) or 0)
        valid = (
            finality_result.get("activate") is True
            and attestation.get("decision") == "ACTIVATE_PLAYER"
            and str(attestation.get("tenant_id") or "") == self.tenant_id
            and str(attestation.get("draft_id") or "") == self.draft_id
            and int(attestation.get("draft_version") or 0) == self.draft_version
            and str(attestation.get("player_id") or "") == player_id
            and int(attestation.get("player_slot") or 0) == slot
            and str(attestation.get("roster_draft_binding") or "")
            == self.roster_draft_binding
            and str(attestation.get("preauthorization_receipt_id") or "")
            == self.preauthorization_receipt_id
            and _receipt_binds_event(receipt, attestation, tenant_id=self.tenant_id)
        )
        if not valid:
            raise PersistenceAuthorityDenied(
                "FINALITY_RECEIPT_REQUIRED",
                "player activation is not bound to a valid finality receipt",
            )
        receipt_id = str(receipt["receipt_id"])
        self._finality_receipts[player_id] = receipt_id
        return receipt_id

    def authorize_player_update(
        self, player: Any, changed_fields: Iterable[str], *, token: object
    ) -> None:
        self._ensure_active(token)
        fields: Set[str] = set(changed_fields)
        player_id = str(getattr(player, "id", "") or "")
        receipt_id = str(getattr(player, "finality_receipt_id", "") or "")
        valid = (
            fields
            and not (fields - _PLAYER_FINALITY_FIELDS)
            and str(getattr(player, "team_id", "") or "") == self.team_id
            and str(getattr(player, "governance_state", "") or "") == "ACTIVE"
            and self._finality_receipts.get(player_id) == receipt_id
        )
        if not valid:
            raise PersistenceAuthorityDenied(
                "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
                "only the receipt-bound PENDING_FINALITY to ACTIVE transition is authorized",
            )

    def deny_delete(self, _entity: Any, *, token: object) -> None:
        self._ensure_active(token)
        raise PersistenceAuthorityDenied(
            "PERSISTENCE_AUTHORITY_SCOPE_MISMATCH",
            "registration materialization authority never authorizes deletion",
        )


def issue_registration_persistence_capability(
    governance_result: Mapping[str, Any],
    *,
    tenant_id: str,
    draft_id: str,
    draft_version: int,
    team_id: str,
) -> RegistrationPersistenceCapability:
    """Issue an opaque capability only from an exact preauthorization response."""
    roster = governance_result.get("roster_decision") or {}
    receipt = governance_result.get("preauthorization_receipt") or {}
    valid = (
        governance_result.get("authorized") is True
        and roster.get("decision") == "AUTHORIZE_PENDING_MATERIALIZATION"
        and str(roster.get("tenant_id") or "") == tenant_id
        and str(roster.get("draft_id") or "") == draft_id
        and int(roster.get("draft_version") or 0) == int(draft_version)
        and str(roster.get("team_id") or "") == team_id
        and str(roster.get("roster_draft_binding") or "").startswith("hmac-sha256:")
        and isinstance(roster.get("ordered_player_slots"), list)
        and _receipt_binds_event(receipt, roster, tenant_id=tenant_id)
    )
    if not valid:
        raise PersistenceAuthorityDenied(
            "PERSISTENCE_AUTHORITY_INVALID",
            "gate response cannot issue persistence authority",
        )
    return RegistrationPersistenceCapability(
        issuer=_ISSUER,
        tenant_id=tenant_id,
        draft_id=draft_id,
        draft_version=draft_version,
        team_id=team_id,
        decision_id=_required_string(roster.get("decision_id"), "decision_id"),
        roster_draft_binding=_required_string(
            roster.get("roster_draft_binding"), "roster_draft_binding"
        ),
        preauthorization_receipt_id=_required_string(
            receipt.get("receipt_id"), "preauthorization_receipt_id"
        ),
        player_slots=roster["ordered_player_slots"],
    )
