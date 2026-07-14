"""Deterministic canonical contract for CTT registration OCR drafts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "ctt.registration_draft.v2"
LOW_CONFIDENCE_THRESHOLD = 0.80
SHA256_PATTERN = r"^[0-9a-f]{64}$"
CROP_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"
CURP_PATTERN = re.compile(r"^[A-Z][AEIOUX][A-Z]{2}[0-9]{6}[HM][A-Z]{5}[A-Z0-9][0-9]$")


class CttFieldName(str, Enum):
    TEAM_NAME = "team_name"
    CATEGORY = "category"
    GENDER = "gender"
    LEAGUE = "league"
    REPRESENTATIVE_NAME = "representative_name"
    EMAIL = "email"
    STATE = "state"
    MUNICIPALITY = "municipality"
    GIVEN_NAMES = "given_names"
    PATERNAL_SURNAME = "paternal_surname"
    MATERNAL_SURNAME = "maternal_surname"
    BIRTH_DATE = "birth_date"
    CURP = "curp"


class CttValidationCode(str, Enum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INVALID_DATE_FORMAT = "INVALID_DATE_FORMAT"
    INVALID_CURP_FORMAT = "INVALID_CURP_FORMAT"
    FIELD_CONFLICT_REQUIRES_REVIEW = "FIELD_CONFLICT_REQUIRES_REVIEW"
    SLOT_INCOMPLETE = "SLOT_INCOMPLETE"
    TEAM_NAME_REQUIRED = "TEAM_NAME_REQUIRED"


class CttSlotStatus(str, Enum):
    EMPTY = "empty"
    PRESENT = "present"
    REQUIRES_REVIEW = "requires_review"


def _unique(values: Sequence[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_birth_date(value: str) -> Optional[str]:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    match = re.fullmatch(r"([0-9]{2})/([0-9]{2})/([0-9]{2}|[0-9]{4})", value)
    if not match:
        return None
    day, month, year_text = match.groups()
    if len(year_text) == 2:
        year_number = int(year_text)
        year = 2000 + year_number if year_number <= 30 else 1900 + year_number
    else:
        year = int(year_text)
    try:
        parsed = datetime(year, int(month), int(day)).date()
    except ValueError:
        return None
    return parsed.isoformat()


def _normalize_field_value(
    field_name: CttFieldName,
    raw_text: Optional[str],
) -> Tuple[Optional[str], List[CttValidationCode]]:
    if raw_text is None or not raw_text.strip():
        return None, []

    text = _normalize_text(raw_text)
    if field_name == CttFieldName.BIRTH_DATE:
        normalized_date = _normalize_birth_date(text)
        if normalized_date is None:
            return None, [CttValidationCode.INVALID_DATE_FORMAT]
        return normalized_date, []
    if field_name == CttFieldName.CURP:
        curp = re.sub(r"\s+", "", text).upper()
        if not CURP_PATTERN.fullmatch(curp):
            return None, [CttValidationCode.INVALID_CURP_FORMAT]
        return curp, []
    if field_name == CttFieldName.EMAIL:
        return text.lower(), []
    return text, []


def _field_has_content(field: "CttFieldObservation") -> bool:
    return bool(field.raw_text and field.raw_text.strip()) or bool(
        field.normalized_value
    )


class CttFieldEvidence(BaseModel):
    """Stable reference to the visual crop supporting one field observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(ge=1, le=2)
    slot: Optional[int] = Field(default=None, ge=1, le=20)
    crop_id: str = Field(min_length=1, max_length=160, pattern=CROP_ID_PATTERN)
    crop_sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_page_slot_pair(self) -> "CttFieldEvidence":
        if self.slot is None:
            if self.page != 1:
                raise ValueError("header evidence must come from page 1")
            return self
        expected_page = 1 if self.slot <= 8 else 2
        if self.page != expected_page:
            raise ValueError(f"slot {self.slot} must use page {expected_page} evidence")
        return self


class CttFieldObservation(BaseModel):
    """Raw OCR evidence plus a deterministic canonical value."""

    model_config = ConfigDict(extra="forbid")

    field_name: CttFieldName
    raw_text: Optional[str] = None
    normalized_value: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    requires_review: bool = False
    evidence: CttFieldEvidence
    validation_codes: List[CttValidationCode] = Field(default_factory=list)
    candidates: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def derive_canonical_state(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        field_name = CttFieldName(data["field_name"])
        raw_text = data.get("raw_text")
        normalized_value, deterministic_codes = _normalize_field_value(
            field_name,
            raw_text,
        )

        candidate_values: List[str] = []
        for candidate in data.get("candidates") or []:
            normalized_candidate, candidate_codes = _normalize_field_value(
                field_name,
                str(candidate),
            )
            deterministic_codes.extend(candidate_codes)
            if normalized_candidate:
                candidate_values.append(normalized_candidate)
        if normalized_value:
            candidate_values.insert(0, normalized_value)
        candidate_values = _unique(candidate_values)

        codes = list(deterministic_codes)
        has_content = bool(raw_text and str(raw_text).strip()) or bool(candidate_values)
        if (
            has_content
            and float(data.get("confidence", 0.0)) < LOW_CONFIDENCE_THRESHOLD
        ):
            codes.append(CttValidationCode.LOW_CONFIDENCE)
        if len(candidate_values) > 1:
            codes.append(CttValidationCode.FIELD_CONFLICT_REQUIRES_REVIEW)
            normalized_value = None

        data["normalized_value"] = normalized_value
        data["candidates"] = candidate_values
        data["validation_codes"] = _unique([code.value for code in codes])
        data["requires_review"] = bool(data["validation_codes"])
        return data


class CttPlayerFields(BaseModel):
    """Fixed player field set used by the structured OCR response."""

    model_config = ConfigDict(extra="forbid")

    given_names: CttFieldObservation
    paternal_surname: CttFieldObservation
    maternal_surname: CttFieldObservation
    birth_date: CttFieldObservation
    curp: CttFieldObservation

    @model_validator(mode="after")
    def validate_field_names(self) -> "CttPlayerFields":
        expected = {
            "given_names": CttFieldName.GIVEN_NAMES,
            "paternal_surname": CttFieldName.PATERNAL_SURNAME,
            "maternal_surname": CttFieldName.MATERNAL_SURNAME,
            "birth_date": CttFieldName.BIRTH_DATE,
            "curp": CttFieldName.CURP,
        }
        for attribute, field_name in expected.items():
            if getattr(self, attribute).field_name != field_name:
                raise ValueError(f"{attribute} must contain {field_name.value}")
        return self

    def observations(self) -> List[CttFieldObservation]:
        return [
            self.given_names,
            self.paternal_surname,
            self.maternal_surname,
            self.birth_date,
            self.curp,
        ]


class CttSlotDraft(BaseModel):
    """Canonical state for one template player slot."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1, le=2)
    slot: int = Field(ge=1, le=20)
    fields: CttPlayerFields
    status: CttSlotStatus = CttSlotStatus.EMPTY
    requires_review: bool = False
    validation_codes: List[CttValidationCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_slot_state(self) -> "CttSlotDraft":
        expected_page = 1 if self.slot <= 8 else 2
        if self.page != expected_page:
            raise ValueError(f"slot {self.slot} belongs to page {expected_page}")

        observations = self.fields.observations()
        for observation in observations:
            if observation.evidence.page != self.page:
                raise ValueError("field evidence page does not match slot page")
            if observation.evidence.slot != self.slot:
                raise ValueError("field evidence slot does not match slot number")

        has_content = any(_field_has_content(field) for field in observations)
        codes: List[CttValidationCode] = []
        if not has_content:
            self.status = CttSlotStatus.EMPTY
            self.requires_review = False
            self.validation_codes = []
            return self

        required_fields = (
            self.fields.given_names,
            self.fields.paternal_surname,
            self.fields.birth_date,
        )
        if any(field.normalized_value is None for field in required_fields):
            codes.append(CttValidationCode.SLOT_INCOMPLETE)
        self.validation_codes = [
            CttValidationCode(code) for code in _unique([code.value for code in codes])
        ]
        self.requires_review = bool(
            self.validation_codes
            or any(field.requires_review for field in observations)
        )
        self.status = (
            CttSlotStatus.REQUIRES_REVIEW
            if self.requires_review
            else CttSlotStatus.PRESENT
        )
        return self


class CttTeamFields(BaseModel):
    """Canonical header fields from the first page."""

    model_config = ConfigDict(extra="forbid")

    name: CttFieldObservation
    category: CttFieldObservation
    gender: CttFieldObservation
    league: CttFieldObservation
    representative_name: CttFieldObservation
    email: CttFieldObservation
    state: CttFieldObservation
    municipality: CttFieldObservation

    @model_validator(mode="after")
    def validate_field_names_and_evidence(self) -> "CttTeamFields":
        expected = {
            "name": CttFieldName.TEAM_NAME,
            "category": CttFieldName.CATEGORY,
            "gender": CttFieldName.GENDER,
            "league": CttFieldName.LEAGUE,
            "representative_name": CttFieldName.REPRESENTATIVE_NAME,
            "email": CttFieldName.EMAIL,
            "state": CttFieldName.STATE,
            "municipality": CttFieldName.MUNICIPALITY,
        }
        for attribute, field_name in expected.items():
            observation = getattr(self, attribute)
            if observation.field_name != field_name:
                raise ValueError(f"{attribute} must contain {field_name.value}")
            if observation.evidence.page != 1 or observation.evidence.slot is not None:
                raise ValueError("team fields require page 1 header evidence")
        return self

    def observations(self) -> List[CttFieldObservation]:
        return [
            self.name,
            self.category,
            self.gender,
            self.league,
            self.representative_name,
            self.email,
            self.state,
            self.municipality,
        ]


class CttTeamDraft(BaseModel):
    """Canonical registration header and its review state."""

    model_config = ConfigDict(extra="forbid")

    fields: CttTeamFields
    requires_review: bool = False
    validation_codes: List[CttValidationCode] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_team_state(self) -> "CttTeamDraft":
        codes: List[CttValidationCode] = []
        if not self.fields.name.normalized_value:
            codes.append(CttValidationCode.TEAM_NAME_REQUIRED)
        self.validation_codes = [
            CttValidationCode(code) for code in _unique([code.value for code in codes])
        ]
        self.requires_review = bool(
            self.validation_codes
            or any(field.requires_review for field in self.fields.observations())
        )
        return self


class CttRegistrationDraft(BaseModel):
    """Versioned, deterministic draft produced before any database commit."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["ctt.registration_draft.v2"] = SCHEMA_VERSION
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    team: CttTeamDraft
    slots: List[CttSlotDraft]
    requires_review: bool = False

    @model_validator(mode="after")
    def validate_and_sort_slots(self) -> "CttRegistrationDraft":
        keys = [(slot.page, slot.slot) for slot in self.slots]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate page/slot entries are not allowed")
        if {slot.slot for slot in self.slots} != set(range(1, 21)):
            raise ValueError("draft must materialize every slot from 1 through 20")
        self.slots = sorted(self.slots, key=lambda slot: (slot.page, slot.slot))
        self.requires_review = bool(
            self.team.requires_review
            or any(slot.requires_review for slot in self.slots)
        )
        return self

    def canonical_payload(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=False)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()
