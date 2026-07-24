import base64
import hashlib
import json

import httpx
import pytest

from devnous.tournaments.core.ctt_chandra_client import (
    ChandraCircuitOpen,
    ChandraClientSettings,
    ChandraCttClient,
    ChandraServiceError,
)
from devnous.tournaments.core.ctt_ocr_provider import CttFieldCrop


def _crop(payload=b"\xff\xd8image"):
    return CttFieldCrop(
        crop_id="p1:slot-4:birth_date",
        document_sha256=hashlib.sha256(b"document").hexdigest(),
        normalized_page_sha256=hashlib.sha256(b"page").hexdigest(),
        crop_sha256=hashlib.sha256(payload).hexdigest(),
        field_name="birth_date",
        source_page=1,
        source_region="slot-4:birth_date",
        slot=4,
        image_bytes=payload,
    )


def _settings(**changes):
    values = {
        "base_url": "http://chandra.internal",
        "expected_model_revision": "hf-sha-123",
        "failure_threshold": 2,
    }
    values.update(changes)
    return ChandraClientSettings(**values)


@pytest.mark.asyncio
async def test_health_revision_and_no_database_gate():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "model_revision": "wrong",
                "pipeline_version": "chandra.ctt.crop.v1",
                "database_access": False,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://chandra.internal"
    ) as http:
        client = ChandraCttClient(_settings(), client=http)
        with pytest.raises(ChandraServiceError, match="revision"):
            await client.healthcheck()


@pytest.mark.asyncio
async def test_transcription_sends_only_crop_metadata_and_bytes():
    crop = _crop()
    context = b"\xff\xd8context"
    crop = CttFieldCrop(
        **{
            **crop.__dict__,
            "context_image_bytes": context,
            "context_sha256": hashlib.sha256(context).hexdigest(),
        }
    )
    seen = []

    async def handler(request):
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "model_revision": "hf-sha-123",
                    "pipeline_version": "chandra.ctt.crop.v1",
                    "database_access": False,
                },
            )
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "crop_id": body["crop_id"],
                "crop_sha256": body["crop_sha256"],
                "raw_text": "28/10/2004",
                "candidates": [],
                "model_revision": "hf-sha-123",
                "pipeline_version": "chandra.ctt.crop.v1",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://chandra.internal"
    ) as http:
        client = ChandraCttClient(_settings(), client=http)
        result = await client.transcribe([crop])

    assert result[crop.crop_id].raw_text == "28/10/2004"
    assert seen[0]["slot"] == 4
    assert seen[0]["context_sha256"] == hashlib.sha256(context).hexdigest()
    assert base64.b64decode(seen[0]["image_base64"]) == context
    assert "document_sha256" not in seen[0]
    assert "page_image" not in seen[0]


@pytest.mark.asyncio
async def test_byte_limit_is_fail_closed():
    crop = _crop(b"\xff\xd8" + b"x" * 2048)
    settings = _settings(max_image_bytes=1024)

    async def handler(request):
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "model_revision": "hf-sha-123",
                    "pipeline_version": "chandra.ctt.crop.v1",
                    "database_access": False,
                },
            )
        raise AssertionError("oversized crop must not be posted")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://chandra.internal",
    ) as http:
        client = ChandraCttClient(settings, client=http)
        with pytest.raises(ChandraServiceError, match="byte limit"):
            await client.transcribe([crop])


@pytest.mark.asyncio
async def test_circuit_opens_after_repeated_failures():
    async def handler(request):
        return httpx.Response(503)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://chandra.internal",
    ) as http:
        client = ChandraCttClient(_settings(), client=http)
        with pytest.raises(ChandraServiceError):
            await client.healthcheck()
        with pytest.raises(ChandraServiceError):
            await client.healthcheck()
        with pytest.raises(ChandraCircuitOpen):
            await client.healthcheck()
