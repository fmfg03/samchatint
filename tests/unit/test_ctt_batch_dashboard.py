"""Dashboard integration preserves private source delivery and guard ordering."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

os.environ.setdefault("SESSION_SECRET_KEY", "synthetic-batch-route-0123456789abcdef")

import copa_telmex_dashboard as dashboard  # noqa: E402


@pytest.mark.asyncio
async def test_private_batch_asset_refreshes_account_before_private_delivery(
    monkeypatch,
):
    account, session, asset = uuid4(), uuid4(), uuid4()
    events = []
    client = object()

    async def context(request):
        events.append("fresh-account")
        return client, account, None

    async def call(actual_client, operation, *args):
        assert events == ["fresh-account"]
        assert actual_client is client and operation == "source_asset"
        assert args == (account, session, asset)
        return b"\x89PNG\r\n\x1a\nsynthetic"

    monkeypatch.setattr(
        dashboard,
        "_staff_batch_routes",
        SimpleNamespace(_catalog_context=context, _call=call),
    )
    response = await dashboard.serve_batch_review_asset(object(), session, asset)
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    entity = SimpleNamespace(
        image_path="ctt-cas-v2:" + "a" * 64, session_id=session, id=asset
    )
    assert dashboard._review_asset_public_url(entity).endswith(
        f"/sessions/{session}/assets/{asset}"
    )


@pytest.mark.asyncio
async def test_denied_account_cannot_call_private_source_service(monkeypatch):
    async def context(request):
        raise HTTPException(status_code=403)

    async def call(*args):
        raise AssertionError("private source requested before access checks")

    monkeypatch.setattr(
        dashboard,
        "_staff_batch_routes",
        SimpleNamespace(_catalog_context=context, _call=call),
    )
    with pytest.raises(HTTPException) as error:
        await dashboard.serve_batch_review_asset(object(), uuid4(), uuid4())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_private_protocol_data_cannot_render_as_source_page(monkeypatch):
    async def context(request):
        return object(), uuid4(), None

    async def call(*args):
        return {"private_proof": "must never render"}

    monkeypatch.setattr(
        dashboard,
        "_staff_batch_routes",
        SimpleNamespace(_catalog_context=context, _call=call),
    )
    with pytest.raises(HTTPException) as error:
        await dashboard.serve_batch_review_asset(object(), uuid4(), uuid4())
    assert error.value.status_code == 503
    assert error.value.detail == "Source unavailable"
