from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import (
    client_executive_routes,
    client_reporting_routes,
    dependencies,
)


EMPLOYEE_ID = UUID("10000000-0000-0000-0000-000000000319")


def _employee(role="empleado", *, active=True):
    return SimpleNamespace(
        id=EMPLOYEE_ID,
        rol=role,
        activo=active,
        departamento=None,
        permissions=set(),
    )


def _direction_client(monkeypatch, employee, *, decision=None, portfolios=None):
    """Build a session-backed app that exercises middleware then Direction routes."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="direction-test-session")
    app.include_router(client_executive_routes.router)
    app.include_router(client_reporting_routes.router)
    session = object()

    async def db_session():
        yield session

    async def load_employee(_session, employee_id):
        return employee if employee_id == EMPLOYEE_ID else None

    async def visible_tools(_session, _employee):
        return set()

    async def unexpected_global_path_gate(*_args, **_kwargs):
        raise AssertionError(
            "Direction routes must delegate authorization to their guard."
        )

    async def direction_decision(_session, _employee, _tool, action_key):
        if decision is not None:
            return decision(action_key)
        return None

    async def direction_portfolios(_session, _employee_id, *, is_superadmin):
        if portfolios is not None:
            return portfolios(is_superadmin)
        return ["portfolio-a"]

    async def dashboard(*_args, **_kwargs):
        return {"edition_year": 2026, "cards": []}

    app.dependency_overrides[dependencies.get_db_session] = db_session
    monkeypatch.setattr(dependencies, "_load_empleado_proxy_by_id", load_employee)
    monkeypatch.setattr(dependencies, "visible_tools_for", visible_tools)
    monkeypatch.setattr(dependencies, "can_access_path", unexpected_global_path_gate)
    monkeypatch.setattr(
        client_executive_routes, "explicit_tool_decision", direction_decision
    )
    monkeypatch.setattr(
        client_executive_routes,
        "authorized_direction_portfolio_ids",
        direction_portfolios,
    )
    monkeypatch.setattr(client_executive_routes, "build_client_dashboard", dashboard)

    @app.get("/_test/login")
    async def login(request: Request):
        request.session["empleado_id"] = str(EMPLOYEE_ID)
        return {"ok": True}

    return TestClient(app, follow_redirects=False)


def test_direction_route_requires_authenticated_session(monkeypatch):
    client = _direction_client(monkeypatch, _employee())
    response = client.get("/direccion/tableros")
    assert response.status_code == 401


def test_direction_route_rejects_inactive_employee_before_guard(monkeypatch):
    client = _direction_client(monkeypatch, _employee(active=False))
    client.get("/_test/login")
    response = client.get("/direccion/tableros")
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["empleado", "admin"])
def test_active_employee_without_eligible_scope_reaches_guard_and_gets_403(
    monkeypatch, role
):
    client = _direction_client(
        monkeypatch,
        _employee(role),
        portfolios=lambda _is_superadmin: [],
    )
    client.get("/_test/login")
    response = client.get("/direccion/tableros")
    assert response.status_code == 403


def test_position_holder_reaches_direction_endpoint_after_middleware(monkeypatch):
    client = _direction_client(monkeypatch, _employee("coordinador"))
    client.get("/_test/login")
    response = client.get("/direccion/tableros")
    assert response.status_code == 200


def test_superadmin_reaches_direction_endpoint_with_active_scope(monkeypatch):
    client = _direction_client(monkeypatch, _employee("superadmin"))
    client.get("/_test/login")
    response = client.get("/direccion/tableros")
    assert response.status_code == 200


def test_explicit_denial_blocks_superadmin_after_middleware(monkeypatch):
    client = _direction_client(
        monkeypatch,
        _employee("superadmin"),
        decision=lambda _action: False,
    )
    client.get("/_test/login")
    response = client.get("/direccion/tableros")
    assert response.status_code == 403


def test_read_permission_cannot_post_direction_report_write(monkeypatch):
    client = _direction_client(
        monkeypatch,
        _employee("coordinador"),
        decision=lambda action: True if action == "ver" else None,
    )
    client.get("/_test/login")
    response = client.post(
        "/direccion/reportes/gestion/configuraciones",
        data={"portfolio_id": "portfolio-a", "frequency": "weekly"},
    )
    assert response.status_code == 403
