"""Presentation and cheap read-poll acceptance, without financial mutations."""

from unittest.mock import AsyncMock

import httpx
import pytest
from lxml import html

from tests.unit.gastos.test_amex_direct_routes import (
    complete_direct_workspace,
    direct_workspace,
    workspace,
)


async def get(w, suffix=""):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    ) as client:
        return await client.get(f"{w.routes.BASE}/{w.statement.id}/directo{suffix}")


@pytest.mark.asyncio
async def test_poll_only_reads_authorized_statement_source(complete_direct_workspace):
    w = complete_direct_workspace
    response = await get(w, "?workspace_poll=1")
    assert response.status_code == 200
    assert response.json() == {"source_version": w.view.source_version}
    w.routes.context.list_direct_statement.assert_awaited_once()
    w.routes.context.list_direct_classification_options.assert_not_awaited()
    w.routes.suggestions.suggest_direct_statement_cfdis.assert_not_awaited()
    w.routes.accounting.preview_direct_accounting.assert_not_awaited()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_preserves_owner_denial(complete_direct_workspace):
    w = complete_direct_workspace
    w.routes.context.list_direct_statement = AsyncMock(
        side_effect=PermissionError("denied")
    )
    response = await get(w, "?workspace_poll=1")
    assert response.status_code == 403
    w.routes.accounting.preview_direct_accounting.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_statement_one_wrapper_dialog_sources_and_no_nested_forms(
    complete_direct_workspace,
):
    w = complete_direct_workspace
    response = await get(w)
    assert response.status_code == 200
    doc = html.fromstring(response.text)
    wrappers = doc.xpath('//*[@id="amex-workspace-view"]')
    assert len(wrappers) == 1
    assert wrappers[0].get("data-amex-view") == "statement"
    assert wrappers[0].get("data-amex-root") == "true"
    assert wrappers[0].get("data-source-version") == str(w.view.source_version)
    assert not doc.xpath("//form//form")
    assert doc.xpath('//script[contains(@src,"/static/amex_workspace.js")]')
    assert doc.xpath('//link[contains(@href,"/static/amex_workspace.css")]')
    for name in ("treatment", "project", "budget", "no-cfdi", "journal"):
        assert doc.xpath(f'//*[@data-amex-dialog-source="{name}"]')
        assert doc.xpath(f'//*[@data-amex-open="{name}"]')
    row = doc.xpath(f'//tr[@data-item-id="{w.item.id}"]')[0]
    assert row.get("data-amex-bucket")
    assert row.get("data-amex-search")
    assert "<img src=x onerror=bad()>" not in response.text
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_owner_redirect_marks_error_and_rolls_back(
    complete_direct_workspace,
):
    from urllib.parse import parse_qs, urlsplit

    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace
    w.routes.matching.reconcile_direct_statement.return_value = DirectResult(
        "pending", "stale_item_version"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    ) as client:
        page = await client.get(f"{w.routes.BASE}/{w.statement.id}/directo")
        token = html.fromstring(page.text).xpath('//input[@name="_csrf_token"]/@value')[
            0
        ]
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/conciliar",
            data={"_csrf_token": token, "expected_source_version": "4"},
        )
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["error_msg"] == query["msg"]
    assert "cambió" in query["error_msg"][0]
    w.routes.matching.reconcile_direct_statement.assert_awaited_once()
    w.session.rollback.assert_awaited_once()
    w.session.commit.assert_not_awaited()
