"""Pure approval and application contract for a frozen tournament proposal.

The contract records authority and exact intended effects.  It performs no
persistence, routing, transaction management, or domain mutation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple, Union
from uuid import UUID

from .tournament_draft_workbench import (
    FrozenTournamentProposal,
    TournamentProposalTamperedError,
    verify_frozen_proposal,
)
from .tournament_goal_shadow import canonical_json, canonical_sha256


CONTRACT_VERSION = "tournament_application_contract_v1"
APPROVAL_RECEIPT_VERSION = "tournament_approval_receipt_v1"
APPLICATION_RECEIPT_VERSION = "tournament_application_receipt_v1"
FROZEN = "frozen"
APPROVED = "approved"
APPLIED = "applied"
STATES = frozenset({FROZEN, APPROVED, APPLIED})
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CASE_ID_PATTERN = re.compile(r"^analyst_case_[0-9a-f]{32}$")
NO_CLAIMS: Tuple[str, ...] = (
    "communications_created",
    "matches_or_schedule_created",
    "media_created",
    "operations_link_created",
    "players_created",
    "rich_tournament_config_created",
    "rich_tournament_dates_created",
    "teams_created",
)
WRITE_FIELDS: Tuple[str, ...] = (
    "id",
    "name",
    "description",
    "active",
    "display_order",
    "cuenta_contable_relacionada",
    "etapas",
    "categorias",
    "form_visibility_areas",
)
PROJECTION_FIELDS: Tuple[str, ...] = (
    "name",
    "description",
    "active",
    "display_order",
    "accounting_account",
    "stages",
    "categories",
    "visibility_areas",
)


class TournamentApplicationContractError(ValueError):
    """Raised when a lifecycle transition or binding is invalid."""


class TournamentApplicationTamperedError(TournamentApplicationContractError):
    """Raised when a serialized receipt or lifecycle no longer verifies."""


def _hash_payload(payload: Mapping[str, Any]) -> str:
    return f"sha256:{canonical_sha256(payload)}"


def _replay_key(
    *, operation: str, case_id: str, proposal_hash: str, actor_id: Optional[str] = None
) -> str:
    payload = {
        "operation": operation,
        "case_id": case_id,
        "proposal_hash": proposal_hash,
    }
    if actor_id is not None:
        payload["actor_id"] = actor_id
    return _hash_payload(payload)


def _clean_timestamp(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TournamentApplicationContractError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise TournamentApplicationContractError(
            f"{field_name} must include a timezone"
        )
    return parsed.astimezone(timezone.utc).isoformat()


def _positive_version(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TournamentApplicationContractError(
            f"{field_name} must be a positive integer"
        )
    return value


def _verified_actor(value: Mapping[str, Any], *, field_name: str) -> Dict[str, Any]:
    actor = json.loads(canonical_json(value))
    if not isinstance(actor, dict):
        raise TournamentApplicationContractError(f"{field_name} must be an object")
    if not str(actor.get("id") or "").strip() or actor.get("activo") is not True:
        raise TournamentApplicationContractError(
            f"{field_name} must bind an active employee"
        )
    return actor


def _proposal(
    value: Union[FrozenTournamentProposal, Mapping[str, Any]],
) -> FrozenTournamentProposal:
    if isinstance(value, FrozenTournamentProposal):
        verify_frozen_proposal(value)
        return value
    try:
        return FrozenTournamentProposal.from_mapping(value)
    except (TournamentProposalTamperedError, TypeError) as exc:
        raise TournamentApplicationTamperedError(str(exc)) from exc


@dataclass(frozen=True)
class ApprovalReceipt:
    payload_json: str
    receipt_hash: str

    @property
    def payload(self) -> Dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise TournamentApplicationTamperedError(
                "Approval receipt payload must be an object"
            )
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {"payload": self.payload, "receipt_hash": self.receipt_hash}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApprovalReceipt":
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise TournamentApplicationTamperedError(
                "Approval receipt payload must be an object"
            )
        receipt = cls(
            payload_json=canonical_json(payload),
            receipt_hash=str(value.get("receipt_hash") or ""),
        )
        verify_approval_receipt(receipt)
        return receipt


@dataclass(frozen=True)
class ApplicationReceipt:
    payload_json: str
    receipt_hash: str

    @property
    def payload(self) -> Dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise TournamentApplicationTamperedError(
                "Application receipt payload must be an object"
            )
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {"payload": self.payload, "receipt_hash": self.receipt_hash}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationReceipt":
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise TournamentApplicationTamperedError(
                "Application receipt payload must be an object"
            )
        receipt = cls(
            payload_json=canonical_json(payload),
            receipt_hash=str(value.get("receipt_hash") or ""),
        )
        verify_application_receipt(receipt)
        return receipt


@dataclass(frozen=True)
class TournamentApplicationContract:
    frozen_proposal: FrozenTournamentProposal
    state: str = FROZEN
    approval_receipt: Optional[ApprovalReceipt] = None
    application_receipt: Optional[ApplicationReceipt] = None
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or self.state not in STATES:
            raise TournamentApplicationContractError(
                "Invalid tournament application contract state"
            )
        verify_frozen_proposal(self.frozen_proposal)
        if self.state == FROZEN and (
            self.approval_receipt is not None or self.application_receipt is not None
        ):
            raise TournamentApplicationContractError(
                "Frozen state cannot contain approval or application receipts"
            )
        if self.state == APPROVED and (
            self.approval_receipt is None or self.application_receipt is not None
        ):
            raise TournamentApplicationContractError(
                "Approved state requires only an approval receipt"
            )
        if self.state == APPLIED and (
            self.approval_receipt is None or self.application_receipt is None
        ):
            raise TournamentApplicationContractError(
                "Applied state requires approval and application receipts"
            )
        if self.state != FROZEN:
            verify_tournament_application_contract(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "state": self.state,
            "frozen_proposal": self.frozen_proposal.to_dict(),
            "approval_receipt": (
                self.approval_receipt.to_dict() if self.approval_receipt else None
            ),
            "application_receipt": (
                self.application_receipt.to_dict() if self.application_receipt else None
            ),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TournamentApplicationContract":
        proposal_value = value.get("frozen_proposal")
        if not isinstance(proposal_value, Mapping):
            raise TournamentApplicationTamperedError("Frozen proposal is required")
        approval_value = value.get("approval_receipt")
        application_value = value.get("application_receipt")
        contract = cls(
            contract_version=str(value.get("contract_version") or ""),
            state=str(value.get("state") or ""),
            frozen_proposal=_proposal(proposal_value),
            approval_receipt=(
                ApprovalReceipt.from_mapping(approval_value)
                if isinstance(approval_value, Mapping)
                else None
            ),
            application_receipt=(
                ApplicationReceipt.from_mapping(application_value)
                if isinstance(application_value, Mapping)
                else None
            ),
        )
        verify_tournament_application_contract(contract)
        return contract


def start_tournament_application(
    proposal: Union[FrozenTournamentProposal, Mapping[str, Any]],
) -> TournamentApplicationContract:
    """Create the inert lifecycle rooted at one verified frozen proposal."""

    return TournamentApplicationContract(frozen_proposal=_proposal(proposal))


def _expected_bindings(contract: TournamentApplicationContract) -> Dict[str, str]:
    proposal_payload = verify_frozen_proposal(contract.frozen_proposal)
    return {
        "case_id": str(proposal_payload.get("case_id") or ""),
        "proposal_hash": contract.frozen_proposal.proposal_hash,
        "draft_hash": str(proposal_payload.get("draft_hash") or ""),
        "source_hash": str(proposal_payload.get("source_authority_hash") or ""),
        "owner_employee_id": str(proposal_payload.get("owner_employee_id") or ""),
    }


def _assert_expected(
    contract: TournamentApplicationContract,
    *,
    expected_proposal_hash: str,
    expected_draft_hash: str,
    verified_source_hash: str,
) -> Dict[str, str]:
    bindings = _expected_bindings(contract)
    if (
        str(expected_proposal_hash or "").strip().casefold()
        != bindings["proposal_hash"]
        or str(expected_draft_hash or "").strip().casefold() != bindings["draft_hash"]
        or str(verified_source_hash or "").strip().casefold() != bindings["source_hash"]
    ):
        raise TournamentApplicationContractError(
            "Expected proposal, draft, or source binding is stale"
        )
    return bindings


def approve_tournament_application(
    contract: TournamentApplicationContract,
    *,
    approved_by: Mapping[str, Any],
    approved_case_version: int,
    approved_at: str,
    expected_proposal_hash: str,
    expected_draft_hash: str,
    verified_source_hash: str,
    note: Optional[str] = None,
) -> TournamentApplicationContract:
    """Bind one independent active approver to the exact frozen proposal."""

    if contract.state != FROZEN:
        raise TournamentApplicationContractError(
            "Only a frozen proposal can be approved"
        )
    bindings = _assert_expected(
        contract,
        expected_proposal_hash=expected_proposal_hash,
        expected_draft_hash=expected_draft_hash,
        verified_source_hash=verified_source_hash,
    )
    actor = _verified_actor(approved_by, field_name="approved_by")
    if str(actor["id"]) == bindings["owner_employee_id"]:
        raise TournamentApplicationContractError(
            "Proposal owner cannot approve the proposal"
        )
    payload = {
        "receipt_version": APPROVAL_RECEIPT_VERSION,
        "event_type": "tournament_proposal_approved",
        **bindings,
        "approved_by": actor,
        "replay_key": _replay_key(
            operation="approve",
            case_id=bindings["case_id"],
            proposal_hash=bindings["proposal_hash"],
            actor_id=str(actor["id"]),
        ),
        "approved_case_version": _positive_version(
            approved_case_version, field_name="approved_case_version"
        ),
        "approved_at": _clean_timestamp(approved_at, field_name="approved_at"),
        "note": str(note).strip() if note is not None else None,
        "decision": APPROVED,
        "execution_status": "not_executed",
        "operational_writes_performed": False,
    }
    receipt = ApprovalReceipt(
        payload_json=canonical_json(payload),
        receipt_hash=_hash_payload(payload),
    )
    return TournamentApplicationContract(
        frozen_proposal=contract.frozen_proposal,
        state=APPROVED,
        approval_receipt=receipt,
    )


def _effective_projection(
    proposal_payload: Mapping[str, Any],
    effective_projection: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    draft = proposal_payload.get("draft")
    if not isinstance(draft, Mapping):
        raise TournamentApplicationTamperedError("Proposal draft must be an object")
    if not all(key in draft for key in PROJECTION_FIELDS):
        raise TournamentApplicationTamperedError(
            "Proposal draft must contain all eight tournament fields"
        )
    approved = {key: draft.get(key) for key in PROJECTION_FIELDS}
    source = approved if effective_projection is None else effective_projection
    if not isinstance(source, Mapping) or set(source) != set(PROJECTION_FIELDS):
        raise TournamentApplicationContractError(
            "Effective projection must contain exactly the eight tournament fields"
        )
    effective = {key: source.get(key) for key in PROJECTION_FIELDS}
    if canonical_json(effective) != canonical_json(approved):
        raise TournamentApplicationTamperedError(
            "Effective projection differs from the frozen approved draft"
        )
    return json.loads(canonical_json(approved))


def _write_set(
    proposal_payload: Mapping[str, Any],
    target_id: str,
    *,
    effective_projection: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    projection = _effective_projection(proposal_payload, effective_projection)
    return {
        "operation": "insert",
        "table": "tournaments",
        "record": {
            "id": target_id,
            "name": projection["name"],
            "description": projection["description"],
            "active": projection["active"],
            "display_order": projection["display_order"],
            "cuenta_contable_relacionada": projection["accounting_account"],
            "etapas": list(projection["stages"] or []),
            "categorias": list(projection["categories"] or []),
            "form_visibility_areas": list(projection["visibility_areas"] or []),
        },
        "row_count": 1,
    }


def apply_tournament_application(
    contract: TournamentApplicationContract,
    *,
    applied_by: Mapping[str, Any],
    applied_case_version: int,
    applied_at: str,
    target_tournament_id: str,
    expected_approval_receipt_hash: str,
    expected_proposal_hash: str,
    expected_draft_hash: str,
    verified_source_hash: str,
    effective_projection: Optional[Mapping[str, Any]] = None,
) -> TournamentApplicationContract:
    """Record the one exact local Tournament insert authorized for application."""

    if contract.state != APPROVED or contract.approval_receipt is None:
        raise TournamentApplicationContractError(
            "Only an approved proposal can be applied"
        )
    bindings = _assert_expected(
        contract,
        expected_proposal_hash=expected_proposal_hash,
        expected_draft_hash=expected_draft_hash,
        verified_source_hash=verified_source_hash,
    )
    approval = verify_approval_receipt(contract.approval_receipt)
    if (
        str(expected_approval_receipt_hash or "").strip().casefold()
        != contract.approval_receipt.receipt_hash
    ):
        raise TournamentApplicationContractError(
            "Expected approval receipt binding is stale"
        )
    actor = _verified_actor(applied_by, field_name="applied_by")
    if str(actor["id"]) == bindings["owner_employee_id"]:
        raise TournamentApplicationContractError(
            "Proposal owner cannot apply the proposal"
        )
    try:
        target_id = str(UUID(str(target_tournament_id).strip()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TournamentApplicationContractError(
            "target_tournament_id must be a UUID"
        ) from exc
    proposal_payload = verify_frozen_proposal(contract.frozen_proposal)
    effective = _effective_projection(proposal_payload, effective_projection)
    payload = {
        "receipt_version": APPLICATION_RECEIPT_VERSION,
        "event_type": "tournament_proposal_applied",
        **bindings,
        "approval_receipt_hash": contract.approval_receipt.receipt_hash,
        "approved_by_employee_id": str(
            (approval.get("approved_by") or {}).get("id") or ""
        ),
        "applied_by": actor,
        "replay_key": _replay_key(
            operation="apply",
            case_id=bindings["case_id"],
            proposal_hash=bindings["proposal_hash"],
        ),
        "applied_case_version": _positive_version(
            applied_case_version, field_name="applied_case_version"
        ),
        "applied_at": _clean_timestamp(applied_at, field_name="applied_at"),
        "target_tournament_id": target_id,
        "effective_projection": effective,
        "write_set": _write_set(
            proposal_payload,
            target_id,
            effective_projection=effective,
        ),
        "persistence_verification": {
            "precommit_readback_matches_effective_projection": True,
            "operations_link_created_by_transaction": False,
        },
        "no_claims": list(NO_CLAIMS),
        "execution_status": APPLIED,
        "operational_writes_performed": True,
        "external_notifications_enqueued": False,
    }
    receipt = ApplicationReceipt(
        payload_json=canonical_json(payload),
        receipt_hash=_hash_payload(payload),
    )
    return TournamentApplicationContract(
        frozen_proposal=contract.frozen_proposal,
        state=APPLIED,
        approval_receipt=contract.approval_receipt,
        application_receipt=receipt,
    )


def verify_approval_receipt(
    receipt: Union[ApprovalReceipt, Mapping[str, Any]],
) -> Dict[str, Any]:
    value = (
        receipt
        if isinstance(receipt, ApprovalReceipt)
        else ApprovalReceipt(
            payload_json=canonical_json(receipt.get("payload") or {}),
            receipt_hash=str(receipt.get("receipt_hash") or ""),
        )
    )
    payload = value.payload
    actor = payload.get("approved_by")
    if (
        value.receipt_hash != _hash_payload(payload)
        or payload.get("receipt_version") != APPROVAL_RECEIPT_VERSION
        or payload.get("event_type") != "tournament_proposal_approved"
        or payload.get("decision") != APPROVED
        or payload.get("execution_status") != "not_executed"
        or payload.get("operational_writes_performed") is not False
        or not isinstance(actor, Mapping)
        or actor.get("activo") is not True
        or not str(actor.get("id") or "").strip()
        or str(actor.get("id")) == str(payload.get("owner_employee_id"))
        or payload.get("replay_key")
        != _replay_key(
            operation="approve",
            case_id=str(payload.get("case_id") or ""),
            proposal_hash=str(payload.get("proposal_hash") or ""),
            actor_id=str(actor.get("id") or ""),
        )
        or not CASE_ID_PATTERN.fullmatch(str(payload.get("case_id") or ""))
        or not SHA256_PATTERN.fullmatch(str(payload.get("proposal_hash") or ""))
        or not DIGEST_PATTERN.fullmatch(str(payload.get("draft_hash") or ""))
        or not SHA256_PATTERN.fullmatch(str(payload.get("source_hash") or ""))
    ):
        raise TournamentApplicationTamperedError(
            "Approval receipt bindings are inconsistent"
        )
    _positive_version(
        payload.get("approved_case_version"), field_name="approved_case_version"
    )
    _clean_timestamp(payload.get("approved_at"), field_name="approved_at")
    return dict(payload)


def verify_application_receipt(
    receipt: Union[ApplicationReceipt, Mapping[str, Any]],
) -> Dict[str, Any]:
    value = (
        receipt
        if isinstance(receipt, ApplicationReceipt)
        else ApplicationReceipt(
            payload_json=canonical_json(receipt.get("payload") or {}),
            receipt_hash=str(receipt.get("receipt_hash") or ""),
        )
    )
    payload = value.payload
    actor = payload.get("applied_by")
    write_set = payload.get("write_set")
    effective_projection = payload.get("effective_projection")
    persistence_verification = payload.get("persistence_verification")
    record = write_set.get("record") if isinstance(write_set, Mapping) else None
    if (
        value.receipt_hash != _hash_payload(payload)
        or payload.get("receipt_version") != APPLICATION_RECEIPT_VERSION
        or payload.get("event_type") != "tournament_proposal_applied"
        or payload.get("execution_status") != APPLIED
        or payload.get("operational_writes_performed") is not True
        or payload.get("external_notifications_enqueued") is not False
        or payload.get("no_claims") != list(NO_CLAIMS)
        or not isinstance(actor, Mapping)
        or actor.get("activo") is not True
        or not str(actor.get("id") or "").strip()
        or str(actor.get("id")) == str(payload.get("owner_employee_id"))
        or payload.get("replay_key")
        != _replay_key(
            operation="apply",
            case_id=str(payload.get("case_id") or ""),
            proposal_hash=str(payload.get("proposal_hash") or ""),
        )
        or not isinstance(write_set, Mapping)
        or not isinstance(effective_projection, Mapping)
        or set(effective_projection) != set(PROJECTION_FIELDS)
        or persistence_verification
        != {
            "precommit_readback_matches_effective_projection": True,
            "operations_link_created_by_transaction": False,
        }
        or write_set.get("operation") != "insert"
        or write_set.get("table") != "tournaments"
        or write_set.get("row_count") != 1
        or not isinstance(record, Mapping)
        or set(record) != set(WRITE_FIELDS)
        or record.get("id") != payload.get("target_tournament_id")
        or not SHA256_PATTERN.fullmatch(str(payload.get("proposal_hash") or ""))
        or not DIGEST_PATTERN.fullmatch(str(payload.get("draft_hash") or ""))
        or not SHA256_PATTERN.fullmatch(str(payload.get("source_hash") or ""))
        or not SHA256_PATTERN.fullmatch(str(payload.get("approval_receipt_hash") or ""))
    ):
        raise TournamentApplicationTamperedError(
            "Application receipt bindings are inconsistent"
        )
    try:
        UUID(str(payload.get("target_tournament_id") or ""))
    except ValueError as exc:
        raise TournamentApplicationTamperedError(
            "Application target binding is invalid"
        ) from exc
    _positive_version(
        payload.get("applied_case_version"), field_name="applied_case_version"
    )
    _clean_timestamp(payload.get("applied_at"), field_name="applied_at")
    return dict(payload)


def verify_tournament_application_contract(
    contract: TournamentApplicationContract,
) -> Dict[str, Any]:
    bindings = _expected_bindings(contract)
    if contract.approval_receipt is not None:
        approval = verify_approval_receipt(contract.approval_receipt)
        if any(approval.get(key) != value for key, value in bindings.items()):
            raise TournamentApplicationTamperedError(
                "Approval receipt does not bind the frozen proposal"
            )
    if contract.application_receipt is not None:
        application = verify_application_receipt(contract.application_receipt)
        if any(application.get(key) != value for key, value in bindings.items()):
            raise TournamentApplicationTamperedError(
                "Application receipt does not bind the frozen proposal"
            )
        if (
            contract.approval_receipt is None
            or application.get("approval_receipt_hash")
            != contract.approval_receipt.receipt_hash
            or application.get("approved_by_employee_id")
            != str(
                contract.approval_receipt.payload.get("approved_by", {}).get("id") or ""
            )
            or application.get("write_set")
            != _write_set(
                contract.frozen_proposal.payload,
                str(application.get("target_tournament_id") or ""),
                effective_projection=application.get("effective_projection"),
            )
        ):
            raise TournamentApplicationTamperedError(
                "Application receipt authority or write set is inconsistent"
            )
    return contract.to_dict()


__all__ = [
    "APPLIED",
    "APPROVED",
    "FROZEN",
    "ApplicationReceipt",
    "ApprovalReceipt",
    "NO_CLAIMS",
    "TournamentApplicationContract",
    "TournamentApplicationContractError",
    "TournamentApplicationTamperedError",
    "apply_tournament_application",
    "approve_tournament_application",
    "start_tournament_application",
    "verify_application_receipt",
    "verify_approval_receipt",
    "verify_tournament_application_contract",
]
