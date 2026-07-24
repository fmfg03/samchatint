"""Shared runtime seam used by web and Telegram CTT intake surfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from PIL import Image

from .ctt_crop_extractor import extract_ctt_with_providers, validate_ctt_crop
from .ctt_datalab_client import DatalabChandraClient, DatalabChandraSettings
from .ctt_ocr_provider import (
    CttOcrCoordinator,
    CttOcrMode,
    CttProviderFieldCache,
    ctt_ocr_mode,
)
from .ctt_openai_crop_client import OpenAICttCropClient


def configured_ctt_ocr_mode(value: Optional[str] = None) -> CttOcrMode:
    return ctt_ocr_mode(value or os.getenv("OCR_PROVIDER") or "openai")


async def run_ctt_provider_runtime(
    image_paths: Sequence[str | Path],
    *,
    mode: CttOcrMode,
    repo_root: Path,
    openai_api_key: Optional[str] = None,
    datalab_api_key: Optional[str] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Extract a two-page draft with no persistence or domain mutation access."""
    if len(image_paths) != 2:
        raise ValueError("provider runtime requires the two canonical CTT pages")
    openai_key = (openai_api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    datalab_key = (datalab_api_key or os.getenv("DATALAB_API_KEY") or "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is required for baseline/field fallback")
    if mode != CttOcrMode.OPENAI and not datalab_key:
        raise RuntimeError("DATALAB_API_KEY is required for Chandra modes")

    openai_model = (
        os.getenv("OPENAI_OCR_MODEL_REVISION")
        or os.getenv("OPENAI_OCR_MODEL")
        or "gpt-5.6-terra"
    )
    openai = OpenAICttCropClient.from_api_key(
        openai_key,
        model_revision=openai_model,
        timeout_seconds=float(os.getenv("OCR_FIELD_TIMEOUT_SECONDS", "45")),
    )
    chandra = None
    if mode != CttOcrMode.OPENAI:
        chandra = DatalabChandraClient(
            DatalabChandraSettings(
                api_key=datalab_key,
                mode=os.getenv("DATALAB_OCR_MODE", "balanced"),
                contract_model_revision=(
                    os.getenv("DATALAB_CONTRACT_MODEL_REVISION") or None
                ),
                timeout_seconds=float(os.getenv("DATALAB_TIMEOUT_SECONDS", "30")),
                max_poll_seconds=float(os.getenv("DATALAB_MAX_POLL_SECONDS", "120")),
                max_concurrency=max(1, int(os.getenv("DATALAB_MAX_CONCURRENCY", "2"))),
            )
        )
    cache_root = Path(
        os.getenv(
            "CTT_PROVIDER_CACHE_DIR",
            str(repo_root / "private" / "ctt_provider_cache"),
        )
    )
    coordinator = CttOcrCoordinator(
        openai=openai,
        chandra=chandra,
        cache=CttProviderFieldCache(cache_root),
        validator=validate_ctt_crop,
    )
    layout = json.loads(
        (repo_root / "config" / "layout_ctt_2026.json").read_text(encoding="utf-8")
    )
    images = []
    try:
        for path in image_paths:
            with Image.open(path) as source:
                images.append(source.convert("RGB"))
        return await extract_ctt_with_providers(images, layout, coordinator, mode=mode)
    finally:
        for image in images:
            image.close()
        await openai.close()
        if chandra is not None:
            await chandra.close()
