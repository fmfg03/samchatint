"""Authority-free provider orchestration for canonical CTT field crops.

Providers only transcribe immutable crops.  Page, slot, crop identity,
normalization, domain validation and persistence remain owned by SamChat.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256_PATTERN = r"^[0-9a-f]{64}$"
PROVIDER_SCHEMA_VERSION = "ctt.provider_field.v1"
PROVIDER_CACHE_VERSION = "ctt.provider_cache.v1"


class CttOcrMode(str, Enum):
    OPENAI = "openai"
    CHANDRA_SHADOW = "chandra_shadow"
    CHANDRA_PRIMARY = "chandra_primary"


def ctt_ocr_mode(value: Optional[str]) -> CttOcrMode:
    """Parse an OCR mode and fail closed to the established provider."""
    raw = (value or CttOcrMode.OPENAI.value).strip().lower()
    try:
        return CttOcrMode(raw)
    except ValueError:
        return CttOcrMode.OPENAI


@dataclass(frozen=True)
class CttFieldCrop:
    """One immutable, deterministically assigned field crop."""

    crop_id: str
    document_sha256: str
    normalized_page_sha256: str
    crop_sha256: str
    field_name: str
    source_page: int
    source_region: str
    image_bytes: bytes
    slot: Optional[int] = None
    context_image_bytes: Optional[bytes] = None
    context_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("document_sha256", "normalized_page_sha256", "crop_sha256"):
            value = getattr(self, name)
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not self.crop_id.strip() or not self.field_name.strip():
            raise ValueError("crop_id and field_name are required")
        if self.source_page < 1 or self.source_page > 3:
            raise ValueError("source_page must be between 1 and 3")
        if self.slot is not None and (self.slot < 1 or self.slot > 25):
            raise ValueError("slot must be between 1 and 25")
        if not self.image_bytes:
            raise ValueError("image_bytes cannot be empty")
        if hashlib.sha256(self.image_bytes).hexdigest() != self.crop_sha256:
            raise ValueError("crop_sha256 does not bind image_bytes")
        if (self.context_image_bytes is None) != (self.context_sha256 is None):
            raise ValueError("context image and hash must be provided together")
        if self.context_image_bytes is not None and self.context_sha256 is not None:
            if not self.context_image_bytes:
                raise ValueError("context_image_bytes cannot be empty")
            if len(self.context_sha256) != 64 or any(
                ch not in "0123456789abcdef" for ch in self.context_sha256
            ):
                raise ValueError("context_sha256 must be a lowercase SHA-256 digest")
            if (
                hashlib.sha256(self.context_image_bytes).hexdigest()
                != self.context_sha256
            ):
                raise ValueError("context_sha256 does not bind context_image_bytes")


class CttProviderRawField(BaseModel):
    """Provider output.  Numeric confidence and structural authority are absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    crop_id: str = Field(min_length=1, max_length=240)
    crop_sha256: str = Field(pattern=SHA256_PATTERN)
    raw_text: Optional[str] = Field(default=None, max_length=500)
    candidates: List[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def normalize_empty_values(self) -> "CttProviderRawField":
        raw = self.raw_text.strip() if self.raw_text else None
        candidates = sorted({item.strip() for item in self.candidates if item.strip()})
        object.__setattr__(self, "raw_text", raw or None)
        object.__setattr__(self, "candidates", candidates)
        return self


class CttValidationDecision(BaseModel):
    """Deterministic validator decision applied after transcription."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_value: Optional[str] = Field(default=None, max_length=500)
    validation_codes: List[str] = Field(default_factory=list)
    accepted: bool


class CttCanonicalProviderField(BaseModel):
    """Canonical field receipt suitable for a draft, never for direct mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PROVIDER_SCHEMA_VERSION
    provider: str = Field(min_length=1, max_length=80)
    mode: CttOcrMode
    model_revision: str = Field(min_length=1, max_length=200)
    pipeline_version: str = Field(min_length=1, max_length=160)
    raw_text: Optional[str] = None
    normalized_value: Optional[str] = None
    source_page: int = Field(ge=1, le=3)
    source_region: str = Field(min_length=1, max_length=240)
    slot: Optional[int] = Field(default=None, ge=1, le=25)
    requires_review: bool
    validation_codes: List[str] = Field(default_factory=list)
    evidence_crop_hash: str = Field(pattern=SHA256_PATTERN)
    candidates: List[str] = Field(default_factory=list)


class CttFieldValidator(Protocol):
    def __call__(
        self, crop: CttFieldCrop, observation: CttProviderRawField
    ) -> CttValidationDecision: ...


class CttCropProvider(Protocol):
    name: str
    model_revision: str
    pipeline_version: str

    async def transcribe(
        self, crops: Sequence[CttFieldCrop]
    ) -> Mapping[str, CttProviderRawField]: ...


class CttProviderCacheIdentity(BaseModel):
    """All inputs able to change a field transcription."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_sha256: str = Field(pattern=SHA256_PATTERN)
    normalized_page_sha256: str = Field(pattern=SHA256_PATTERN)
    crop_id: str = Field(min_length=1, max_length=240)
    crop_sha256: str = Field(pattern=SHA256_PATTERN)
    context_sha256: str = Field(pattern=SHA256_PATTERN)
    provider: str
    mode: CttOcrMode
    model_revision: str
    pipeline_version: str
    schema_version: str = PROVIDER_SCHEMA_VERSION
    cache_version: str = PROVIDER_CACHE_VERSION

    def cache_key(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CttProviderFieldCache:
    """Private first-writer-wins cache for provider field receipts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, identity: CttProviderCacheIdentity) -> Path:
        key = identity.cache_key()
        return self.root / key[:2] / key[2:4] / f"{key}.json"

    def load(self, identity: CttProviderCacheIdentity) -> Optional[CttProviderRawField]:
        path = self._path(identity)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if payload.get("identity") != identity.model_dump(mode="json"):
            raise RuntimeError("CTT provider cache identity mismatch")
        return CttProviderRawField.model_validate(payload.get("observation"))

    def save(
        self,
        identity: CttProviderCacheIdentity,
        observation: CttProviderRawField,
    ) -> CttProviderRawField:
        if observation.crop_sha256 != identity.crop_sha256:
            raise RuntimeError("provider result is not bound to cache crop")
        path = self._path(identity)
        existing = self.load(identity)
        if existing is not None:
            if existing != observation:
                raise RuntimeError("identical cache identity produced different OCR")
            return existing
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(
            {
                "identity": identity.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".ctt-")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            try:
                os.link(temporary, path)
            except FileExistsError:
                return self.save(identity, observation)
            return observation
        finally:
            temporary.unlink(missing_ok=True)


class CttShadowComparison(BaseModel):
    """PII-free shadow evidence; field contents are intentionally excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    crop_id: str
    crop_sha256: str = Field(pattern=SHA256_PATTERN)
    exact_match: bool
    openai_present: bool
    chandra_present: bool
    openai_accepted: bool
    chandra_accepted: bool
    validation_codes: List[str] = Field(default_factory=list)


class CttProviderExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CttOcrMode
    fields: Dict[str, CttCanonicalProviderField]
    shadow: List[CttShadowComparison] = Field(default_factory=list)
    provider_calls: Dict[str, int] = Field(default_factory=dict)
    cache_hits: int = 0


def conservative_field_validator(
    crop: CttFieldCrop, observation: CttProviderRawField
) -> CttValidationDecision:
    """Default non-domain validator. Callers should add field-specific rules."""
    del crop
    codes: List[str] = []
    value = observation.raw_text.strip() if observation.raw_text else None
    if not value:
        codes.append("MISSING_VALUE")
    if observation.candidates:
        codes.append("AMBIGUOUS_TRANSCRIPTION")
    return CttValidationDecision(
        normalized_value=value,
        validation_codes=codes,
        accepted=not codes,
    )


class CttOcrCoordinator:
    """Run OCR modes while keeping providers outside the authority boundary."""

    def __init__(
        self,
        *,
        openai: CttCropProvider,
        chandra: Optional[CttCropProvider],
        cache: CttProviderFieldCache,
        validator: CttFieldValidator = conservative_field_validator,
    ) -> None:
        self.openai = openai
        self.chandra = chandra
        self.cache = cache
        self.validator = validator

    def _identity(
        self, crop: CttFieldCrop, provider: CttCropProvider, mode: CttOcrMode
    ) -> CttProviderCacheIdentity:
        return CttProviderCacheIdentity(
            document_sha256=crop.document_sha256,
            normalized_page_sha256=crop.normalized_page_sha256,
            crop_id=crop.crop_id,
            crop_sha256=crop.crop_sha256,
            context_sha256=crop.context_sha256 or crop.crop_sha256,
            provider=provider.name,
            mode=mode,
            model_revision=provider.model_revision,
            pipeline_version=provider.pipeline_version,
        )

    async def _read(
        self,
        provider: CttCropProvider,
        crops: Sequence[CttFieldCrop],
        mode: CttOcrMode,
    ) -> Tuple[Dict[str, CttProviderRawField], int, int]:
        output: Dict[str, CttProviderRawField] = {}
        missing: List[CttFieldCrop] = []
        hits = 0
        for crop in crops:
            cached = self.cache.load(self._identity(crop, provider, mode))
            if cached is None:
                missing.append(crop)
            else:
                output[crop.crop_id] = cached
                hits += 1
        calls = 0
        if missing:
            calls = 1
            received = dict(await provider.transcribe(missing))
            expected = {crop.crop_id for crop in missing}
            if set(received) != expected:
                raise RuntimeError(
                    "provider must return exactly the requested crop ids"
                )
            for crop in missing:
                item = received[crop.crop_id]
                if item.crop_id != crop.crop_id or item.crop_sha256 != crop.crop_sha256:
                    raise RuntimeError(
                        "provider response changed immutable crop identity"
                    )
                output[crop.crop_id] = self.cache.save(
                    self._identity(crop, provider, mode), item
                )
        return output, calls, hits

    def _canonical(
        self,
        crop: CttFieldCrop,
        raw: CttProviderRawField,
        provider: CttCropProvider,
        mode: CttOcrMode,
        *,
        extra_codes: Sequence[str] = (),
        force_review: bool = False,
    ) -> CttCanonicalProviderField:
        decision = self.validator(crop, raw)
        codes = sorted(set(decision.validation_codes) | set(extra_codes))
        return CttCanonicalProviderField(
            provider=provider.name,
            mode=mode,
            model_revision=provider.model_revision,
            pipeline_version=provider.pipeline_version,
            raw_text=raw.raw_text,
            normalized_value=decision.normalized_value,
            source_page=crop.source_page,
            source_region=crop.source_region,
            slot=crop.slot,
            requires_review=force_review or not decision.accepted or bool(codes),
            validation_codes=codes,
            evidence_crop_hash=crop.crop_sha256,
            candidates=raw.candidates,
        )

    async def extract(
        self, crops: Sequence[CttFieldCrop], *, mode: CttOcrMode
    ) -> CttProviderExecution:
        ids = [crop.crop_id for crop in crops]
        if len(ids) != len(set(ids)):
            raise ValueError("crop ids must be unique")
        if mode != CttOcrMode.OPENAI and self.chandra is None:
            raise RuntimeError("Chandra mode requires a configured Chandra provider")
        if (
            mode == CttOcrMode.CHANDRA_PRIMARY
            and self.chandra is not None
            and not bool(getattr(self.chandra, "revision_pinned", True))
        ):
            raise RuntimeError(
                "chandra_primary requires a contractually pinned model revision"
            )

        if mode == CttOcrMode.OPENAI:
            raw, calls, hits = await self._read(self.openai, crops, mode)
            return CttProviderExecution(
                mode=mode,
                fields={
                    crop.crop_id: self._canonical(
                        crop, raw[crop.crop_id], self.openai, mode
                    )
                    for crop in crops
                },
                provider_calls={self.openai.name: calls},
                cache_hits=hits,
            )

        assert self.chandra is not None
        chandra_raw, chandra_calls, chandra_hits = await self._read(
            self.chandra, crops, mode
        )
        if mode == CttOcrMode.CHANDRA_SHADOW:
            openai_raw, openai_calls, openai_hits = await self._read(
                self.openai, crops, mode
            )
            shadow: List[CttShadowComparison] = []
            for crop in crops:
                left = openai_raw[crop.crop_id]
                right = chandra_raw[crop.crop_id]
                left_decision = self.validator(crop, left)
                right_decision = self.validator(crop, right)
                shadow.append(
                    CttShadowComparison(
                        crop_id=crop.crop_id,
                        crop_sha256=crop.crop_sha256,
                        exact_match=(
                            left_decision.normalized_value
                            == right_decision.normalized_value
                        ),
                        openai_present=bool(left.raw_text),
                        chandra_present=bool(right.raw_text),
                        openai_accepted=left_decision.accepted,
                        chandra_accepted=right_decision.accepted,
                        validation_codes=sorted(
                            set(left_decision.validation_codes)
                            | set(right_decision.validation_codes)
                        ),
                    )
                )
            return CttProviderExecution(
                mode=mode,
                fields={
                    crop.crop_id: self._canonical(
                        crop, openai_raw[crop.crop_id], self.openai, mode
                    )
                    for crop in crops
                },
                shadow=shadow,
                provider_calls={
                    self.openai.name: openai_calls,
                    self.chandra.name: chandra_calls,
                },
                cache_hits=chandra_hits + openai_hits,
            )

        rejected = [
            crop
            for crop in crops
            if not self.validator(crop, chandra_raw[crop.crop_id]).accepted
        ]
        fallback_raw: Dict[str, CttProviderRawField] = {}
        openai_calls = openai_hits = 0
        if rejected:
            fallback_raw, openai_calls, openai_hits = await self._read(
                self.openai, rejected, mode
            )

        fields: Dict[str, CttCanonicalProviderField] = {}
        for crop in crops:
            chandra_item = chandra_raw[crop.crop_id]
            chandra_decision = self.validator(crop, chandra_item)
            if chandra_decision.accepted:
                fields[crop.crop_id] = self._canonical(
                    crop, chandra_item, self.chandra, mode
                )
                continue
            fallback = fallback_raw[crop.crop_id]
            fallback_decision = self.validator(crop, fallback)
            disagreement = bool(chandra_item.raw_text) and (
                chandra_decision.normalized_value != fallback_decision.normalized_value
            )
            if fallback_decision.accepted:
                fields[crop.crop_id] = self._canonical(
                    crop,
                    fallback,
                    self.openai,
                    mode,
                    extra_codes=("PROVIDER_DISAGREEMENT",) if disagreement else (),
                    force_review=disagreement,
                )
            else:
                combined = CttProviderRawField(
                    crop_id=crop.crop_id,
                    crop_sha256=crop.crop_sha256,
                    raw_text=chandra_item.raw_text or fallback.raw_text,
                    candidates=sorted(
                        set(chandra_item.candidates)
                        | set(fallback.candidates)
                        | ({fallback.raw_text} if fallback.raw_text else set())
                    ),
                )
                fields[crop.crop_id] = self._canonical(
                    crop,
                    combined,
                    self.chandra,
                    mode,
                    extra_codes=("FALLBACK_UNRESOLVED",),
                    force_review=True,
                )
        return CttProviderExecution(
            mode=mode,
            fields=fields,
            provider_calls={
                self.chandra.name: chandra_calls,
                self.openai.name: openai_calls,
            },
            cache_hits=chandra_hits + openai_hits,
        )
