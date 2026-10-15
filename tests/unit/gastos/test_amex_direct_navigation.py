"""Generic AMEX entries render the direct workflow without changing legacy actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from devnous.gastos.routes import client_executive_routes, user_routes
from devnous.gastos.services import access_control_service as access

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
    monkeypatch.setattr(access, "explicit_tool_decision", AsyncMock(return_value=None))
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
    monkeypatch.setattr(access, "explicit_tool_decision", AsyncMock(return_value=None))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["panel", "batch"])
@pytest.mark.parametrize("decision", [False, None, True, "lookup_error"])
async def test_amex_entry_honors_persisted_direct_read_decision(
    monkeypatch, entry, decision
):
    """Use the real rule lookup, including absence of any explicit allow."""
    actor = employee()
    calls = []

    async def execute(_query, params=None):
        if params and params.get("tool_key") == "amex_direct":
            calls.append(params)
            if decision == "lookup_error":
                raise RuntimeError("synthetic authority lookup unavailable")
            row = None if decision is None else (decision,)
            return SimpleNamespace(fetchone=lambda: row)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    session = SimpleNamespace(execute=execute)
    monkeypatch.setattr(
        user_routes, "visible_tools_for", AsyncMock(return_value={"admin.gastos.amex"})
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
    monkeypatch.setattr(
        user_routes, "list_amex_card_accounts", AsyncMock(return_value=[])
    )
    handler = (
        user_routes.panel if entry == "panel" else user_routes.carga_masiva_amex_get
    )
    html = await handler(Request({"type": "http", "query_string": b""}), session, actor)
    direct_allowed = decision is None or decision is True
    assert (f'href="{DIRECT_URL}"' in html) is direct_allowed
    assert ('href="/admin/contabilidad/amex/estados"' in html) is not direct_allowed
    assert len(calls) == 1
    assert calls[0]["action_key"] == "ver"
    assert not access.default_allows("amex_direct", actor.rol)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [
        SimpleNamespace(rol="finanzas", activo=False),
        SimpleNamespace(rol="empleado", activo=True),
        SimpleNamespace(rol="coordinador", activo=True),
        SimpleNamespace(rol="finanzas"),
    ],
)
async def test_direct_entry_never_infers_finance_or_active_authority(
    monkeypatch, actor
):
    lookup = AsyncMock(side_effect=AssertionError("ineligible actor must not query"))
    monkeypatch.setattr(
        user_routes, "can_defer_path_authorization_to_route_guard", lookup
    )
    assert await user_routes._amex_entry_destination(object(), actor) == (
        "/admin/contabilidad/amex/estados"
    )
    lookup.assert_not_awaited()
