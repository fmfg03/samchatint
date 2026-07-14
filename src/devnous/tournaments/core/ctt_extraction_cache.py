"""Deterministic cache and multi-attempt reconciliation for CTT OCR drafts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Literal, Mapping, Optional, Sequence, Tuple

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .ctt_ocr_contract import (
    SCHEMA_VERSION,
    SHA256_PATTERN,
    CttFieldName,
    CttFieldObservation,
    CttPlayerFields,
    CttRegistrationDraft,
    CttSlotDraft,
    CttTeamDraft,
    CttTeamFields,
)
from .ctt_responses_extractor import (
    CTT_RESPONSES_PIPELINE_VERSION,
    CttResponsesExtractionResult,
    CttResponsesExtractor,
)

CACHE_VERSION: Literal["ctt.draft_cache.v1"] = "ctt.draft_cache.v1"
PRESENCE_CONFLICT_CONFIDENCE = 0.79
TEAM_FIELD_ATTRIBUTES = (
    "name",
    "category",
    "gender",
    "league",
    "representative_name",
    "email",
    "state",
    "municipality",
)
PLAYER_FIELD_ATTRIBUTES = (
    "given_names",
    "paternal_surname",
    "maternal_surname",
    "birth_date",
    "curp",
)


class CttDraftCacheError(RuntimeError):
    """Base class for fail-closed CTT draft cache errors."""


class CttDraftCacheCorruption(CttDraftCacheError):
    """A cache entry exists but cannot be trusted."""


class CttDraftCacheCollision(CttDraftCacheError):
    """The same extraction fingerprint produced a different canonical draft."""


class CttReconciliationError(RuntimeError):
    """Extraction attempts cannot be reconciled safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CttExtractionFingerprint(BaseModel):
    """Inputs that completely identify one deterministic extraction policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_sha256: str = Field(pattern=SHA256_PATTERN)
    model: str = Field(min_length=1, max_length=160)
    cache_version: Literal["ctt.draft_cache.v1"] = CACHE_VERSION
    schema_version: Literal["ctt.registration_draft.v3"] = SCHEMA_VERSION
    pipeline_version: str = Field(min_length=1, max_length=160)
    layout_sha256: str = Field(pattern=SHA256_PATTERN)
    attempts: int = Field(ge=1, le=3)

    @classmethod
    def from_inputs(
        cls,
        *,
        document_sha256: str,
        model: str,
        layout: Mapping[str, Any],
        attempts: int,
        pipeline_version: str = CTT_RESPONSES_PIPELINE_VERSION,
    ) -> "CttExtractionFingerprint":
        """Build a fingerprint from all inputs that can change a draft."""
        return cls(
            document_sha256=document_sha256,
            model=model.strip(),
            pipeline_version=pipeline_version.strip(),
            layout_sha256=_sha256_text(_canonical_json(layout)),
            attempts=attempts,
        )

    def canonical_payload(self) -> str:
        """Return the stable serialization used to address cache entries."""
        return _canonical_json(self.model_dump(mode="json"))

    def cache_key(self) -> str:
        """Return the content-addressed key for this extraction request."""
        return _sha256_text(self.canonical_payload())


class _CttDraftCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_version: Literal["ctt.draft_cache.v1"] = CACHE_VERSION
    fingerprint: CttExtractionFingerprint
    canonical_hash: str = Field(pattern=SHA256_PATTERN)
    draft: CttRegistrationDraft


class CttDraftCache:
    """File-backed, content-addressed cache with first-writer-wins semantics."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, fingerprint: CttExtractionFingerprint) -> Path:
        """Return the sharded path for a fingerprint without creating it."""
        key = fingerprint.cache_key()
        return self.root / key[:2] / key[2:4] / f"{key}.json"

    def load(
        self, fingerprint: CttExtractionFingerprint
    ) -> Optional[CttRegistrationDraft]:
        """Load and validate a cached draft, returning ``None`` only if absent."""
        path = self.path_for(fingerprint)
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CttDraftCacheCorruption(f"cannot read cache entry {path}") from exc

        try:
            entry = _CttDraftCacheEntry.model_validate_json(payload)
        except (TypeError, ValueError) as exc:
            raise CttDraftCacheCorruption(f"invalid cache entry {path}") from exc
        if entry.fingerprint != fingerprint:
            raise CttDraftCacheCorruption(f"fingerprint mismatch in {path}")
        if entry.draft.document_sha256 != fingerprint.document_sha256:
            raise CttDraftCacheCorruption(f"document hash mismatch in {path}")
        if entry.draft.canonical_hash() != entry.canonical_hash:
            raise CttDraftCacheCorruption(f"canonical hash mismatch in {path}")
        return entry.draft

    def _existing_or_collision(
        self,
        fingerprint: CttExtractionFingerprint,
        draft: CttRegistrationDraft,
    ) -> CttRegistrationDraft:
        existing = self.load(fingerprint)
        if existing is None:
            raise CttDraftCacheCorruption("cache entry disappeared during save")
        if existing.canonical_hash() != draft.canonical_hash():
            raise CttDraftCacheCollision(
                "identical extraction fingerprint produced a different draft"
            )
        return existing

    def save(
        self,
        fingerprint: CttExtractionFingerprint,
        draft: CttRegistrationDraft,
    ) -> CttRegistrationDraft:
        """Atomically store a draft without overwriting an existing result."""
        if draft.document_sha256 != fingerprint.document_sha256:
            raise CttDraftCacheCollision(
                "draft document hash does not match extraction fingerprint"
            )

        path = self.path_for(fingerprint)
        if path.exists():
            return self._existing_or_collision(fingerprint, draft)

        key = fingerprint.cache_key()
        private_directories = (
            self.root,
            self.root / key[:2],
            self.root / key[:2] / key[2:4],
        )
        for directory in private_directories:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                directory.chmod(0o700)
            except OSError:
                pass

        entry = _CttDraftCacheEntry(
            fingerprint=fingerprint,
            canonical_hash=draft.canonical_hash(),
            draft=draft,
        )
        payload = _canonical_json(entry.model_dump(mode="json")).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{fingerprint.cache_key()}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.chmod(0o600)
            try:
                os.link(str(temporary_path), str(path))
            except FileExistsError:
                return self._existing_or_collision(fingerprint, draft)
            return draft
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _normalized_sort_key(value: str) -> Tuple[str, str]:
    normalized = unicodedata.normalize("NFC", value)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold(), normalized


def _has_content(observation: CttFieldObservation) -> bool:
    return bool(observation.raw_text and observation.raw_text.strip()) or bool(
        observation.candidates
    )


def _canonical_variants(observation: CttFieldObservation) -> List[str]:
    if observation.candidates:
        return list(observation.candidates)
    if observation.normalized_value:
        return [observation.normalized_value]
    if observation.raw_text and observation.raw_text.strip():
        return [unicodedata.normalize("NFC", observation.raw_text).strip()]
    return []


def _candidate_input(field_name: CttFieldName, value: str) -> str:
    if field_name == CttFieldName.BIRTH_DATE:
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return value
    return value


def _reconcile_observation(
    observations: Sequence[CttFieldObservation],
) -> CttFieldObservation:
    if not observations:
        raise CttReconciliationError("cannot reconcile an empty observation set")
    field_name = observations[0].field_name
    evidence = observations[0].evidence
    if any(observation.field_name != field_name for observation in observations):
        raise CttReconciliationError("field names differ between extraction attempts")
    if any(observation.evidence != evidence for observation in observations):
        raise CttReconciliationError("field evidence differs between attempts")

    content = [observation for observation in observations if _has_content(observation)]
    if not content:
        return CttFieldObservation(
            field_name=field_name,
            raw_text=None,
            confidence=min(observation.confidence for observation in observations),
            candidates=[],
            evidence=evidence,
        )

    raw_values = sorted(
        {
            observation.raw_text.strip()
            for observation in content
            if observation.raw_text and observation.raw_text.strip()
        },
        key=_normalized_sort_key,
    )
    primary = sorted(
        content,
        key=lambda observation: (
            -observation.confidence,
            _normalized_sort_key(observation.raw_text or ""),
        ),
    )[0]
    raw_text = primary.raw_text.strip() if primary.raw_text else None
    if raw_text is None and raw_values:
        raw_text = raw_values[0]

    variants = sorted(
        {
            variant
            for observation in content
            for variant in _canonical_variants(observation)
        },
        key=_normalized_sort_key,
    )
    confidence = min(observation.confidence for observation in content)
    if len(content) != len(observations):
        confidence = min(confidence, PRESENCE_CONFLICT_CONFIDENCE)

    candidates: List[str] = []
    if len(variants) > 1:
        candidates = [_candidate_input(field_name, value) for value in variants]
        candidates.extend(raw_values)

    return CttFieldObservation(
        field_name=field_name,
        raw_text=raw_text,
        confidence=confidence,
        candidates=candidates,
        evidence=evidence,
    )


def reconcile_ctt_drafts(
    drafts: Sequence[CttRegistrationDraft],
) -> CttRegistrationDraft:
    """Reconcile attempts deterministically, surfacing every disagreement."""
    if not drafts:
        raise CttReconciliationError("at least one CTT draft is required")
    document_sha256 = drafts[0].document_sha256
    if any(draft.document_sha256 != document_sha256 for draft in drafts):
        raise CttReconciliationError("document hashes differ between attempts")

    ordered = sorted(drafts, key=lambda draft: draft.canonical_payload())
    team_values = {
        attribute: _reconcile_observation(
            [getattr(draft.team.fields, attribute) for draft in ordered]
        )
        for attribute in TEAM_FIELD_ATTRIBUTES
    }
    team = CttTeamDraft(fields=CttTeamFields(**team_values))

    slot_numbers = tuple(slot.slot for slot in ordered[0].slots)
    if any(
        tuple(slot.slot for slot in draft.slots) != slot_numbers for draft in ordered
    ):
        raise CttReconciliationError("draft slot sets differ between attempts")

    slots: List[CttSlotDraft] = []
    for index, slot_number in enumerate(slot_numbers):
        source_slots = [draft.slots[index] for draft in ordered]
        field_values = {
            attribute: _reconcile_observation(
                [getattr(slot.fields, attribute) for slot in source_slots]
            )
            for attribute in PLAYER_FIELD_ATTRIBUTES
        }
        slots.append(
            CttSlotDraft(
                page=source_slots[0].page,
                slot=slot_number,
                occupied=any(slot.occupied for slot in source_slots),
                fields=CttPlayerFields(**field_values),
            )
        )

    return CttRegistrationDraft(
        document_sha256=document_sha256,
        team=team,
        slots=slots,
    )


@dataclass(frozen=True)
class CttCachedExtractionResult:
    """Canonical result plus cache and provider audit metadata."""

    draft: CttRegistrationDraft
    model: str
    response_ids: Tuple[str, ...]
    cache_hit: bool
    cache_key: str
    attempts: int


class CttCachedResponsesExtractor:
    """Add deterministic caching and bounded reconciliation to an extractor."""

    def __init__(
        self,
        extractor: CttResponsesExtractor,
        cache: CttDraftCache,
        *,
        attempts: int = 1,
    ) -> None:
        if attempts < 1 or attempts > 3:
            raise ValueError("attempts must be between 1 and 3")
        self.extractor = extractor
        self.cache = cache
        self.attempts = attempts

    async def extract(
        self,
        page_images: Sequence[Image.Image],
        layout: Mapping[str, Any],
        *,
        document_sha256: str,
    ) -> CttCachedExtractionResult:
        """Return a cached draft or perform and reconcile bounded attempts."""
        fingerprint = CttExtractionFingerprint.from_inputs(
            document_sha256=document_sha256,
            model=self.extractor.model,
            layout=layout,
            attempts=self.attempts,
        )
        cached = self.cache.load(fingerprint)
        if cached is not None:
            return CttCachedExtractionResult(
                draft=cached,
                model=fingerprint.model,
                response_ids=(),
                cache_hit=True,
                cache_key=fingerprint.cache_key(),
                attempts=self.attempts,
            )

        results: List[CttResponsesExtractionResult] = []
        for _attempt in range(self.attempts):
            result = await self.extractor.extract(
                page_images,
                layout,
                document_sha256=document_sha256,
            )
            if result.model != fingerprint.model:
                raise CttReconciliationError(
                    "extractor result model differs from extraction fingerprint"
                )
            results.append(result)

        draft = (
            results[0].draft
            if len(results) == 1
            else reconcile_ctt_drafts([result.draft for result in results])
        )
        stored = self.cache.save(fingerprint, draft)
        return CttCachedExtractionResult(
            draft=stored,
            model=fingerprint.model,
            response_ids=tuple(
                response_id for result in results for response_id in result.response_ids
            ),
            cache_hit=False,
            cache_key=fingerprint.cache_key(),
            attempts=self.attempts,
        )
