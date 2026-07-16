"""REG-S07 governed successors for committed Team and Player projections."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional
from uuid import NAMESPACE_URL, UUID, uuid5

from .models import (
    Player,
    RegistrationPostcommitMutationDecision,
    RegistrationPostcommitMutationExecution,
    RegistrationPostcommitMutationProposal,
    Team,
)
from .persistence_authority import PersistenceAuthorityDenied, semantic_event_hash


TEAM_EDIT_FIELDS = frozenset(
    {
        "name",
        "category",
        "gender",
        "league",
        "league_phone",
        "league_address",
        "representative_name",
        "contact_email",
        "contact_phone",
        "state",
        "municipality",
    }
)
PLAYER_EDIT_FIELDS = frozenset(
    {"first_name", "last_name", "birth_date", "curp", "email"}
)
PLAYER_VERIFY_FIELDS = frozenset(
    {"verified_by_human", "needs_review", "verification_notes"}
)
POSTCOMMIT_AUTHORITY_FIELDS = frozenset(
    {"postcommit_revision", "postcommit_snapshot_hash", "updated_at"}
)

_ISSUER = object()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_binding(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _binding_key() -> bytes:
    value = (
        os.getenv("SAMCHAT_POSTCOMMIT_BINDING_KEY")
        or os.getenv("SAMCHAT_HUMAN_FIELD_BINDING_KEY")
        or ""
    ).encode("utf-8")
    if len(value) < 32:
        raise ValueError("SAMCHAT_POSTCOMMIT_BINDING_KEY is missing or too short")
    return value


def hmac_binding(value: Any) -> str:
    return "hmac-sha256:" + hmac.new(
        _binding_key(), canonical_bytes(value), hashlib.sha256
    ).hexdigest()


def _iso(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def team_snapshot(team: Team) -> dict[str, Any]:
    return {
        "entity_type": "TEAM",
        "id": str(team.id),
        "name": team.name,
        "tournament_slug": team.tournament_slug,
        "gender": team.gender,
        "category": team.category,
        "league": team.league,
        "league_phone": team.league_phone,
        "league_address": team.league_address,
        "representative_name": team.representative_name,
        "contact_email": team.contact_email,
        "contact_phone": team.contact_phone,
        "state": team.state,
        "municipality": team.municipality,
        "roster_image_path": team.roster_image_path,
        "telegram_chat_id": team.telegram_chat_id,
        "telegram_user_id": team.telegram_user_id,
    }


def player_snapshot(player: Player) -> dict[str, Any]:
    photo_data = str(player.photo_data or "")
    return {
        "entity_type": "PLAYER",
        "id": str(player.id),
        "team_id": str(player.team_id),
        "first_name": player.first_name,
        "last_name": player.last_name,
        "birth_date": _iso(player.birth_date),
        "curp": player.curp,
        "email": player.email,
        "photo_path": player.photo_path,
        "photo_data_hash": (
            "sha256:" + hashlib.sha256(photo_data.encode("utf-8")).hexdigest()
        ),
        "photo_sha256": player.photo_sha256,
        "photo_ahash": player.photo_ahash,
        "curp_valid": bool(player.curp_valid),
        "curp_validation_date": _iso(player.curp_validation_date),
        "curp_validation_errors": player.curp_validation_errors,
        "ocr_confidence": player.ocr_confidence,
        "needs_review": bool(player.needs_review),
        "verified_by_human": bool(player.verified_by_human),
        "verification_notes": player.verification_notes,
        "roster_index": player.roster_index,
        "governance_state": player.governance_state,
        "governance_draft_id": player.governance_draft_id,
        "governance_draft_version": player.governance_draft_version,
        "governance_decision_id": player.governance_decision_id,
        "roster_draft_binding": player.roster_draft_binding,
        "preauthorization_receipt_id": player.preauthorization_receipt_id,
        "finality_receipt_id": player.finality_receipt_id,
    }


def entity_snapshot(entity: Team | Player) -> dict[str, Any]:
    if isinstance(entity, Team):
        return team_snapshot(entity)
    if isinstance(entity, Player):
        return player_snapshot(entity)
    raise TypeError("REG-S07 supports Team and Player only")


def changed_fields(
    base_snapshot: Mapping[str, Any],
    proposed_snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    ignored = {"entity_type", "id", "team_id"}
    return [
        {
            "field_path": key,
            "previous_value": base_snapshot.get(key),
            "proposed_value": proposed_snapshot.get(key),
        }
        for key in sorted(set(base_snapshot) | set(proposed_snapshot))
        if key not in ignored
        and base_snapshot.get(key) != proposed_snapshot.get(key)
    ]


def proposal_id_for(
    mutation_request_id: UUID, entity_type: str, entity_id: UUID
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"samchat-regs07:{entity_type}:{entity_id}:{mutation_request_id}",
    )


def execution_id_for(proposal_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"samchat-regs07-execution:{proposal_id}")


def source_evidence_binding(entity: Team | Player) -> str:
    if isinstance(entity, Player):
        payload = {
            "entity_type": "PLAYER",
            "entity_id": str(entity.id),
            "team_id": str(entity.team_id),
            "governance_draft_id": entity.governance_draft_id,
            "governance_draft_version": entity.governance_draft_version,
            "governance_decision_id": entity.governance_decision_id,
            "roster_draft_binding": entity.roster_draft_binding,
            "preauthorization_receipt_id": entity.preauthorization_receipt_id,
            "finality_receipt_id": entity.finality_receipt_id,
        }
    else:
        payload = {
            "entity_type": "TEAM",
            "entity_id": str(entity.id),
            "tournament_slug": entity.tournament_slug,
            "roster_image_path": entity.roster_image_path,
            "telegram_chat_id": entity.telegram_chat_id,
            "telegram_user_id": entity.telegram_user_id,
        }
    return hmac_binding(payload)


def build_gate_request(
    proposal: RegistrationPostcommitMutationProposal,
) -> dict[str, Any]:
    payload = {
        "tenant_id": "samchat-prod",
        "proposal_id": str(proposal.id),
        "mutation_request_id": str(proposal.mutation_request_id),
        "entity_type": proposal.entity_type,
        "entity_id": str(proposal.entity_id),
        "team_id": str(proposal.team_id),
        "mutation_type": proposal.mutation_type,
        "base_revision": proposal.base_revision,
        "proposed_revision": proposal.proposed_revision,
        "base_snapshot_hash": proposal.base_snapshot_hash,
        "expected_current_revision": proposal.base_revision,
        "expected_current_snapshot_hash": proposal.base_snapshot_hash,
        "proposed_snapshot_hash": proposal.proposed_snapshot_hash,
        "field_changes": list(proposal.field_changes or []),
        "field_change_set_hash": proposal.field_change_set_hash,
        "mutation_reason": proposal.mutation_reason,
        "mutation_reason_binding": proposal.mutation_reason_binding,
        "source_evidence_binding": proposal.source_evidence_binding,
        "actor": {
            "principal_id": proposal.proposer_principal_id,
            "role": proposal.proposer_role,
            "role_assignment_id": proposal.role_assignment_id,
            "authorization_epoch": proposal.authorization_epoch,
            "authentication_method": proposal.authentication_method,
            "authentication_assurance_level": (
                proposal.authentication_assurance_level
            ),
            "auth_context_id": proposal.auth_context_id,
        },
    }
    if proposal.mutation_type == "VERIFY_PLAYER":
        payload["verification_projection"] = {
            "verified_by_human": bool(
                proposal.proposed_snapshot.get("verified_by_human")
            ),
            "needs_review": bool(
                proposal.proposed_snapshot.get("needs_review")
            ),
        }
    return payload


def decision_row(
    proposal_id: UUID,
    response: Mapping[str, Any],
) -> RegistrationPostcommitMutationDecision:
    event = dict(response.get("postcommit_mutation_decision") or {})
    receipt = dict(response.get("postcommit_mutation_receipt") or {})
    return RegistrationPostcommitMutationDecision(
        proposal_id=proposal_id,
        decision_id=str(event["decision_id"]),
        policy_hash=str(event["policy_hash"]),
        decision=str(event["decision"]),
        reason_codes=list(event.get("reason_codes") or []),
        receipt_id=str(receipt["receipt_id"]),
        receipt_alg=str(receipt["alg"]),
        event_hash=str(receipt["event_hash"]),
        decision_document=event,
        receipt_document=receipt,
        issued_at=_parse_time(event["issued_at"]),
        expires_at=_parse_time(event["expires_at"]),
    )


def execution_row(
    *,
    execution_id: UUID,
    proposal_id: UUID,
    decision_id: UUID,
    database_transaction_id: str,
    response: Mapping[str, Any],
) -> RegistrationPostcommitMutationExecution:
    event = dict(response.get("postcommit_mutation_attestation") or {})
    receipt = dict(response.get("postcommit_finality_receipt") or {})
    return RegistrationPostcommitMutationExecution(
        id=execution_id,
        proposal_id=proposal_id,
        decision_id=decision_id,
        database_transaction_id=database_transaction_id,
        attestation_id=str(event["attestation_id"]),
        attestation_hash=str(event["attestation_hash"]),
        finality_receipt_id=str(receipt["receipt_id"]),
        finality_receipt_alg=str(receipt["alg"]),
        finality_event_document=event,
        finality_receipt_document=receipt,
    )


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def build_finality_request(
    *,
    proposal: RegistrationPostcommitMutationProposal,
    decision: RegistrationPostcommitMutationDecision,
    actual_projection_hash: str,
    database_transaction_id: str,
    cas_succeeded: bool,
) -> dict[str, Any]:
    return {
        "tenant_id": "samchat-prod",
        "proposal_id": str(proposal.id),
        "entity_type": proposal.entity_type,
        "entity_id": str(proposal.entity_id),
        "team_id": str(proposal.team_id),
        "mutation_type": proposal.mutation_type,
        "base_revision": proposal.base_revision,
        "proposed_revision": proposal.proposed_revision,
        "base_snapshot_hash": proposal.base_snapshot_hash,
        "proposed_snapshot_hash": proposal.proposed_snapshot_hash,
        "actual_projection_hash": actual_projection_hash,
        "field_change_set_hash": proposal.field_change_set_hash,
        "database_transaction_id": database_transaction_id,
        "cas_succeeded": bool(cas_succeeded),
        "preauthorization_decision": dict(decision.decision_document or {}),
        "preauthorization_receipt": dict(decision.receipt_document or {}),
    }


def _receipt_binds_event(
    receipt: Mapping[str, Any], event: Mapping[str, Any]
) -> bool:
    return (
        receipt.get("receipt_type") == "EvidenceReceipt.v1"
        and receipt.get("verified") is True
        and receipt.get("alg") == "Ed25519"
        and receipt.get("event_hash") == semantic_event_hash(event)
        and receipt.get("event_type") == event.get("event_type")
        and receipt.get("tenant_id") == event.get("tenant_id")
        and bool(receipt.get("receipt_id"))
    )


class PostcommitMutationCapability:
    """One-transaction authority for one exact committed-state successor."""

    def __init__(
        self,
        *,
        issuer: object,
        entity_type: str,
        entity_id: str,
        team_id: str,
        changed_field_names: Iterable[str],
        base_revision: int,
        proposed_revision: int,
        base_snapshot_hash: str,
        proposed_snapshot_hash: str,
        execution_id: str,
        finality_receipt_id: str,
    ):
        if issuer is not _ISSUER:
            raise PersistenceAuthorityDenied(
                "POSTCOMMIT_AUTHORITY_INVALID",
                "post-commit capabilities require exact double-receipt authority",
            )
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.team_id = team_id
        self.changed_field_names = frozenset(changed_field_names)
        self.base_revision = int(base_revision)
        self.proposed_revision = int(proposed_revision)
        self.base_snapshot_hash = base_snapshot_hash
        self.proposed_snapshot_hash = proposed_snapshot_hash
        self.execution_id = execution_id
        self.finality_receipt_id = finality_receipt_id
        self._binding_token: Optional[object] = None
        self._active = True

    def bind(self, token: object) -> None:
        self._ensure_active()
        if self._binding_token is not None and self._binding_token is not token:
            raise PersistenceAuthorityDenied(
                "POSTCOMMIT_AUTHORITY_ALREADY_BOUND",
                "post-commit authority cannot cross transactions",
            )
        self._binding_token = token

    def invalidate(self) -> None:
        self._active = False

    def _ensure_active(self, token: Optional[object] = None) -> None:
        if not self._active:
            raise PersistenceAuthorityDenied(
                "POSTCOMMIT_AUTHORITY_ALREADY_CONSUMED",
                "post-commit authority is no longer active",
            )
        if token is not None and self._binding_token is not token:
            raise PersistenceAuthorityDenied(
                "POSTCOMMIT_AUTHORITY_SCOPE_MISMATCH",
                "post-commit authority is not bound to this transaction",
            )

    def authorize_team_create(self, _team: Any, *, token: object) -> None:
        self._ensure_active(token)
        raise PersistenceAuthorityDenied(
            "POSTCOMMIT_AUTHORITY_SCOPE_MISMATCH",
            "post-commit authority never authorizes creation",
        )

    authorize_player_create = authorize_team_create

    def _authorize_update(
        self, entity: Any, changed: Iterable[str], expected_type: str, token: object
    ) -> None:
        self._ensure_active(token)
        changed_set = set(changed) - POSTCOMMIT_AUTHORITY_FIELDS
        valid = (
            self.entity_type == expected_type
            and str(getattr(entity, "id", "") or "") == self.entity_id
            and changed_set == self.changed_field_names
            and int(getattr(entity, "postcommit_revision", 0) or 0)
            == self.proposed_revision
            and str(getattr(entity, "postcommit_snapshot_hash", "") or "")
            == self.proposed_snapshot_hash
        )
        if expected_type == "PLAYER":
            valid = valid and str(getattr(entity, "team_id", "") or "") == self.team_id
        if not valid:
            raise PersistenceAuthorityDenied(
                "POSTCOMMIT_AUTHORITY_SCOPE_MISMATCH",
                "mutation does not match the exact receipt-bound successor",
            )

    def authorize_team_update(
        self, team: Any, changed_fields: Iterable[str], *, token: object
    ) -> None:
        self._authorize_update(team, changed_fields, "TEAM", token)

    def authorize_player_update(
        self, player: Any, changed_fields: Iterable[str], *, token: object
    ) -> None:
        self._authorize_update(player, changed_fields, "PLAYER", token)

    def deny_delete(self, _entity: Any, *, token: object) -> None:
        self._ensure_active(token)
        raise PersistenceAuthorityDenied(
            "POSTCOMMIT_DELETE_DENIED",
            "REG-S07 never authorizes physical deletion",
        )


def issue_postcommit_persistence_capability(
    *,
    proposal: RegistrationPostcommitMutationProposal,
    decision: RegistrationPostcommitMutationDecision,
    finality_response: Mapping[str, Any],
    execution_id: UUID,
) -> PostcommitMutationCapability:
    decision_event = dict(decision.decision_document or {})
    decision_receipt = dict(decision.receipt_document or {})
    attestation = dict(
        finality_response.get("postcommit_mutation_attestation") or {}
    )
    finality_receipt = dict(
        finality_response.get("postcommit_finality_receipt") or {}
    )
    exact = (
        decision.decision == "AUTHORIZE_POSTCOMMIT_MUTATION"
        and decision_event.get("proposal_id") == str(proposal.id)
        and decision_event.get("base_snapshot_hash")
        == proposal.base_snapshot_hash
        and decision_event.get("proposed_snapshot_hash")
        == proposal.proposed_snapshot_hash
        and _receipt_binds_event(decision_receipt, decision_event)
        and attestation.get("decision") == "ATTEST_POSTCOMMIT_MUTATION"
        and attestation.get("proposal_id") == str(proposal.id)
        and attestation.get("actual_projection_hash")
        == proposal.proposed_snapshot_hash
        and _receipt_binds_event(finality_receipt, attestation)
    )
    if not exact:
        raise PersistenceAuthorityDenied(
            "POSTCOMMIT_AUTHORITY_INVALID",
            "gate responses do not bind the exact committed-state successor",
        )
    return PostcommitMutationCapability(
        issuer=_ISSUER,
        entity_type=proposal.entity_type,
        entity_id=str(proposal.entity_id),
        team_id=str(proposal.team_id),
        changed_field_names=[
            str(item["field_path"]) for item in proposal.field_changes or []
        ],
        base_revision=proposal.base_revision,
        proposed_revision=proposal.proposed_revision,
        base_snapshot_hash=proposal.base_snapshot_hash,
        proposed_snapshot_hash=proposal.proposed_snapshot_hash,
        execution_id=str(execution_id),
        finality_receipt_id=str(finality_receipt["receipt_id"]),
    )
