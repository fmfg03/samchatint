import hashlib
from dataclasses import replace

import httpx
import pytest

from devnous.tournaments.core.ctt_datalab_client import (
    DatalabChandraClient,
    DatalabChandraSettings,
)
from devnous.tournaments.core.ctt_ocr_provider import CttFieldCrop


def _crop():
    payload = b"\x89PNG\r\n\x1a\nfield"
    context = b"\x89PNG\r\n\x1a\ncontext"
    return CttFieldCrop(
        crop_id="p1:slot-1:nombre:given_names",
        document_sha256=hashlib.sha256(b"doc").hexdigest(),
        normalized_page_sha256=hashlib.sha256(b"page").hexdigest(),
        crop_sha256=hashlib.sha256(payload).hexdigest(),
        field_name="given_names",
        source_page=1,
        source_region="slot-1:nombre",
        slot=1,
        image_bytes=payload,
        context_image_bytes=context,
        context_sha256=hashlib.sha256(context).hexdigest(),
    )


@pytest.mark.asyncio
async def test_datalab_adapter_uses_structured_literal_field_and_polls():
    seen = []

    async def handler(request):
        seen.append((request.method, request.url.path, request.content))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "request_id": "req-1",
                    "request_check_url": "https://www.datalab.to/api/v1/extract/req-1",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "complete",
                "success": True,
                "extraction_schema_json": '{"given_names":"Sophia"}',
                "versions": {"processor": "test"},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://www.datalab.to",
        headers={"X-API-Key": "temporary-test-key"},
    ) as http:
        client = DatalabChandraClient(
            DatalabChandraSettings(
                api_key="temporary-test-key",
                poll_interval_seconds=0.001,
            ),
            client=http,
        )
        result = await client.transcribe([_crop()])

    assert result["p1:slot-1:nombre:given_names"].raw_text == "Sophia"
    assert seen[0][0:2] == ("POST", "/api/v1/extract")
    assert b'name="mode"' in seen[0][2]
    assert b"balanced" in seen[0][2]
    assert b"page_schema" in seen[0][2]
    assert b"given_names" in seen[0][2]
    assert b"context" in seen[0][2]
    assert b"field" not in seen[0][2]
    assert seen[1][0:2] == ("GET", "/api/v1/extract/req-1")


@pytest.mark.asyncio
async def test_standard_datalab_account_is_explicitly_unpinned():
    client = DatalabChandraClient(
        DatalabChandraSettings(api_key="temporary-test-key"),
    )
    assert client.revision_pinned is False
    assert client.model_revision.endswith("-unpinned")
    await client.close()


@pytest.mark.asyncio
async def test_fields_with_same_context_use_one_structured_request():
    first = _crop()
    second_payload = b"\x89PNG\r\n\x1a\nsecond-field"
    second = replace(
        first,
        crop_id="p1:slot-1:apellidos:surnames",
        crop_sha256=hashlib.sha256(second_payload).hexdigest(),
        field_name="surnames",
        source_region="slot-1:apellidos",
        image_bytes=second_payload,
    )
    posts = 0

    async def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            assert b"given_names" in request.content
            assert b"surnames" in request.content
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "request_check_url": "https://www.datalab.to/api/v1/extract/group",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "complete",
                "success": True,
                "extraction_schema_json": (
                    '{"given_names":"Sophia","surnames":"Rodriguez Linares"}'
                ),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://www.datalab.to",
        headers={"X-API-Key": "temporary-test-key"},
    ) as http:
        client = DatalabChandraClient(
            DatalabChandraSettings(
                api_key="temporary-test-key", poll_interval_seconds=0.001
            ),
            client=http,
        )
        result = await client.transcribe([first, second])

    assert posts == 1
    assert result[first.crop_id].raw_text == "Sophia"
    assert result[second.crop_id].raw_text == "Rodriguez Linares"


@pytest.mark.asyncio
async def test_submit_retries_rate_limit_with_bounded_policy():
    posts = 0

    async def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            if posts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "request_check_url": "https://www.datalab.to/api/v1/extract/retry",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "complete",
                "success": True,
                "extraction_schema_json": '{"given_names":"Sophia"}',
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.datalab.to"
    ) as http:
        client = DatalabChandraClient(
            DatalabChandraSettings(
                api_key="temporary-test-key",
                poll_interval_seconds=0.001,
                submit_backoff_seconds=0.001,
            ),
            client=http,
        )
        result = await client.transcribe([_crop()])

    assert posts == 2
    assert result[_crop().crop_id].raw_text == "Sophia"
