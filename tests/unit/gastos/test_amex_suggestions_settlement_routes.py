"""Real HTTP rendering/action boundaries for suggestion and original-credit UI."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from lxml import html

from tests.unit.gastos import test_amex_direct_routes as direct_fixture

workspace = direct_fixture.workspace
direct_workspace = direct_fixture.direct_workspace
complete_direct_workspace = direct_fixture.complete_direct_workspace


@pytest.fixture
def suggested_workspace(complete_direct_workspace):
    w = complete_direct_workspace
    w.candidate = {
        "id": str(uuid4()),
        "uuid": str(uuid4()),
        "issuer_name": "SUNTORY <script>bad()</script>",
        "issuer_rfc": "RSU6902178N7",
        "date": "2026-10-06",
        "total": "3814.00",
        "amount_delta": "0.00",
        "days_delta": 7,
        "payment_method": "PPD",
        "complement_status": "not_local",
        "policy_applies": True,
        "status": "sin vigencia reciente",
        "observed_at": None,
        "available": True,
        "identity_hash": "fiscal-proof",
    }
    w.suggestions = {
        "items": [
            {
                "item_id": str(w.item.id),
                "item_version": 2,
                "row_number": 2,
                "bank_date": "2026-09-29",
                "bank_amount": "3814.00",
                "candidates": [w.candidate],
                "has_more": False,
                "cursor": None,
                "coverage_complete": True,
            }
        ],
        "has_more": False,
        "next_cursor": None,
        "source_version": 4,
    }
    w.routes.suggestions.suggest_direct_statement_cfdis.return_value = w.suggestions
    return w


def client(w):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    )


def state_path(w):
    return f"{w.routes.BASE}/{w.statement.id}/directo"


@pytest.mark.asyncio
async def test_statement_suggestions_show_cross_month_diff_without_nested_forms(
    suggested_workspace,
):
    w = suggested_workspace
    async with client(w) as c:
        response = await c.get(state_path(w))
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert not tree.xpath('//script[contains(text(), "bad()")]')
    assert "SUNTORY &lt;script&gt;" in response.text
    assert "2026-09-29" in response.text and "2026-10-06" in response.text
    assert "Días entre emisión y cargo: 7" in response.text
    assert "Complemento: No localizado" in response.text and "PPD" in response.text
    assert not tree.xpath("//form//form")
    w.routes.matching.link_direct_cfdi_manual.assert_not_awaited()
    w.routes.accounting.confirm_direct_accounting.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_invoice_picker_uses_suggestions_not_manual_search(
    suggested_workspace, monkeypatch
):
    w = suggested_workspace
    finder = AsyncMock()
    monkeypatch.setattr(w.routes.finder, "search_direct_cfdis", finder)
    async with client(w) as c:
        response = await c.get(
            state_path(w) + "/cfdi", params={"item_id": str(w.item.id)}
        )
    assert response.status_code == 200
    assert "La primera propuesta no está confirmada" in response.text
    assert "Vincular factura seleccionada" in response.text
    assert "Diferencia CFDI − cargo: 0.00" in response.text
    assert 'name="search" value="1"' in response.text
    finder.assert_not_awaited()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()
    w.routes.matching.link_direct_cfdi_manual.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_search_remains_explicit_and_separate(
    suggested_workspace, monkeypatch
):
    w = suggested_workspace
    finder = AsyncMock(
        return_value={
            "candidates": [],
            "row_number": 2,
            "item_version": 2,
            "source_version": 4,
            "has_more": False,
            "cursor": None,
        }
    )
    monkeypatch.setattr(w.routes.finder, "search_direct_cfdis", finder)
    async with client(w) as c:
        response = await c.get(
            state_path(w) + "/cfdi",
            params={"item_id": str(w.item.id), "search": "1", "q": "Suntory"},
        )
    assert response.status_code == 200
    assert finder.await_args.kwargs["q"] == "Suntory"
    w.routes.suggestions.suggest_direct_statement_cfdis.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_continuation_preserves_scoped_item_cursor(suggested_workspace):
    w = suggested_workspace
    w.suggestions["items"][0].update(
        has_more=True, cursor="opaque-cursor", coverage_complete=False
    )
    async with client(w) as c:
        response = await c.get(
            state_path(w) + "/cfdi", params={"item_id": str(w.item.id)}
        )
    tree = html.fromstring(response.text)
    urls = tree.xpath('//a[contains(text(), "Siguiente página")]/@href')
    assert len(urls) == 1 and "suggest_cursor=opaque-cursor" in urls[0]
    assert "item_id=" + str(w.item.id) in urls[0]
    assert "search=" not in urls[0]


@pytest.mark.asyncio
async def test_denied_suggestions_preserve_authorized_statement_read(
    suggested_workspace,
):
    from devnous.gastos.services.amex_direct_common import DirectAccessError

    w = suggested_workspace
    w.routes.suggestions.suggest_direct_statement_cfdis.side_effect = DirectAccessError(
        "explicit_action_denied"
    )
    async with client(w) as c:
        response = await c.get(state_path(w))
    assert response.status_code == 200
    assert "la lectura del estado se conserva" in response.text
    assert w.candidate["uuid"] not in response.text
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_prior_only_statement_freezes_controls_without_empty_policy(
    suggested_workspace,
):
    w = suggested_workspace
    w.preview.status = "completed"
    w.preview.progress = {"completion_receipt": {"id": str(uuid4())}}
    async with client(w) as c:
        response = await c.get(state_path(w))
    assert response.status_code == 200
    assert "Todos los cargos ya reconocidos" in response.text
    assert "No se creó otra póliza" in response.text
    assert "Propuesta incompleta" not in response.text
    assert "Generar póliza directa completa" not in response.text
    w.routes.suggestions.suggest_direct_statement_cfdis.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_credit_selection_is_explicit_csrf_guarded_owner_action(
    suggested_workspace,
):
    w = suggested_workspace
    origin = {
        key: str(uuid4())
        for key in (
            "origin_poliza_id",
            "origin_line_id",
            "origin_expense_id",
            "origin_document_id",
            "origin_cuenta_gastos_id",
            "origin_account_id",
        )
    }
    origin.update(
        origin_account_code="1170-002-001",
        credit_amount="3814.00",
        residual_amount="3814.00",
        cfdi_uuid=w.candidate["uuid"],
        concepto="Gasto original",
    )
    w.routes.settlements.list_settlement_sources.return_value["candidates"] = [origin]
    async with client(w) as c:
        page = await c.get(
            state_path(w) + "/cuenta-gastos", params={"item_id": str(w.item.id)}
        )
        assert page.status_code == 200 and "Crédito: 3814.00" in page.text
        w.routes.settlements.select_settlement_source.assert_not_awaited()
        token = html.fromstring(page.text).xpath('//input[@name="_csrf_token"]/@value')[
            0
        ]
        payload = {
            key: origin[key]
            for key in origin
            if key.startswith("origin_") and key != "origin_account_code"
        }
        payload.update(
            item_id=str(w.item.id),
            expected_source_version="4",
            expected_item_version="2",
            reason="Mismo consumo",
            confirmed="true",
        )
        denied = await c.post(state_path(w) + "/cuenta-gastos", data=payload)
        assert denied.status_code == 403
        w.routes.settlements.select_settlement_source.assert_not_awaited()
        response = await c.post(
            state_path(w) + "/cuenta-gastos", data={**payload, "_csrf_token": token}
        )
    assert response.status_code == 303
    args = w.routes.settlements.select_settlement_source.await_args.kwargs
    assert str(args["origin_line_id"]) == origin["origin_line_id"] and args["confirmed"]
    w.routes.accounting.confirm_direct_accounting.assert_not_awaited()
    w.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_dashboard_prior_completion_is_not_displayed_as_unconfirmed(
    suggested_workspace,
):
    w = suggested_workspace
    rows = w.routes.context.list_direct_statements.return_value
    rows[0]["completion_receipt"] = {"id": str(uuid4())}
    async with client(w) as c:
        response = await c.get(w.routes.BASE + "/directo")
    assert response.status_code == 200
    assert "Finalizado: cargos reconocidos previamente" in response.text
    assert "Sin corte directo confirmado" not in response.text


@pytest.mark.asyncio
async def test_source_picker_continuation_keeps_item_and_scoped_opaque_cursor(
    suggested_workspace,
):
    w = suggested_workspace
    w.routes.settlements.list_settlement_sources.return_value.update(
        has_more=True, next_cursor="opaque-source-cursor"
    )
    async with client(w) as c:
        response = await c.get(
            state_path(w) + "/cuenta-gastos",
            params={"item_id": str(w.item.id), "cursor": "current-source-cursor"},
        )
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    url = tree.xpath('//a[contains(text(), "Siguientes orígenes contables")]/@href')[0]
    assert "item_id=" + str(w.item.id) in url and "cursor=opaque-source-cursor" in url
    assert (
        w.routes.settlements.list_settlement_sources.await_args.kwargs["cursor"]
        == "current-source-cursor"
    )
