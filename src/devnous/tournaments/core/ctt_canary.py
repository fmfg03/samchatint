"""Reversible rollout controls for the canonical CTT Responses extractor.

The canary is intentionally persistence-free.  It accepts images and returns a
sanitized report plus an in-memory canonical draft; it never receives a
database session or invokes registration persistence code.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Literal, Mapping, Optional, Sequence

from PIL import Image, ImageOps, ImageStat
from pydantic import BaseModel, ConfigDict, Field

from .ctt_extraction_cache import (
    CttCachedExtractionResult,
    CttCachedResponsesExtractor,
)
from .ctt_ocr_contract import CttRegistrationDraft

CANARY_REPORT_VERSION: Literal["ctt.canary_report.v1"] = "ctt.canary_report.v1"


class CttCanaryMode(str, Enum):
    """Rollout modes for the canonical extractor."""

    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


class CttCanarySeverity(str, Enum):
    WARNING = "warning"
    BLOCKER = "blocker"


class CttCanaryInputError(ValueError):
    """A supplied image cannot be trusted as a CTT registration page."""


class CttCanaryIncident(BaseModel):
    """Non-sensitive evidence explaining a canary decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=80)
    severity: CttCanarySeverity
    message: str = Field(min_length=1, max_length=240)
    page: Optional[int] = Field(default=None, ge=1, le=3)
    slot: Optional[int] = Field(default=None, ge=1, le=25)
    field_names: List[str] = Field(default_factory=list)
    validation_codes: List[str] = Field(default_factory=list)


class CttCanaryReport(BaseModel):
    """Sanitized rollout evidence safe for logs and pull requests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ctt.canary_report.v1"] = CANARY_REPORT_VERSION
    mode: CttCanaryMode
    accepted: bool
    use_canonical_result: bool
    no_database_write: Literal[True] = True
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model: Optional[str] = None
    page_count: int = Field(ge=1, le=3)
    slot_count: int = Field(ge=0, le=25)
    occupied_count: int = Field(ge=0, le=25)
    review_count: int = Field(ge=0, le=25)
    provider_response_count: int = Field(ge=0)
    first_cache_hit: Optional[bool] = None
    replay_cache_hit: Optional[bool] = None
    replay_hash_matches: Optional[bool] = None
    incidents: List[CttCanaryIncident] = Field(default_factory=list)


@dataclass(frozen=True)
class CttCanaryPolicy:
    """Acceptance criteria for one bounded rollout cohort."""

    minimum_players: int = 16
    maximum_players: int = 25
    expected_team_name: Optional[str] = None
    require_cache_replay: bool = True

    def __post_init__(self) -> None:
        if self.minimum_players < 1:
            raise ValueError("minimum_players must be positive")
        if self.maximum_players > 25 or self.maximum_players < self.minimum_players:
            raise ValueError("maximum_players must be between minimum_players and 25")


@dataclass(frozen=True)
class CttCanaryExecution:
    """Canary report and optional in-memory draft."""

    report: CttCanaryReport
    draft: Optional[CttRegistrationDraft]


def ctt_document_sha256(page_payloads: Sequence[bytes]) -> str:
    """Hash ordered pages with explicit boundaries."""
    if len(page_payloads) not in (2, 3):
        raise ValueError("CTT documents require two or three page payloads")
    digest = hashlib.sha256()
    for page_number, payload in enumerate(page_payloads, start=1):
        if not payload:
            raise ValueError(f"CTT page {page_number} is empty")
        digest.update(page_number.to_bytes(1, "big"))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def ctt_canary_mode_from_env() -> CttCanaryMode:
    """Read rollout mode, failing closed on invalid values."""
    raw = (
        (os.getenv("CTT_RESPONSES_ROLLOUT") or CttCanaryMode.OFF.value).strip().lower()
    )
    try:
        return CttCanaryMode(raw)
    except ValueError:
        return CttCanaryMode.OFF


def validate_ctt_canary_pages(page_images: Sequence[Image.Image]) -> None:
    """Reject obvious non-form uploads before any provider call."""
    if len(page_images) not in (2, 3):
        raise CttCanaryInputError("CTT canary requires two or three pages")
    for page_number, image in enumerate(page_images, start=1):
        oriented = ImageOps.exif_transpose(image)
        width, height = oriented.size
        if width < 1 or height < 1:
            raise CttCanaryInputError(f"page {page_number} has invalid dimensions")
        portrait_ratio = min(width, height) / max(width, height)
        if not 0.62 <= portrait_ratio <= 0.90:
            raise CttCanaryInputError(
                f"page {page_number} does not have a registration-page aspect ratio"
            )
        sample = oriented.convert("L")
        sample.thumbnail((128, 128), Image.Resampling.BILINEAR)
        mean_luminance = float(ImageStat.Stat(sample).mean[0])
        if mean_luminance < 110.0:
            raise CttCanaryInputError(
                f"page {page_number} is too dark to be a CTT registration page"
            )


def _normalized_match_value(value: Optional[str]) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(without_marks.casefold().split())


def _slot_incident(slot: Any) -> CttCanaryIncident:
    required_fields = {"given_names", "paternal_surname", "birth_date"}
    fields = [
        observation.field_name.value
        for observation in slot.fields.observations()
        if observation.requires_review
        or (
            observation.field_name.value in required_fields
            and observation.normalized_value is None
        )
    ]
    codes = sorted(
        {
            code.value
            for observation in slot.fields.observations()
            for code in observation.validation_codes
        }
        | {code.value for code in slot.validation_codes}
    )
    return CttCanaryIncident(
        code="PLAYER_SLOT_REQUIRES_REVIEW",
        severity=CttCanarySeverity.WARNING,
        message="Occupied player slot requires operator review.",
        page=slot.page,
        slot=slot.slot,
        field_names=sorted(set(fields)),
        validation_codes=codes,
    )


def _draft_incidents(
    draft: CttRegistrationDraft,
    policy: CttCanaryPolicy,
) -> List[CttCanaryIncident]:
    incidents: List[CttCanaryIncident] = []
    occupied = [slot for slot in draft.slots if slot.occupied]
    if len(occupied) < policy.minimum_players:
        incidents.append(
            CttCanaryIncident(
                code="ROSTER_BELOW_MINIMUM",
                severity=CttCanarySeverity.BLOCKER,
                message=(
                    f"Detected {len(occupied)} occupied slots; cohort requires at least "
                    f"{policy.minimum_players}."
                ),
            )
        )
    if len(occupied) > policy.maximum_players:
        incidents.append(
            CttCanaryIncident(
                code="ROSTER_ABOVE_MAXIMUM",
                severity=CttCanarySeverity.BLOCKER,
                message=(
                    f"Detected {len(occupied)} occupied slots; contract allows at most "
                    f"{policy.maximum_players}."
                ),
            )
        )

    team_name = draft.team.fields.name.normalized_value
    if not team_name:
        incidents.append(
            CttCanaryIncident(
                code="TEAM_NAME_MISSING",
                severity=CttCanarySeverity.BLOCKER,
                message="Canonical team name is missing.",
                page=1,
                field_names=["team_name"],
            )
        )
    elif policy.expected_team_name and _normalized_match_value(
        team_name
    ) != _normalized_match_value(policy.expected_team_name):
        incidents.append(
            CttCanaryIncident(
                code="TEAM_NAME_MISMATCH",
                severity=CttCanarySeverity.BLOCKER,
                message="Canonical team name does not match the canary cohort expectation.",
                page=1,
                field_names=["team_name"],
            )
        )

    team_review_fields = sorted(
        observation.field_name.value
        for observation in draft.team.fields.observations()
        if observation.requires_review
    )
    if team_review_fields:
        incidents.append(
            CttCanaryIncident(
                code="TEAM_HEADER_REQUIRES_REVIEW",
                severity=CttCanarySeverity.WARNING,
                message="One or more team header fields require operator review.",
                page=1,
                field_names=team_review_fields,
            )
        )

    occupied_numbers = [slot.slot for slot in occupied]
    if occupied_numbers:
        first_gap = next(
            (
                number
                for number in range(min(occupied_numbers), max(occupied_numbers) + 1)
                if number not in occupied_numbers
            ),
            None,
        )
        if first_gap is not None:
            incidents.append(
                CttCanaryIncident(
                    code="NONCONTIGUOUS_ROSTER",
                    severity=CttCanarySeverity.WARNING,
                    message="An empty slot appears before the last occupied slot.",
                    page=1 if first_gap <= 8 else (2 if first_gap <= 20 else 3),
                    slot=first_gap,
                )
            )

    incidents.extend(_slot_incident(slot) for slot in occupied if slot.requires_review)
    return incidents


class CttCanaryRunner:
    """Execute, replay and evaluate the canonical extractor without persistence."""

    def __init__(
        self,
        extractor: CttCachedResponsesExtractor,
        *,
        mode: CttCanaryMode = CttCanaryMode.SHADOW,
        policy: Optional[CttCanaryPolicy] = None,
    ) -> None:
        self.extractor = extractor
        self.mode = mode
        self.policy = policy or CttCanaryPolicy()

    async def run(
        self,
        page_images: Sequence[Image.Image],
        layout: Mapping[str, Any],
        *,
        document_sha256: str,
    ) -> CttCanaryExecution:
        if self.mode == CttCanaryMode.OFF:
            incident = CttCanaryIncident(
                code="CANARY_DISABLED",
                severity=CttCanarySeverity.BLOCKER,
                message="Canonical extractor rollout is disabled.",
            )
            return CttCanaryExecution(
                report=CttCanaryReport(
                    mode=self.mode,
                    accepted=False,
                    use_canonical_result=False,
                    document_sha256=document_sha256,
                    page_count=len(page_images),
                    slot_count=0,
                    occupied_count=0,
                    review_count=0,
                    provider_response_count=0,
                    incidents=[incident],
                ),
                draft=None,
            )

        try:
            validate_ctt_canary_pages(page_images)
        except CttCanaryInputError as exc:
            incident = CttCanaryIncident(
                code="INVALID_DOCUMENT_IMAGE",
                severity=CttCanarySeverity.BLOCKER,
                message=str(exc),
            )
            return CttCanaryExecution(
                report=CttCanaryReport(
                    mode=self.mode,
                    accepted=False,
                    use_canonical_result=False,
                    document_sha256=document_sha256,
                    page_count=len(page_images),
                    slot_count=0,
                    occupied_count=0,
                    review_count=0,
                    provider_response_count=0,
                    incidents=[incident],
                ),
                draft=None,
            )

        try:
            first = await self.extractor.extract(
                page_images,
                layout,
                document_sha256=document_sha256,
            )
            replay: Optional[CttCachedExtractionResult] = None
            if self.policy.require_cache_replay:
                replay = await self.extractor.extract(
                    page_images,
                    layout,
                    document_sha256=document_sha256,
                )
        except Exception as exc:
            incident = CttCanaryIncident(
                code="EXTRACTION_FAILED",
                severity=CttCanarySeverity.BLOCKER,
                message=f"Canonical extraction failed: {type(exc).__name__}.",
            )
            return CttCanaryExecution(
                report=CttCanaryReport(
                    mode=self.mode,
                    accepted=False,
                    use_canonical_result=False,
                    document_sha256=document_sha256,
                    page_count=len(page_images),
                    slot_count=0,
                    occupied_count=0,
                    review_count=0,
                    provider_response_count=0,
                    incidents=[incident],
                ),
                draft=None,
            )

        draft = first.draft
        occupied = [slot for slot in draft.slots if slot.occupied]
        reviews = [slot for slot in occupied if slot.requires_review]
        incidents = _draft_incidents(draft, self.policy)
        replay_hash_matches: Optional[bool] = None
        if replay is not None:
            replay_hash_matches = (
                replay.draft.canonical_hash() == draft.canonical_hash()
            )
            if not replay.cache_hit:
                incidents.append(
                    CttCanaryIncident(
                        code="CACHE_REPLAY_MISSED",
                        severity=CttCanarySeverity.BLOCKER,
                        message="Repeated extraction did not resolve from the deterministic cache.",
                    )
                )
            if not replay_hash_matches:
                incidents.append(
                    CttCanaryIncident(
                        code="CANONICAL_REPLAY_MISMATCH",
                        severity=CttCanarySeverity.BLOCKER,
                        message="Repeated extraction produced a different canonical hash.",
                    )
                )

        accepted = not any(
            incident.severity == CttCanarySeverity.BLOCKER for incident in incidents
        )
        use_canonical = accepted and self.mode == CttCanaryMode.ACTIVE
        report = CttCanaryReport(
            mode=self.mode,
            accepted=accepted,
            use_canonical_result=use_canonical,
            document_sha256=document_sha256,
            canonical_hash=draft.canonical_hash(),
            model=first.model,
            page_count=len(page_images),
            slot_count=len(draft.slots),
            occupied_count=len(occupied),
            review_count=len(reviews),
            provider_response_count=len(first.response_ids),
            first_cache_hit=first.cache_hit,
            replay_cache_hit=replay.cache_hit if replay is not None else None,
            replay_hash_matches=replay_hash_matches,
            incidents=incidents,
        )
        return CttCanaryExecution(report=report, draft=draft)
