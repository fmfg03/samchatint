from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import client_executive_routes, user_routes


def _employee(*, visible_tool_keys=None, rol="coordinador"):
    return SimpleNamespace(
        id="direction-user",
        nombre="Usuario Dirección",
        rol=rol,
        activo=True,
        departamento="Dirección",
        correo="direction-nav@example.invalid",
        visible_tool_keys=set(visible_tool_keys or set()),
        direction_entry_visible=False,
    )


@pytest.mark.asyncio
async def test_direction_discovery_uses_route_owned_scope_without_explicit_allow(
    monkeypatch,
):
    employee = _employee(visible_tool_keys={"panel.home"})

    async def decision(*_args, **_kwargs):
        return None

    async def portfolios(*_args, **_kwargs):
        return ["portfolio-1"]

    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", decision)
    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )

    assert await client_executive_routes.direction_entry_visible(object(), employee)


@pytest.mark.asyncio
async def test_direction_discovery_respects_explicit_deny(monkeypatch):
    employee = _employee(visible_tool_keys={"direccion.tableros_ejecutivos"})

    async def decision(*_args, **_kwargs):
        return False

    async def portfolios(*_args, **_kwargs):
        raise AssertionError("explicit deny must fail before scope lookup")

    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", decision)
    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )

    assert not await client_executive_routes.direction_entry_visible(object(), employee)


@pytest.mark.asyncio
async def test_direction_discovery_rejects_coarse_allow_without_scope(monkeypatch):
    employee = _employee(visible_tool_keys={"direccion.tableros_ejecutivos"})

    async def decision(*_args, **_kwargs):
        return True

    async def portfolios(*_args, **_kwargs):
        return []

    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", decision)
    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )

    assert not await client_executive_routes.direction_entry_visible(object(), employee)


def test_top_navigation_requires_route_owned_discovery_flag():
    employee = _employee(visible_tool_keys={"direccion.tableros_ejecutivos"})

    html = user_routes.render_top_navigation(employee)
    assert 'href="/direccion/tableros"' not in html

    employee.direction_entry_visible = True
    html = user_routes.render_top_navigation(employee)
    assert 'href="/direccion/tableros"' in html
    assert ">Dirección</a>" in html


def test_generic_admin_role_does_not_create_direction_discovery():
    employee = _employee(visible_tool_keys=set(), rol="admin")

    html = user_routes.render_top_navigation(employee)

    assert 'href="/direccion/tableros"' not in html


@pytest.mark.asyncio
async def test_direction_discovery_reraises_non_authorization_http_error(monkeypatch):
    employee = _employee()

    async def fail(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="temporary failure")

    monkeypatch.setattr(
        client_executive_routes, "_assigned_direction_portfolios", fail
    )

    with pytest.raises(HTTPException) as excinfo:
        await client_executive_routes.direction_entry_visible(object(), employee)

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_panel_derives_direction_entry_from_route_owned_discovery(monkeypatch):
    employee = _employee(visible_tool_keys={"panel.home"})

    async def visible_tools(*_args, **_kwargs):
        return {"panel.home"}

    async def direction_visible(*_args, **_kwargs):
        return True

    async def cannot_review(*_args, **_kwargs):
        return False

    monkeypatch.setattr(user_routes, "visible_tools_for", visible_tools)
    monkeypatch.setattr(
        client_executive_routes, "direction_entry_visible", direction_visible
    )
    monkeypatch.setattr(
        user_routes, "_can_review_pending_approvals", cannot_review
    )
    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda _employee: False)

    html = await user_routes.panel(object(), object(), employee)

    assert employee.direction_entry_visible is True
    assert 'href="/direccion/tableros"' in html
    assert ">Dirección</a>" in html
    assert "Tableros ejecutivos de solo lectura dentro de tu alcance asignado." in html
