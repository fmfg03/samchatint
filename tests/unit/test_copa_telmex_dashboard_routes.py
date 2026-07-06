import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

import copa_telmex_dashboard as dashboard


class _FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MissingTeamDB:
    def __init__(self, session):
        self.session = session

    async def get_team_by_id(self, team_id):
        return None


class _FakeRequest:
    def __init__(self, session=None, form_data=None):
        self.session = session or {}
        self._form_data = form_data or {}

    async def form(self):
        return self._form_data


class _ScalarNoneResult:
    def scalar_one_or_none(self):
        return None


class _MissingPlayerSession:
    async def execute(self, statement):
        return _ScalarNoneResult()


class _MissingPlayerSessionContext:
    async def __aenter__(self):
        return _MissingPlayerSession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_view_team_preserves_not_found_http_exception(monkeypatch):
    monkeypatch.setattr(dashboard, "async_session_maker", lambda: _FakeSessionContext())
    monkeypatch.setattr(dashboard, "CopaTelmexDB", _MissingTeamDB)
    request = _FakeRequest(session={"empleado_id": "admin-1", "rol": "admin"})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dashboard.view_team(request, str(uuid4())))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Team not found"


TEAM_PLAYER_MUTATION_ROUTES = [
    lambda request, object_id: dashboard.edit_team(object_id, request),
    lambda request, object_id: dashboard.edit_player(object_id, request),
    lambda request, object_id: dashboard.verify_player(object_id, request),
    lambda request, object_id: dashboard.delete_player(object_id, request),
    lambda request, object_id: dashboard.delete_team(object_id, request),
]


@pytest.mark.parametrize("route_call", TEAM_PLAYER_MUTATION_ROUTES)
def test_team_player_mutations_reject_anonymous_session(route_call, monkeypatch):
    def fail_session_maker():
        raise AssertionError("mutation route reached DB before auth")

    monkeypatch.setattr(dashboard, "async_session_maker", fail_session_maker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(route_call(_FakeRequest(), str(uuid4())))

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize("route_call", TEAM_PLAYER_MUTATION_ROUTES)
def test_team_player_mutations_reject_low_privilege_session(route_call, monkeypatch):
    def fail_session_maker():
        raise AssertionError("mutation route reached DB before auth")

    monkeypatch.setattr(dashboard, "async_session_maker", fail_session_maker)
    request = _FakeRequest(session={"empleado_id": "emp-1", "rol": "empleado"})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(route_call(request, str(uuid4())))

    assert exc_info.value.status_code == 403


AUTHORIZED_TEAM_PLAYER_MUTATION_ROUTES = [
    (
        lambda request, object_id: dashboard.edit_team(object_id, request),
        "Team not found",
    ),
    (
        lambda request, object_id: dashboard.edit_player(object_id, request),
        "Player not found",
    ),
    (
        lambda request, object_id: dashboard.verify_player(object_id, request),
        "Player not found",
    ),
    (
        lambda request, object_id: dashboard.delete_player(object_id, request),
        "Player not found",
    ),
    (
        lambda request, object_id: dashboard.delete_team(object_id, request),
        "Team not found",
    ),
]


@pytest.mark.parametrize("route_call, expected_detail", AUTHORIZED_TEAM_PLAYER_MUTATION_ROUTES)
def test_team_player_mutations_allow_authorized_session_to_reach_handler_path(
    route_call,
    expected_detail,
    monkeypatch,
):
    monkeypatch.setattr(dashboard, "CopaTelmexDB", _MissingTeamDB)
    monkeypatch.setattr(
        dashboard,
        "async_session_maker",
        lambda: _MissingPlayerSessionContext(),
    )
    request = _FakeRequest(
        session={"empleado_id": "admin-1", "rol": "admin"},
        form_data={"name": "Equipo", "first_name": "Ana"},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(route_call(request, str(uuid4())))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == expected_detail


class _FakeBeginContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return _FakeBeginContext(self.conn)


class _FakeConn:
    def __init__(self):
        self.calls = []

    async def execute(self, statement):
        self.calls.append(str(statement))


class _ExplodingDB:
    def __init__(self, session):
        self.session = session

    async def get_registration_stats(self):
        raise RuntimeError("postgres://user:pass@db/private?curp=SENSITIVE")


def test_internal_server_error_uses_generic_detail():
    exc = dashboard._internal_server_error()

    assert exc.status_code == 500
    assert exc.detail == "Internal server error"


def test_legacy_dashboard_does_not_expose_internal_exception(monkeypatch):
    monkeypatch.setattr(dashboard, "async_session_maker", lambda: _FakeSessionContext())
    monkeypatch.setattr(dashboard, "CopaTelmexDB", _ExplodingDB)
    request = _FakeRequest(session={"empleado_id": "admin-1", "rol": "admin"})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dashboard.home(request))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"
    assert "postgres://user:pass" not in exc_info.value.detail
    assert "SENSITIVE" not in exc_info.value.detail


def test_healthz_returns_healthy_payload():
    response = asyncio.run(dashboard.healthz())
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["service"] == "samchat-gastos"


def test_readyz_returns_200_when_schema_health_is_ok(monkeypatch):
    conn = _FakeConn()

    async def fake_check_schema_health(_conn):
        return {"ok": True, "missing_tables": [], "missing_columns": [], "missing_indexes": []}

    monkeypatch.setattr(dashboard, "db_engine", _FakeEngine(conn))
    monkeypatch.setattr(dashboard, "check_schema_health", fake_check_schema_health)

    response = asyncio.run(dashboard.readyz())
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert any("SELECT 1" in call for call in conn.calls)


def test_readyz_returns_503_when_schema_health_is_not_ok(monkeypatch):
    conn = _FakeConn()

    async def fake_check_schema_health(_conn):
        return {"ok": False, "missing_tables": ["empleados"]}

    monkeypatch.setattr(dashboard, "db_engine", _FakeEngine(conn))
    monkeypatch.setattr(dashboard, "check_schema_health", fake_check_schema_health)

    response = asyncio.run(dashboard.readyz())
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 503
    assert payload["ok"] is False
    assert payload["status"] == "degraded"


def test_production_missing_database_url_fails_fast(monkeypatch):
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard._require_database_url_for_runtime()

    assert "DATABASE_URL" in str(exc_info.value)


def test_dev_missing_database_url_keeps_local_fallback(monkeypatch):
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    db_url = dashboard._require_database_url_for_runtime()

    assert db_url.startswith("postgresql+asyncpg://")
