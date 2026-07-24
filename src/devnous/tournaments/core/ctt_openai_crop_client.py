"""Structured OpenAI fallback restricted to individual CTT field crops."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .ctt_ocr_provider import CttFieldCrop, CttProviderRawField


class _CropText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: Optional[str] = Field(default=None, max_length=500)
    candidates: List[str] = Field(default_factory=list, max_length=8)


class OpenAICttCropClient:
    name = "openai"
    pipeline_version = "openai.ctt.field_crop.v1"
    revision_pinned = True

    def __init__(
        self,
        client: Any,
        *,
        model_revision: str,
        timeout_seconds: float = 45.0,
    ) -> None:
        if not model_revision.strip():
            raise ValueError("OpenAI model revision is required")
        self.client = client
        self.model_revision = model_revision.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_api_key(
        cls, api_key: str, *, model_revision: str, timeout_seconds: float = 45.0
    ) -> "OpenAICttCropClient":
        if not api_key.strip():
            raise ValueError("OpenAI API key is required")
        from openai import AsyncOpenAI

        return cls(
            AsyncOpenAI(api_key=api_key),
            model_revision=model_revision,
            timeout_seconds=timeout_seconds,
        )

    async def _one(self, crop: CttFieldCrop) -> CttProviderRawField:
        encoded = base64.b64encode(crop.image_bytes).decode("ascii")
        response = await self.client.responses.parse(
            model=self.model_revision,
            instructions=(
                "Transcribe únicamente el campo recortado. No deduzcas desde "
                "otros campos. Usa null si está vacío o ilegible; candidates sólo "
                "para alternativas realmente visibles."
            ),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Campo CTT: {crop.field_name}",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{encoded}",
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=_CropText,
            store=False,
            max_output_tokens=500,
            timeout=self.timeout_seconds,
        )
        parsed = _CropText.model_validate(response.output_parsed)
        return CttProviderRawField(
            crop_id=crop.crop_id,
            crop_sha256=crop.crop_sha256,
            raw_text=parsed.raw_text,
            candidates=parsed.candidates,
        )

    async def transcribe(
        self, crops: Sequence[CttFieldCrop]
    ) -> Mapping[str, CttProviderRawField]:
        # Deliberately sequential: fallback is bounded to rejected fields and
        # must not create an unbounded burst of provider calls.
        output: Dict[str, CttProviderRawField] = {}
        for crop in crops:
            output[crop.crop_id] = await self._one(crop)
        return output

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            await close()
