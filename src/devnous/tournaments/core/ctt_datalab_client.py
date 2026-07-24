"""Datalab Balanced adapter for Chandra shadow/bake-off crop transcription.

The public API does not offer model pinning outside enterprise plans.  This
adapter therefore reports ``revision_pinned = False`` unless an explicit
contract revision is configured; the coordinator refuses unpinned primary use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import httpx

from .ctt_ocr_provider import CttFieldCrop, CttProviderRawField


class DatalabChandraError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatalabChandraSettings:
    api_key: str
    base_url: str = "https://www.datalab.to"
    mode: str = "balanced"
    contract_model_revision: Optional[str] = None
    timeout_seconds: float = 30.0
    poll_interval_seconds: float = 1.0
    max_poll_seconds: float = 120.0
    max_concurrency: int = 2
    max_submit_attempts: int = 5
    submit_backoff_seconds: float = 2.0
    max_image_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("Datalab API key is required")
        if self.mode not in {"balanced", "accurate", "fast"}:
            raise ValueError("unsupported Datalab mode")
        if self.timeout_seconds <= 0 or self.max_poll_seconds <= 0:
            raise ValueError("Datalab timeouts must be positive")
        if self.poll_interval_seconds <= 0 or self.max_concurrency < 1:
            raise ValueError("invalid polling or concurrency")
        if self.max_submit_attempts < 1 or self.submit_backoff_seconds <= 0:
            raise ValueError("invalid Datalab submit retry policy")


class DatalabChandraClient:
    name = "chandra_datalab"
    pipeline_version = "datalab.extract.literal_field.v3"

    def __init__(
        self,
        settings: DatalabChandraSettings,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings
        self.revision_pinned = bool(settings.contract_model_revision)
        self.model_revision = (
            settings.contract_model_revision or f"datalab-{settings.mode}-unpinned"
        )
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            headers={"X-API-Key": settings.api_key},
            timeout=httpx.Timeout(settings.timeout_seconds),
        )
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    @staticmethod
    def _extension_and_type(image: bytes) -> tuple[str, str]:
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png", "image/png"
        if image.startswith(b"\xff\xd8"):
            return "jpg", "image/jpeg"
        if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
            return "webp", "image/webp"
        raise DatalabChandraError("Datalab crop must be PNG, JPEG or WebP")

    @staticmethod
    def _image(crop: CttFieldCrop) -> Tuple[bytes, str]:
        return (
            crop.context_image_bytes or crop.image_bytes,
            crop.context_sha256 or crop.crop_sha256,
        )

    async def _submit(self, crops: Sequence[CttFieldCrop]) -> str:
        if not crops:
            raise DatalabChandraError("cannot submit an empty crop group")
        image_bytes, image_sha256 = self._image(crops[0])
        if any(self._image(crop) != (image_bytes, image_sha256) for crop in crops):
            raise DatalabChandraError("crop group must share exact context bytes")
        field_names = [crop.field_name for crop in crops]
        if len(set(field_names)) != len(field_names):
            raise DatalabChandraError("crop group contains duplicate field names")
        if len(image_bytes) > self.settings.max_image_bytes:
            raise DatalabChandraError("crop exceeds Datalab image byte limit")
        extension, content_type = self._extension_and_type(image_bytes)
        files = {
            "file": (
                f"{image_sha256}.{extension}",
                image_bytes,
                content_type,
            )
        }
        schema = {
            "type": "object",
            "properties": {
                crop.field_name: {
                    "type": ["string", "null"],
                    "description": (
                        "Transcripción literal solo del valor manuscrito del campo "
                        f"{crop.field_name}. La imagen puede incluir etiquetas y "
                        "otros campos como contexto; no incluirlos, corregir, "
                        "completar ni explicar."
                    ),
                }
                for crop in crops
            },
            "required": field_names,
        }
        data = {
            "mode": self.settings.mode,
            "page_schema": json.dumps(schema, ensure_ascii=False),
            "max_pages": "1",
        }
        response = None
        for attempt in range(self.settings.max_submit_attempts):
            response = await self._client.post(
                "/api/v1/extract", data=data, files=files
            )
            if response.status_code != 429:
                break
            if attempt + 1 == self.settings.max_submit_attempts:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                delay = 0.0
            delay = max(
                delay,
                self.settings.submit_backoff_seconds * (2**attempt),
            )
            await asyncio.sleep(min(delay, 30.0))
        if response is None:
            raise DatalabChandraError("Datalab submit produced no response")
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            raise DatalabChandraError(str(body.get("error") or "submit failed"))
        check_url = str(body.get("request_check_url") or "")
        if not check_url.startswith("https://www.datalab.to/api/v1/"):
            raise DatalabChandraError("Datalab returned an invalid poll URL")
        return check_url

    async def _poll(self, check_url: str) -> Mapping[str, object]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.max_poll_seconds
        while loop.time() < deadline:
            response = await self._client.get(check_url)
            response.raise_for_status()
            body = response.json()
            status = str(body.get("status") or "").lower()
            if status == "complete":
                if not body.get("success", True):
                    raise DatalabChandraError(
                        str(body.get("error") or "conversion failed")
                    )
                return body
            if status in {"failed", "error", "cancelled"}:
                raise DatalabChandraError(
                    str(body.get("error") or f"conversion {status}")
                )
            await asyncio.sleep(self.settings.poll_interval_seconds)
        raise DatalabChandraError("Datalab conversion timed out")

    async def _group(self, crops: Sequence[CttFieldCrop]) -> List[CttProviderRawField]:
        # The provider's limit is jobs in flight, not individual HTTP calls.
        # Hold one permit for the complete submit-to-completion lifecycle.
        async with self._semaphore:
            body = await self._poll(await self._submit(crops))
        try:
            extracted = json.loads(str(body.get("extraction_schema_json") or "{}"))
        except json.JSONDecodeError as exc:
            raise DatalabChandraError("invalid structured extraction JSON") from exc
        versions = body.get("versions") or {}
        if self.revision_pinned:
            receipt = hashlib.sha256(
                json.dumps(versions, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            expected = str(self.settings.contract_model_revision)
            if expected.startswith("sha256:") and receipt != expected.removeprefix(
                "sha256:"
            ):
                raise DatalabChandraError("Datalab version receipt mismatch")
        results = []
        for crop in crops:
            raw_value = extracted.get(crop.field_name)
            raw_text = str(raw_value).strip() if raw_value is not None else None
            results.append(
                CttProviderRawField(
                    crop_id=crop.crop_id,
                    crop_sha256=crop.crop_sha256,
                    raw_text=raw_text,
                    candidates=[],
                )
            )
        return results

    async def transcribe(
        self, crops: Sequence[CttFieldCrop]
    ) -> Mapping[str, CttProviderRawField]:
        groups: Dict[str, List[CttFieldCrop]] = {}
        for crop in crops:
            _image_bytes, image_sha256 = self._image(crop)
            groups.setdefault(image_sha256, []).append(crop)
        grouped_results = await asyncio.gather(
            *(self._group(group) for group in groups.values())
        )
        results = [item for group in grouped_results for item in group]
        output: Dict[str, CttProviderRawField] = {
            result.crop_id: result for result in results
        }
        if len(output) != len(crops):
            raise DatalabChandraError("duplicate crop ids in Datalab result")
        return output

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
