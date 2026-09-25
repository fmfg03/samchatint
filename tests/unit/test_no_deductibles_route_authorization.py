from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import admin_routes, dependencies
from devnous.gastos.services import access_control_service


EMPLOYEE_ID = UUID("10000000-0000-0000-0000-000000000646")
NO_DEDUCIBLES_PATH = "/admin/finanzas/no-deducibles"
NO_DEDUCIBLES_EXPORT_PATH = f"{NO_DEDUCIBLES_PATH}/export.xlsx"


def _employee(*, role="empleado", permissions=()):
    return SimpleNamespace(
        id=EMPLOYEE_ID,
        nombre="Release smoke",
        correo="fmfg@az.tc",
        rol=role,
        activo=True,
        departamento=None,
        permissions=set(permissions),
    )


def _client(monkeypatch, employee):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="no-deducibles-route-test")
    app.include_router(admin_routes.router)
    session = object()

    async def db_session():
        yield session

    async def load_employee(_session, employee_id):
        return employee if employee_id == EMPLOYEE_ID else None

    async def visible_tools(_session, _employee):
        return set()

    async def global_path_gate(_session, _employee, path, _method):
        if path in {NO_DEDUCIBLES_PATH, NO_DEDUCIBLES_EXPORT_PATH}:
            raise AssertionError(
                "exact no-deducibles paths must use their route guard"
            )
        return False

    async def route_owned_path_gate(_session, _employee, _path, _method):
        return True

    async def report_source(*_args, **_kwargs):
        return {
            "summary": {"by_currency": {}, "non_deductible_count": 0},
            "non_deductible_rows": [],
        }

    async def tournaments(*_args, **_kwargs):
        return []

    app.dependency_overrides[dependencies.get_db_session] = db_session
    app.dependency_overrides[admin_routes.get_db_session] = db_session
    monkeypatch.setattr(dependencies, "_load_empleado_proxy_by_id", load_employee)
    monkeypatch.setattr(dependencies, "visible_tools_for", visible_tools)
    monkeypatch.setattr(dependencies, "can_access_path", global_path_gate)
    monkeypatch.setattr(
        dependencies,
        "can_defer_path_authorization_to_route_guard",
        route_owned_path_gate,
    )
    monkeypatch.setattr(
        admin_routes, "render_admin_navigation", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        admin_routes, "_admin_workspace_styles", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        admin_routes,
        "_render_admin_workspace_hero",
        lambda **kwargs: kwargs["title"],
    )
    import samchat.finance_platform.no_deductibles as no_deducibles

    monkeypatch.setattr(no_deducibles, "build_no_deductibles_source", report_source)
    monkeypatch.setattr(
        no_deducibles, "list_tournaments_for_no_deductibles", tournaments
    )

    @app.get("/_test/login")
    async def login(request: Request):
        request.session["empleado_id"] = str(EMPLOYEE_ID)
        return {"ok": True}

    @app.get("/admin/finanzas/other-finance")
    async def other_finance(
        current_empleado=Depends(dependencies.get_current_empleado),
    ):
        return {"employee_id": str(current_empleado.id)}

    return TestClient(app, follow_redirects=False)


def test_route_owned_authorization_is_exact_for_no_deducibles_only():
    assert dependencies._uses_route_owned_authorization(NO_DEDUCIBLES_PATH)
    assert dependencies._uses_route_owned_authorization(
        NO_DEDUCIBLES_EXPORT_PATH
    )
    assert not dependencies._uses_route_owned_authorization("/admin/finanzas")
    assert not dependencies._uses_route_owned_authorization(
        "/admin/finanzas/otra-ruta"
    )


@pytest.mark.parametrize(
    "path", [NO_DEDUCIBLES_PATH, NO_DEDUCIBLES_EXPORT_PATH]
)
def test_no_deducibles_routes_require_an_authenticated_session(monkeypatch, path):
    client = _client(monkeypatch, _employee())
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path", [NO_DEDUCIBLES_PATH, NO_DEDUCIBLES_EXPORT_PATH]
)
def test_employee_without_finance_permission_is_denied(monkeypatch, path):
    client = _client(monkeypatch, _employee())
    client.get("/_test/login")
    assert client.get(path).status_code == 403


@pytest.mark.parametrize(
    "path", [NO_DEDUCIBLES_PATH, NO_DEDUCIBLES_EXPORT_PATH]
)
def test_employee_with_only_finance_permission_reaches_no_deducibles(
    monkeypatch, path
):
    client = _client(
        monkeypatch, _employee(permissions={"admin.finanzas.manage"})
    )
    client.get("/_test/login")
    assert client.get(path).status_code == 200


def test_employee_with_only_finance_permission_remains_denied_elsewhere(
    monkeypatch,
):
    client = _client(
        monkeypatch, _employee(permissions={"admin.finanzas.manage"})
    )
    client.get("/_test/login")
    assert client.get("/admin/finanzas/other-finance").status_code == 403


def test_explicit_finance_denial_is_preserved_for_no_deducibles(monkeypatch):
    client = _client(
        monkeypatch, _employee(permissions={"admin.finanzas.manage"})
    )

    async def explicit_denial(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        dependencies,
        "can_defer_path_authorization_to_route_guard",
        explicit_denial,
    )
    client.get("/_test/login")
    assert client.get(NO_DEDUCIBLES_PATH).status_code == 403


@pytest.mark.asyncio
async def test_route_guard_deferral_preserves_explicit_tool_denial(monkeypatch):
    observed = {}

    async def explicit_denial(_session, _employee, tool_key, action_key):
        observed.update(tool_key=tool_key, action_key=action_key)
        return False

    monkeypatch.setattr(
        access_control_service, "explicit_tool_decision", explicit_denial
    )
    allowed = await access_control_service.can_defer_path_authorization_to_route_guard(
        object(), _employee(), NO_DEDUCIBLES_EXPORT_PATH
    )

    assert not allowed
    assert observed == {"tool_key": "admin.finanzas", "action_key": "ver"}


def test_existing_finance_role_still_reaches_no_deducibles(monkeypatch):
    client = _client(monkeypatch, _employee(role="finanzas"))
    client.get("/_test/login")
    assert client.get(NO_DEDUCIBLES_PATH).status_code == 200
