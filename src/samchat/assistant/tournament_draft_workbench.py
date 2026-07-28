"""Pure versioned-review contract for a tournament goal draft.

The workbench has no persistence, ORM, router, network, or operational action
dependencies.  Callers own version allocation and storage in ``AnalystCase``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from .tournament_goal_shadow import (
    EXECUTION_STATUS,
    TournamentDraft,
    TournamentGoalShadow,
    TournamentPlanStep,
    ValidationFinding,
    build_tournament_goal_shadow,
    canonical_json,
    canonical_sha256,
)


WORKBENCH_VERSION = "tournament_draft_workbench_v1"
PROPOSAL_VERSION = "tournament_proposal_freeze_v1"
DRAFTING = "drafting"
FROZEN = "frozen"
ABANDONED = "abandoned"
WORKBENCH_STATES = frozenset({DRAFTING, FROZEN, ABANDONED})
CASE_ID_PATTERN = re.compile(r"^analyst_case_[0-9a-f]{32}$")
SOURCE_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
INPUT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
MAX_REVIEW_INPUT_BYTES = 8_000
MAX_ABANDON_REASON_CHARS = 1_000
DATE_INPUT_KEYS = frozenset(
    {
        "source_component:rich_tournament_dates",
        "tournament_dates",
    }
)


class TournamentDraftWorkbenchError(ValueError):
    """Raised when a pure workbench transition violates its contract."""


class TournamentProposalTamperedError(TournamentDraftWorkbenchError):
    """Raised when a frozen proposal no longer matches its bound hash."""


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


def _clean_sequence(value: Any, *, field_name: str) -> Tuple[str, ...]:
    if value is None or isinstance(value, (str, bytes)):
        raise TournamentDraftWorkbenchError(
            f"{field_name} must be an array of text values"
        )
    if not isinstance(value, Sequence):
        raise TournamentDraftWorkbenchError(
            f"{field_name} must be an array of text values"
        )
    if any(not isinstance(item, str) for item in value):
        raise TournamentDraftWorkbenchError(
            f"{field_name} must contain only text values"
        )
    return tuple(item.strip() for item in value)


@dataclass(frozen=True)
class TournamentDraftPatch:
    """Patch whose UNSET fields preserve and explicit null fields clear."""

    name: Any = UNSET
    description: Any = UNSET
    active: Any = UNSET
    display_order: Any = UNSET
    accounting_account: Any = UNSET
    stages: Any = UNSET
    categories: Any = UNSET
    visibility_areas: Any = UNSET

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TournamentDraftPatch":
        allowed = {
            "name",
            "description",
            "active",
            "display_order",
            "accounting_account",
            "stages",
            "categories",
            "visibility_areas",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise TournamentDraftWorkbenchError(
                "Unsupported tournament patch fields: " + ", ".join(unknown)
            )
        values = {field: payload[field] for field in allowed if field in payload}
        for required in ("name", "active", "display_order"):
            if values.get(required, UNSET) is None:
                raise TournamentDraftWorkbenchError(f"{required} cannot be null")
        name = values.get("name", UNSET)
        if name is not UNSET and not isinstance(name, str):
            raise TournamentDraftWorkbenchError("name must be text")
        for optional_text in ("description", "accounting_account"):
            value = values.get(optional_text, UNSET)
            if value is not UNSET and value is not None and not isinstance(value, str):
                raise TournamentDraftWorkbenchError(
                    f"{optional_text} must be text or null"
                )
        for sequence_field in ("stages", "categories", "visibility_areas"):
            value = values.get(sequence_field, UNSET)
            if value is not UNSET:
                values[sequence_field] = _clean_sequence(
                    value, field_name=sequence_field
                )
        active = values.get("active", UNSET)
        if active is not UNSET and not isinstance(active, bool):
            raise TournamentDraftWorkbenchError("active must be boolean")
        display_order = values.get("display_order", UNSET)
        if display_order is not UNSET and (
            isinstance(display_order, bool) or not isinstance(display_order, int)
        ):
            raise TournamentDraftWorkbenchError("display_order must be an integer")
        return cls(**values)

    @property
    def changed_fields(self) -> Tuple[str, ...]:
        return tuple(
            field_name
            for field_name in (
                "name",
                "description",
                "active",
                "display_order",
                "accounting_account",
                "stages",
                "categories",
                "visibility_areas",
            )
            if getattr(self, field_name) is not UNSET
        )

    def apply(self, draft: TournamentDraft) -> Dict[str, Any]:
        values: Dict[str, Any] = {
            "name": draft.name,
            "description": draft.description,
            "active": draft.active,
            "display_order": draft.display_order,
            "accounting_account": draft.accounting_account,
            "stages": draft.stages,
            "categories": draft.categories,
            "visibility_areas": draft.visibility_areas,
        }
        for field_name in self.changed_fields:
            values[field_name] = getattr(self, field_name)
        return values


def _normalize_date_input(value: Any) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        raise TournamentDraftWorkbenchError(
            "Tournament dates require start_date and end_date"
        )
    unknown = sorted(set(value) - {"start_date", "end_date"})
    if unknown:
        raise TournamentDraftWorkbenchError(
            "Unsupported tournament date fields: " + ", ".join(unknown)
        )
    normalized: Dict[str, str] = {}
    parsed: Dict[str, date] = {}
    for field_name in ("start_date", "end_date"):
        raw = str(value.get(field_name) or "").strip()
        if not raw:
            raise TournamentDraftWorkbenchError(
                f"Tournament dates require {field_name}"
            )
        try:
            current = date.fromisoformat(raw)
        except ValueError as exc:
            raise TournamentDraftWorkbenchError(
                f"{field_name} must use YYYY-MM-DD"
            ) from exc
        normalized[field_name] = current.isoformat()
        parsed[field_name] = current
    if parsed["end_date"] < parsed["start_date"]:
        raise TournamentDraftWorkbenchError(
            "end_date cannot be earlier than start_date"
        )
    return normalized


def _normalize_review_value(key: str, value: Any) -> Any:
    if key in DATE_INPUT_KEYS:
        return _normalize_date_input(value)
    if isinstance(value, str):
        normalized: Any = value.strip()
        if not normalized:
            raise TournamentDraftWorkbenchError(f"Review input {key} cannot be blank")
    elif isinstance(value, (Mapping, list, tuple)):
        normalized = json.loads(canonical_json(value))
        if normalized in ({}, []):
            raise TournamentDraftWorkbenchError(f"Review input {key} cannot be empty")
    else:
        raise TournamentDraftWorkbenchError(
            f"Review input {key} must be text, object, or array"
        )
    if len(canonical_json(normalized).encode("utf-8")) > MAX_REVIEW_INPUT_BYTES:
        raise TournamentDraftWorkbenchError(f"Review input {key} is too large")
    return normalized


@dataclass(frozen=True)
class TournamentReviewInput:
    key: str
    value_json: str

    @classmethod
    def build(cls, key: str, value: Any) -> "TournamentReviewInput":
        normalized_key = str(key or "").strip().casefold()
        if not INPUT_KEY_PATTERN.fullmatch(normalized_key):
            raise TournamentDraftWorkbenchError("Invalid review input key")
        normalized_value = _normalize_review_value(normalized_key, value)
        return cls(
            key=normalized_key,
            value_json=canonical_json(normalized_value),
        )

    @property
    def value(self) -> Any:
        return json.loads(self.value_json)

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "value": self.value}


@dataclass(frozen=True)
class TournamentReviewInputs:
    items: Tuple[TournamentReviewInput, ...] = ()

    @classmethod
    def from_mapping(
        cls, payload: Optional[Mapping[str, Any]] = None
    ) -> "TournamentReviewInputs":
        items = [
            TournamentReviewInput.build(key, value)
            for key, value in (payload or {}).items()
        ]
        items.sort(key=lambda item: item.key)
        return cls(items=tuple(items))

    def updated(self, changes: Mapping[str, Any]) -> "TournamentReviewInputs":
        current = {item.key: item for item in self.items}
        for raw_key, raw_value in changes.items():
            key = str(raw_key or "").strip().casefold()
            if not INPUT_KEY_PATTERN.fullmatch(key):
                raise TournamentDraftWorkbenchError("Invalid review input key")
            if raw_value is None:
                current.pop(key, None)
            else:
                current[key] = TournamentReviewInput.build(key, raw_value)
        return TournamentReviewInputs(
            items=tuple(current[key] for key in sorted(current))
        )

    def keys(self) -> Tuple[str, ...]:
        return tuple(item.key for item in self.items)

    def to_dict(self) -> Dict[str, Any]:
        return {item.key: item.value for item in self.items}


@dataclass(frozen=True)
class FrozenTournamentProposal:
    payload_json: str
    proposal_hash: str

    @property
    def payload(self) -> Dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise TournamentProposalTamperedError(
                "Frozen proposal payload must be an object"
            )
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payload": self.payload,
            "proposal_hash": self.proposal_hash,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FrozenTournamentProposal":
        raw_payload = payload.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise TournamentProposalTamperedError(
                "Frozen proposal payload must be an object"
            )
        proposal = cls(
            payload_json=canonical_json(raw_payload),
            proposal_hash=str(payload.get("proposal_hash") or ""),
        )
        verify_frozen_proposal(proposal)
        return proposal


@dataclass(frozen=True)
class TournamentDraftWorkbench:
    shadow: TournamentGoalShadow
    review_inputs: TournamentReviewInputs = TournamentReviewInputs()
    state: str = DRAFTING
    frozen_proposal: Optional[FrozenTournamentProposal] = None
    abandoned_reason: Optional[str] = None
    workbench_version: str = WORKBENCH_VERSION
    execution_status: str = EXECUTION_STATUS
    operational_writes_allowed: bool = False

    def __post_init__(self) -> None:
        if self.state not in WORKBENCH_STATES:
            raise TournamentDraftWorkbenchError("Invalid workbench state")
        if self.execution_status != EXECUTION_STATUS:
            raise TournamentDraftWorkbenchError("Workbench cannot claim execution")
        if self.operational_writes_allowed:
            raise TournamentDraftWorkbenchError(
                "Workbench cannot allow operational writes"
            )
        if self.state == FROZEN and self.frozen_proposal is None:
            raise TournamentDraftWorkbenchError("Frozen workbench requires a proposal")
        if self.state != FROZEN and self.frozen_proposal is not None:
            if self.state != ABANDONED:
                raise TournamentDraftWorkbenchError(
                    "Only frozen or abandoned workbench may retain a proposal"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workbench_version": self.workbench_version,
            "state": self.state,
            "execution_status": self.execution_status,
            "operational_writes_allowed": self.operational_writes_allowed,
            "review_inputs": self.review_inputs.to_dict(),
            "frozen_proposal": (
                self.frozen_proposal.to_dict() if self.frozen_proposal else None
            ),
            "abandoned_reason": self.abandoned_reason,
        }


def _plan_with_review_status(
    shadow: TournamentGoalShadow, *, status: str
) -> TournamentGoalShadow:
    steps = tuple(
        replace(step, status=status) if step.step_id == "await_review" else step
        for step in shadow.plan.steps
    )
    return replace(shadow, plan=replace(shadow.plan, steps=steps))


def _resolved_missing_information(
    shadow: TournamentGoalShadow,
    review_inputs: TournamentReviewInputs,
) -> Tuple[str, ...]:
    resolved = set(review_inputs.keys())
    return tuple(
        item
        for item in shadow.missing_information
        if not item.startswith("source_component:") or item not in resolved
    )


def revise_tournament_draft(
    current: TournamentDraftWorkbench,
    *,
    patch: TournamentDraftPatch,
    review_input_changes: Optional[Mapping[str, Any]] = None,
    additional_findings: Iterable[ValidationFinding] = (),
) -> TournamentDraftWorkbench:
    """Create a new pure draft revision while preserving omitted fields."""

    if current.state != DRAFTING:
        raise TournamentDraftWorkbenchError("Only a drafting workbench can be revised")
    inputs = current.review_inputs.updated(review_input_changes or {})
    values = patch.apply(current.shadow.draft)
    revised = build_tournament_goal_shadow(
        current.shadow.source,
        requested_name=str(values.pop("name")),
        overrides=values,
        goal=current.shadow.plan.goal,
        additional_findings=additional_findings,
    )
    missing = _resolved_missing_information(revised, inputs)
    if any(item.severity == "error" for item in revised.validation.findings):
        review_status = "blocked"
    elif missing:
        review_status = "waiting_input"
    else:
        review_status = "pending"
    revised = replace(revised, missing_information=missing)
    revised = _plan_with_review_status(revised, status=review_status)
    return TournamentDraftWorkbench(
        shadow=revised,
        review_inputs=inputs,
        state=DRAFTING,
    )


def _freeze_payload(
    *,
    case_id: str,
    draft_case_version: int,
    workbench: TournamentDraftWorkbench,
    verified_owner: Mapping[str, Any],
    verified_source_hash: str,
) -> Dict[str, Any]:
    shadow = workbench.shadow
    return {
        "proposal_version": PROPOSAL_VERSION,
        "case_id": case_id,
        "draft_case_version": draft_case_version,
        "source_authority_hash": shadow.source.snapshot_hash,
        "work_product_hash": shadow.work_product_hash,
        "draft_hash": shadow.draft.draft_hash,
        "draft": shadow.draft.to_dict(),
        "validation": shadow.validation.to_dict(),
        "business_diff": shadow.business_diff.to_dict(),
        "review_inputs": workbench.review_inputs.to_dict(),
        "owner_employee_id": str(verified_owner.get("id") or "").strip(),
        "verified_authority": {
            "owner": json.loads(canonical_json(verified_owner)),
            "source_hash": verified_source_hash,
            "source_hash_verified": True,
            "domain_write_performed": False,
        },
        "execution_status": EXECUTION_STATUS,
        "operational_writes_allowed": False,
    }


def freeze_tournament_proposal(
    workbench: TournamentDraftWorkbench,
    *,
    case_id: str,
    draft_case_version: int,
    verified_owner: Mapping[str, Any],
    verified_source_hash: str,
) -> TournamentDraftWorkbench:
    """Freeze one exact, review-complete draft into a hash-bound proposal."""

    if workbench.state != DRAFTING:
        raise TournamentDraftWorkbenchError("Only a drafting workbench can be frozen")
    if not CASE_ID_PATTERN.fullmatch(str(case_id or "")):
        raise TournamentDraftWorkbenchError("Invalid AnalystCase id")
    if (
        isinstance(draft_case_version, bool)
        or not isinstance(draft_case_version, int)
        or draft_case_version < 1
    ):
        raise TournamentDraftWorkbenchError(
            "draft_case_version must be a positive integer"
        )
    if not workbench.shadow.validation.valid:
        raise TournamentDraftWorkbenchError(
            "A proposal with validation errors cannot be frozen"
        )
    if workbench.shadow.missing_information:
        raise TournamentDraftWorkbenchError(
            "Resolve all missing information before freezing"
        )
    owner = json.loads(canonical_json(verified_owner))
    owner_id = str(owner.get("id") or "").strip()
    source_hash = str(verified_source_hash or "").strip().casefold()
    if not owner_id or owner.get("activo") is not True:
        raise TournamentDraftWorkbenchError(
            "Verified proposal owner must be an active employee"
        )
    if (
        not SOURCE_HASH_PATTERN.fullmatch(source_hash)
        or source_hash != workbench.shadow.source.snapshot_hash
    ):
        raise TournamentDraftWorkbenchError(
            "Verified source hash does not match the bound source"
        )
    payload = _freeze_payload(
        case_id=case_id,
        draft_case_version=draft_case_version,
        workbench=workbench,
        verified_owner=owner,
        verified_source_hash=source_hash,
    )
    payload_json = canonical_json(payload)
    proposal = FrozenTournamentProposal(
        payload_json=payload_json,
        proposal_hash=f"sha256:{canonical_sha256(payload)}",
    )
    frozen_shadow = _plan_with_review_status(workbench.shadow, status="frozen")
    return TournamentDraftWorkbench(
        shadow=frozen_shadow,
        review_inputs=workbench.review_inputs,
        state=FROZEN,
        frozen_proposal=proposal,
    )


def verify_frozen_proposal(
    proposal: Union[FrozenTournamentProposal, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return the verified payload or fail closed after any mutation."""

    if isinstance(proposal, FrozenTournamentProposal):
        payload = proposal.payload
        observed_hash = proposal.proposal_hash
    else:
        payload = proposal.get("payload")
        observed_hash = str(proposal.get("proposal_hash") or "")
        if not isinstance(payload, Mapping):
            raise TournamentProposalTamperedError(
                "Frozen proposal payload must be an object"
            )
        payload = json.loads(canonical_json(payload))
    expected_hash = f"sha256:{canonical_sha256(payload)}"
    if observed_hash != expected_hash:
        raise TournamentProposalTamperedError("Frozen proposal hash mismatch")
    if payload.get("proposal_version") != PROPOSAL_VERSION:
        raise TournamentProposalTamperedError("Frozen proposal version mismatch")
    if payload.get("execution_status") != EXECUTION_STATUS:
        raise TournamentProposalTamperedError("Frozen proposal claims execution")
    if payload.get("operational_writes_allowed") is not False:
        raise TournamentProposalTamperedError(
            "Frozen proposal allows operational writes"
        )
    source_hash = payload.get("source_authority_hash")
    draft = payload.get("draft") or {}
    business_diff = payload.get("business_diff") or {}
    authority = payload.get("verified_authority") or {}
    if not all(isinstance(item, Mapping) for item in (draft, business_diff, authority)):
        raise TournamentProposalTamperedError(
            "Frozen proposal internal bindings must be objects"
        )
    owner = authority.get("owner") or {}
    if not isinstance(owner, Mapping):
        raise TournamentProposalTamperedError(
            "Frozen proposal owner binding must be an object"
        )
    if (
        draft.get("base_snapshot_hash") != source_hash
        or business_diff.get("base_snapshot_hash") != source_hash
        or business_diff.get("draft_hash") != payload.get("draft_hash")
        or canonical_sha256(draft) != payload.get("draft_hash")
        or authority.get("source_hash") != source_hash
        or authority.get("source_hash_verified") is not True
        or authority.get("domain_write_performed") is not False
        or not str(owner.get("id") or "").strip()
        or str(owner.get("id") or "").strip()
        != str(payload.get("owner_employee_id") or "").strip()
        or owner.get("activo") is not True
        or not SOURCE_HASH_PATTERN.fullmatch(str(source_hash or ""))
    ):
        raise TournamentProposalTamperedError(
            "Frozen proposal internal hash bindings are inconsistent"
        )
    return dict(payload)


def abandon_tournament_workbench(
    workbench: TournamentDraftWorkbench,
    *,
    reason: str,
) -> TournamentDraftWorkbench:
    """Append an inert abandonment state while retaining a frozen proposal."""

    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise TournamentDraftWorkbenchError("Abandonment reason is required")
    if len(clean_reason) > MAX_ABANDON_REASON_CHARS:
        raise TournamentDraftWorkbenchError("Abandonment reason is too long")
    if workbench.state == ABANDONED:
        if workbench.abandoned_reason == clean_reason:
            return workbench
        raise TournamentDraftWorkbenchError("Workbench is already abandoned")
    abandoned_shadow = _plan_with_review_status(workbench.shadow, status="abandoned")
    return TournamentDraftWorkbench(
        shadow=abandoned_shadow,
        review_inputs=workbench.review_inputs,
        state=ABANDONED,
        frozen_proposal=workbench.frozen_proposal,
        abandoned_reason=clean_reason,
    )


__all__ = [
    "ABANDONED",
    "DRAFTING",
    "FROZEN",
    "FrozenTournamentProposal",
    "TournamentDraftPatch",
    "TournamentDraftWorkbench",
    "TournamentDraftWorkbenchError",
    "TournamentProposalTamperedError",
    "TournamentReviewInput",
    "TournamentReviewInputs",
    "abandon_tournament_workbench",
    "freeze_tournament_proposal",
    "revise_tournament_draft",
    "verify_frozen_proposal",
]
