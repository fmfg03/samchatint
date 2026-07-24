"""Bounded client for an isolated Chandra CTT transcription service."""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .ctt_ocr_provider import CttFieldCrop, CttProviderRawField


class ChandraServiceError(RuntimeError):
    pass


class ChandraCircuitOpen(ChandraServiceError):
    pass


class ChandraHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    model_revision: str = Field(min_length=1, max_length=200)
    pipeline_version: str = Field(min_length=1, max_length=160)
    database_access: bool = False


@dataclass(frozen=True)
class ChandraClientSettings:
    base_url: str
    expected_model_revision: str
    expected_pipeline_version: str = "chandra.ctt.crop.v1"
    timeout_seconds: float = 45.0
    max_concurrency: int = 2
    max_image_bytes: int = 4 * 1024 * 1024
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Chandra base_url must use HTTP(S)")
        if not self.expected_model_revision.strip():
            raise ValueError("expected_model_revision is mandatory")
        if self.timeout_seconds <= 0 or self.max_concurrency < 1:
            raise ValueError("invalid Chandra timeout or concurrency")
        if self.max_image_bytes < 1024:
            raise ValueError("max_image_bytes is too small")
        if self.failure_threshold < 1 or self.cooldown_seconds <= 0:
            raise ValueError("invalid circuit breaker settings")


class ChandraCttClient:
    """Crop provider with timeout, concurrency, revision and circuit gates."""

    name = "chandra"

    def __init__(
        self,
        settings: ChandraClientSettings,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings
        self.model_revision = settings.expected_model_revision
        self.pipeline_version = settings.expected_pipeline_version
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.timeout_seconds),
        )
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None
        self._health_verified = False

    def _before_call(self) -> None:
        if self._opened_at is None:
            return
        if time.monotonic() - self._opened_at < self.settings.cooldown_seconds:
            raise ChandraCircuitOpen("Chandra circuit breaker is open")
        self._opened_at = None
        self._consecutive_failures = 0
        self._health_verified = False

    def _success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def _failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.settings.failure_threshold:
            self._opened_at = time.monotonic()

    async def healthcheck(self) -> ChandraHealth:
        self._before_call()
        try:
            async with self._semaphore:
                response = await self._client.get("/healthz")
            response.raise_for_status()
            health = ChandraHealth.model_validate(response.json())
            if health.status != "ready":
                raise ChandraServiceError("Chandra worker is not ready")
            if health.database_access:
                raise ChandraServiceError("Chandra worker reports database capability")
            if health.model_revision != self.model_revision:
                raise ChandraServiceError("Chandra model revision mismatch")
            if health.pipeline_version != self.pipeline_version:
                raise ChandraServiceError("Chandra pipeline version mismatch")
            self._health_verified = True
            self._success()
            return health
        except ChandraServiceError:
            self._failure()
            raise
        except Exception as exc:
            self._failure()
            raise ChandraServiceError("Chandra healthcheck failed") from exc

    async def _transcribe_one(self, crop: CttFieldCrop) -> CttProviderRawField:
        image_bytes = crop.context_image_bytes or crop.image_bytes
        context_sha256 = crop.context_sha256 or crop.crop_sha256
        if len(image_bytes) > self.settings.max_image_bytes:
            raise ChandraServiceError("crop exceeds Chandra image byte limit")
        payload = {
            "crop_id": crop.crop_id,
            "crop_sha256": crop.crop_sha256,
            "context_sha256": context_sha256,
            "field_name": crop.field_name,
            "source_page": crop.source_page,
            "source_region": crop.source_region,
            "slot": crop.slot,
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "model_revision": self.model_revision,
            "pipeline_version": self.pipeline_version,
        }
        async with self._semaphore:
            response = await self._client.post("/v1/ctt/transcribe", json=payload)
        response.raise_for_status()
        body = response.json()
        if body.get("model_revision") != self.model_revision:
            raise ChandraServiceError("transcription model revision mismatch")
        if body.get("pipeline_version") != self.pipeline_version:
            raise ChandraServiceError("transcription pipeline version mismatch")
        return CttProviderRawField.model_validate(
            {
                "crop_id": body.get("crop_id"),
                "crop_sha256": body.get("crop_sha256"),
                "raw_text": body.get("raw_text"),
                "candidates": body.get("candidates") or [],
            }
        )

    async def transcribe(
        self, crops: Sequence[CttFieldCrop]
    ) -> Mapping[str, CttProviderRawField]:
        self._before_call()
        if not self._health_verified:
            await self.healthcheck()
        try:
            results = await asyncio.gather(
                *(self._transcribe_one(crop) for crop in crops)
            )
            output: Dict[str, CttProviderRawField] = {
                item.crop_id: item for item in results
            }
            if len(output) != len(crops):
                raise ChandraServiceError("Chandra returned duplicate crop ids")
            self._success()
            return output
        except ChandraServiceError:
            self._failure()
            raise
        except Exception as exc:
            self._failure()
            raise ChandraServiceError("Chandra transcription failed") from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
