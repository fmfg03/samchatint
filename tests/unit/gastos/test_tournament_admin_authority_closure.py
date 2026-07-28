from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import admin_routes, dependencies
from devnous.gastos.services import tournament_authority_service

TARGET_ID = UUID("20000000-0000-0000-0000-000000000055")


class _Request:
    def __init__(self, *, session=None, form=None):
        self.session = dict(session or {})
        self._form = dict(form or {})

    async def form(self):
        return self._form


class _Mappings:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _Mappings(self._row)

    def scalar_one_or_none(self):
        return self._row


class _Session:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error
        self.execute_calls = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        if self.error:
            raise self.error
        return _Result(self.row)

    async def commit(self):
        self.commits += 1


def _employee(role: str, permissions=()):
    return SimpleNamespace(rol=role, permissions=set(permissions), activo=True)


def _http_client(employee=None):
    session = _Session()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.include_router(admin_routes.router)

    async def _db():
        yield session

    app.dependency_overrides[admin_routes.get_db_session] = _db
    app.dependency_overrides[dependencies.get_db_session] = _db
    if employee is not None:
        app.dependency_overrides[admin_routes.get_current_empleado] = lambda: employee

    @app.get("/_test/tournament-csrf")
    async def _seed_csrf(request: Request):
        request.session["tournament_admin_csrf"] = "bound-test-token"
        return {"ok": True}

    return TestClient(app, follow_redirects=False), session


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "superadmin", "super_admin"])
async def test_tournament_admin_roles_are_explicit(role: str) -> None:
    assert await admin_routes.require_tournament_admin(_employee(role))


@pytest.mark.asyncio
async def test_tournament_admin_permission_can_be_delegated_without_finance_role() -> (
    None
):
    employee = _employee("empleado", {"admin.torneos.manage"})
    assert await admin_routes.require_tournament_admin(employee) is employee


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["empleado", "finanzas", "director"])
async def test_unrelated_roles_cannot_administer_tournaments(role: str) -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_routes.require_tournament_admin(_employee(role))
    assert exc.value.status_code == 403


def test_anonymous_direct_post_is_rejected_before_tournament_write() -> None:
    client, session = _http_client()
    response = client.post("/admin/torneos/create", data={"name": "Bypass"})
    assert response.status_code == 401
    assert session.execute_calls == [] and session.commits == 0


def test_unrelated_authenticated_role_direct_post_is_forbidden() -> None:
    client, session = _http_client(_employee("finanzas"))
    response = client.post("/admin/torneos/create", data={"name": "Bypass"})
    assert response.status_code == 403
    assert session.execute_calls == [] and session.commits == 0


def test_admin_direct_post_without_csrf_is_forbidden_before_write() -> None:
    client, session = _http_client(_employee("admin"))
    response = client.post("/admin/torneos/create", data={"name": "Bypass"})
    assert response.status_code == 403
    assert session.execute_calls == [] and session.commits == 0


def test_admin_with_valid_csrf_reaches_quarantine_not_database() -> None:
    client, session = _http_client(_employee("admin"))
    assert client.get("/_test/tournament-csrf").status_code == 200
    response = client.post(
        "/admin/torneos/create",
        data={"name": "Bypass", "_csrf_token": "bound-test-token"},
    )
    assert response.status_code == 409
    assert session.execute_calls == [] and session.commits == 0


@pytest.mark.parametrize(
    "path",
    [
        "/admin/torneos/domain-alignment/run",
        "/admin/torneos/domain-alignment/audit",
    ],
)
def test_domain_alignment_post_rejects_cross_site_request_without_csrf(
    path: str,
) -> None:
    client, session = _http_client(_employee("admin"))
    response = client.post(
        path,
        data={"mode": "apply"},
        headers={"Origin": "https://attacker.invalid"},
    )
    assert response.status_code == 403
    assert session.execute_calls == [] and session.commits == 0


@pytest.mark.asyncio
async def test_csrf_token_is_session_bound_and_stable() -> None:
    request = _Request()
    first = admin_routes._tournament_admin_csrf_token(request)
    second = admin_routes._tournament_admin_csrf_token(request)
    assert first == second
    assert len(first) >= 32
    valid = _Request(session=request.session, form={"_csrf_token": first})
    assert await admin_routes.require_tournament_admin_csrf(valid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("submitted", [None, "", "wrong-session-token"])
async def test_csrf_rejects_missing_or_mismatched_token(submitted) -> None:
    request = _Request(
        session={"tournament_admin_csrf": "expected-token"},
        form={"_csrf_token": submitted},
    )
    with pytest.raises(HTTPException) as exc:
        await admin_routes.require_tournament_admin_csrf(request)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_governed_target_lookup_fails_closed_when_case_store_is_unavailable() -> (
    None
):
    with pytest.raises(
        tournament_authority_service.TournamentAuthorityUnavailableError
    ) as exc:
        await tournament_authority_service.get_applied_gastos_project_provenance(
            _Session(error=RuntimeError("database unavailable")), TARGET_ID
        )
    assert isinstance(
        exc.value, tournament_authority_service.TournamentAuthorityUnavailableError
    )


@pytest.mark.asyncio
async def test_governed_target_lookup_returns_none_only_for_a_clean_miss() -> None:
    session = _Session(row=None)
    assert (
        await tournament_authority_service.get_applied_gastos_project_provenance(
            session, TARGET_ID
        )
        is None
    )
    _, params = session.execute_calls[0]
    assert params == {"target_tournament_id": str(TARGET_ID)}


@pytest.mark.asyncio
async def test_governed_target_lookup_rejects_unverifiable_receipt() -> None:
    session = _Session(
        row={
            "case_id": "analyst_case_" + "5" * 32,
            "version_number": 4,
            "answer_contract": {"tournament_application": {"state": "applied"}},
        }
    )
    with pytest.raises(
        tournament_authority_service.TournamentAuthorityUnavailableError
    ) as exc:
        await tournament_authority_service.get_applied_gastos_project_provenance(
            session, TARGET_ID
        )
    assert isinstance(
        exc.value, tournament_authority_service.TournamentAuthorityUnavailableError
    )


@pytest.mark.asyncio
async def test_governed_target_is_blocked_with_provenance(monkeypatch) -> None:
    async def _reject(_session, _target):
        raise tournament_authority_service.GovernedGastosProjectError(
            case_id="analyst_case_" + "5" * 32,
            case_version=4,
            application_hash="sha256:" + "a" * 64,
        )

    monkeypatch.setattr(
        admin_routes,
        "require_ungoverned_gastos_project",
        _reject,
    )
    with pytest.raises(HTTPException) as exc:
        await admin_routes._require_legacy_gastos_project_mutable(object(), TARGET_ID)
    assert exc.value.status_code == 409
    assert exc.value.detail["case_version"] == 4


def _dependency(function, parameter: str):
    return inspect.signature(function).parameters[parameter].default.dependency


def test_every_legacy_route_has_named_tournament_authority_dependencies() -> None:
    read_handlers = [
        admin_routes.admin_tournaments,
        admin_routes.edit_tournament_form,
    ]
    mutation_handlers = [
        admin_routes.create_tournament,
        admin_routes.create_tournament_from_operations,
        admin_routes.link_tournament_to_operations,
        admin_routes.update_tournament,
        admin_routes.toggle_tournament,
        admin_routes.delete_tournament,
        admin_routes.admin_tournaments_domain_alignment_run,
        admin_routes.admin_tournaments_domain_alignment_audit,
    ]
    read_handlers.append(admin_routes.admin_tournaments_domain_alignment)
    for handler in read_handlers + mutation_handlers:
        assert (
            _dependency(handler, "current_empleado")
            is admin_routes.require_tournament_admin
        )
    for handler in mutation_handlers:
        assert (
            _dependency(handler, "_csrf") is admin_routes.require_tournament_admin_csrf
        )


def test_legacy_create_handlers_are_quarantined_before_any_database_call() -> None:
    tree = ast.parse(inspect.getsource(admin_routes))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("create_tournament", "create_tournament_from_operations"):
        body = functions[name].body
        first_effect = body[1] if isinstance(body[0], ast.Expr) else body[0]
        assert isinstance(first_effect, ast.Raise)


def test_legacy_target_mutations_lock_and_guard_before_write() -> None:
    source = inspect.getsource(admin_routes)
    tree = ast.parse(source)
    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in (
        "link_tournament_to_operations",
        "update_tournament",
        "toggle_tournament",
        "delete_tournament",
    ):
        body = functions[name]
        assert ".with_for_update()" in body
        assert "_require_legacy_gastos_project_mutable" in body
    assert "_require_legacy_gastos_project_mutable" in functions["edit_tournament_form"]


def test_every_rendered_tournament_post_form_contains_csrf_input() -> None:
    source = inspect.getsource(admin_routes.admin_tournaments)
    assert source.count('method="POST"') == 4
    assert source.count("{csrf_input}") == 4
    edit_source = inspect.getsource(admin_routes.edit_tournament_form)
    assert 'method="POST"' in edit_source
    assert "{csrf_input}" in edit_source
    alignment_source = inspect.getsource(
        admin_routes._render_tournaments_domain_alignment_page
    )
    assert alignment_source.count('method="POST"') == 2
    assert alignment_source.count("{csrf_input}") == 2


@pytest.mark.asyncio
async def test_edit_page_escapes_persisted_project_values(monkeypatch) -> None:
    project = SimpleNamespace(
        id=TARGET_ID,
        name='"><script>alert(1)</script>',
        description="<img src=x onerror=alert(2)>",
        display_order=1,
        cuenta_contable_relacionada='"><svg onload=alert(3)>',
        etapas=[],
        categorias=[],
        form_visibility_areas=[],
        active=True,
    )

    async def _allow(_session, _target):
        return None

    monkeypatch.setattr(
        admin_routes,
        "require_ungoverned_gastos_project",
        _allow,
    )
    response = await admin_routes.edit_tournament_form(
        tournament_id=TARGET_ID,
        request=_Request(),
        current_empleado=_employee("admin"),
        session=_Session(row=project),
    )
    html = response.body.decode("utf-8")
    assert "<script>" not in html and "<img src=x" not in html and "<svg" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html
