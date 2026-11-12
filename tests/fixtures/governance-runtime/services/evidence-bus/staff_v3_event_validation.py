"""Strict, non-PII structural validation for staff v3 evidence events."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Sha = Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]
Hmac = Annotated[str, StringConstraints(pattern=r"^hmac-sha256:[a-f0-9]{64}$")]
Uuid = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
Version = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
Timestamp = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$"),
]
Transaction = Annotated[str, StringConstraints(pattern=r"^samchat-tx-[a-f0-9]{24}$")]
Reason = Literal[
    "CANDIDATE_POLICY_SATISFIED",
    "FIELD_EVIDENCE_MISSING",
    "HUMAN_APPROVAL_REQUIRED",
    "STALE_GOVERNANCE_DECISION",
    "BLOCKING_INCIDENT_PRESENT",
    "STAFF_REVIEW_REQUIRED",
    "STAFF_SCOPE_INVALID",
    "STAFF_SOURCE_BINDING_MISMATCH",
    "STAFF_FIELD_EVIDENCE_MISSING",
    "STAFF_GEOMETRY_INVALID",
    "STAFF_FINALITY_MISMATCH",
    "STAFF_REVIEW_EXPIRED",
    "STAFF_REVIEW_REVOKED",
    "STAFF_REVIEW_ALREADY_USED",
    "PREAUTHORIZATION_RECEIPT_INVALID",
    "EVIDENCE_WRITE_FAILED_FAIL_CLOSED",
]


class StrictEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: Slug
    tournament_edition_id: Uuid
    tournament_slug: Slug
    team_id: Uuid
    session_id: Uuid
    draft_id: Uuid
    draft_version: Annotated[int, Field(ge=1)]
    timestamp: Timestamp
    policy_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    workflow_id: Literal["samchat_registration_identity_v2"]
    effective_sal: Literal["SAL-3"]
    policy_id: Literal["samchat_registration_identity_policy_v2"]
    policy_version: Version
    attempt_id: Sha
    operation_id: Sha


class StaffSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot: Annotated[int, Field(ge=1, le=2)]
    role: Literal["director_tecnico", "auxiliar"]
    staff_entry_binding: Hmac
    presence: Literal["PRESENT", "ABSENT"]
    source_page_hash: Sha
    evidence_binding: Hmac
    review_decision_hash: Sha
    field_decision_hashes: list[Sha]

    @model_validator(mode="after")
    def validate_slot(self):
        if self.role != ("director_tecnico" if self.slot == 1 else "auxiliar"):
            raise ValueError("slot role mismatch")
        if (self.presence == "ABSENT") != (len(self.field_decision_hashes) == 0):
            raise ValueError("presence field decision mismatch")
        if len(set(self.field_decision_hashes)) != len(self.field_decision_hashes):
            raise ValueError("duplicate field decisions")
        return self


class RosterDecision(StrictEvent):
    authorization_id: Uuid
    member_proof_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    batch_context_hash: Sha
    event_type: Literal["samchat_registration_roster_decision_v3"]
    decision_id: Sha
    ordered_player_slots: list[Annotated[int, Field(ge=1, le=35)]]
    ordered_player_field_decision_hashes: list[Sha]
    ordered_staff_slots: list[StaffSlot]
    source_page_bindings: list[Sha]
    incident_state_hash: Sha
    roster_draft_binding: Hmac
    decision: Literal[
        "AUTHORIZE_PENDING_MATERIALIZATION",
        "REQUIRE_ROSTER_REVIEW",
        "DENY_ROSTER_MATERIALIZATION",
    ]
    reason_codes: list[Reason]
    decision_hash: Sha

    @model_validator(mode="after")
    def validate_scope(self):
        if [item.slot for item in self.ordered_staff_slots] != [1, 2]:
            raise ValueError("staff slots invalid")
        if self.ordered_player_slots != sorted(set(self.ordered_player_slots)):
            raise ValueError("player slots invalid")
        if not self.source_page_bindings or not self.reason_codes:
            raise ValueError("evidence missing")
        if any(
            slot.source_page_hash not in self.source_page_bindings
            for slot in self.ordered_staff_slots
        ):
            raise ValueError("staff source mismatch")
        return self


class StaffFinality(StrictEvent):
    event_type: Literal["samchat_registration_staff_finality_attestation_v3"]
    attestation_id: Sha
    staff_id: Uuid
    staff_entry_binding: Hmac
    staff_slot: Annotated[int, Field(ge=1, le=2)]
    staff_role: Literal["director_tecnico", "auxiliar"]
    roster_draft_binding: Hmac
    preauthorization_receipt_id: Sha
    database_transaction_id: Transaction
    cas_succeeded: bool
    provisional_row_binding: Hmac
    post_execution_row_binding: Hmac
    blocking_incident_count: Annotated[int, Field(ge=0)]
    decision: Literal["ACTIVATE_STAFF", "DENY_STAFF_ACTIVATION"]
    reason_codes: list[Reason]
    attestation_hash: Sha

    @model_validator(mode="after")
    def validate_finality(self):
        if self.staff_role != ("director_tecnico" if self.staff_slot == 1 else "auxiliar"):
            raise ValueError("staff role mismatch")
        if self.decision == "ACTIVATE_STAFF" and (
            not self.cas_succeeded or self.blocking_incident_count
        ):
            raise ValueError("activation conditions missing")
        if not self.reason_codes:
            raise ValueError("reason missing")
        return self


class PlayerFinality(StrictEvent):
    event_type: Literal["samchat_registration_player_finality_attestation_v3"]
    attestation_id: Sha
    player_id: Uuid
    player_entry_binding: Hmac
    player_slot: Annotated[int, Field(ge=1, le=35)]
    roster_draft_binding: Hmac
    preauthorization_receipt_id: Sha
    database_transaction_id: Transaction
    cas_succeeded: bool
    provisional_row_binding: Hmac
    post_execution_row_binding: Hmac
    blocking_incident_count: Annotated[int, Field(ge=0)]
    decision: Literal["ACTIVATE_PLAYER", "DENY_PLAYER_ACTIVATION"]
    reason_codes: list[Reason]
    attestation_hash: Sha

    @model_validator(mode="after")
    def validate_finality(self):
        if self.decision == "ACTIVATE_PLAYER" and (
            not self.cas_succeeded or self.blocking_incident_count
        ):
            raise ValueError("activation conditions missing")
        if not self.reason_codes:
            raise ValueError("reason missing")
        return self


def validate_staff_v3_event(event: dict) -> bool:
    """Reject unrecognized fields or free-text values before durable storage."""
    kind = event.get("event_type")
    try:
        if kind == "samchat_registration_roster_decision_v3":
            RosterDecision.model_validate(event)
        elif kind == "samchat_registration_staff_finality_attestation_v3":
            StaffFinality.model_validate(event)
        elif kind == "samchat_registration_player_finality_attestation_v3":
            PlayerFinality.model_validate(event)
        else:
            return False
        return True
    except (ValueError, TypeError):
        return False
