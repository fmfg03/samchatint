"""Generic AMEX entries render the direct workflow without changing legacy actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from devnous.gastos.routes import client_executive_routes, user_routes

DIRECT_URL = "/admin/contabilidad/amex/estados/directo"


def employee():
    return SimpleNamespace(
        id=uuid4(), rol="finanzas", nombre="Contabilidad", activo=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [True, False])
async def test_panel_amex_entry_opens_direct_workflow_only_when_tool_visible(
    monkeypatch, visible
):
    monkeypatch.setattr(
        user_routes,
        "visible_tools_for",
        AsyncMock(return_value={"admin.gastos.amex"} if visible else {"panel.home"}),
    )
    monkeypatch.setattr(
        client_executive_routes,
        "direction_entry_visible",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        user_routes, "_can_review_pending_approvals", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda *_: False)
    monkeypatch.setattr(user_routes, "_is_configuration_panel_user", lambda *_: False)
    html = await user_routes.panel(
        Request({"type": "http", "query_string": b""}), object(), employee()
    )
    assert (f'href="{DIRECT_URL}"' in html) is visible
    assert 'href="/admin/contabilidad/amex/estados"' not in html
    if visible:
        assert "Carga el estado, revisa CFDI y prepara la póliza de la tarjeta." in html
        assert "prepara las pólizas del informe" not in html


@pytest.mark.asyncio
async def test_batch_primary_entry_opens_direct_but_preserves_legacy_reconciliation(
    monkeypatch,
):
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        user_routes, "list_amex_card_accounts", AsyncMock(return_value=[])
    )
    html = await user_routes.carga_masiva_amex_get(
        Request({"type": "http", "query_string": b""}), session, employee()
    )
    assert f'href="{DIRECT_URL}" class="button primary"' in html
    assert 'href="/admin/contabilidad/amex/estados"' not in html
    assert 'href="/admin/gastos/amex/conciliacion"' in html
    assert 'action="/gastos/carga-masiva-amex"' in html
