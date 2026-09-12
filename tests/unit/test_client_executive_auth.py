from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from devnous.gastos.routes import client_executive_routes, client_reporting_routes
from devnous.gastos.routes.client_executive_routes import (
    _assigned_direction_portfolios,
    _render_dashboard,
)
from devnous.gastos.schema_guard import REQUIRED_COLUMNS, SCHEMA_PATCHES
from devnous.gastos.services.access_control_service import (
    AccessControlLookupError,
    default_allows,
    explicit_tool_decision,
)
from samchat.client_executive.service import DIRECTION_POSITION_KEYS


def test_direction_position_keys_are_the_approved_internal_positions():
    assert DIRECTION_POSITION_KEYS == {
        "direccion_general",
        "direccion_administracion_finanzas",
        "direccion_goat",
        "director_operaciones",
    }


@pytest.mark.asyncio
async def test_authorized_internal_position_holder_enters(monkeypatch):
    async def portfolios(*_args, **_kwargs):
        return ["portfolio-a"]

    async def no_rule(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )
    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", no_rule)
    result = await _assigned_direction_portfolios(
        object(), SimpleNamespace(id="goat-holder", rol="coordinador", activo=True)
    )
    assert result == ["portfolio-a"]


@pytest.mark.asyncio
async def test_employee_without_position_receives_403(monkeypatch):
    async def portfolios(*_args, **_kwargs):
        return []

    async def no_rule(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )
    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", no_rule)
    with pytest.raises(HTTPException) as excinfo:
        await _assigned_direction_portfolios(
            object(), SimpleNamespace(id="employee", rol="empleado", activo=True)
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_without_position_receives_403(monkeypatch):
    async def portfolios(*_args, **_kwargs):
        return []

    async def no_rule(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )
    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", no_rule)
    with pytest.raises(HTTPException) as excinfo:
        await _assigned_direction_portfolios(
            object(), SimpleNamespace(id="admin", rol="admin", activo=True)
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_supervises_active_portfolios(monkeypatch):
    captured = {}

    async def portfolios(_session, _employee_id, *, is_superadmin):
        captured["is_superadmin"] = is_superadmin
        return ["portfolio-a", "portfolio-b"]

    async def no_rule(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )
    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", no_rule)
    assert await _assigned_direction_portfolios(
        object(), SimpleNamespace(id="super", rol="superadmin", activo=True)
    ) == ["portfolio-a", "portfolio-b"]
    assert captured["is_superadmin"] is True


@pytest.mark.asyncio
async def test_cliente_role_is_not_an_authorization_dependency(monkeypatch):
    async def portfolios(*_args, **_kwargs):
        return []

    async def no_rule(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )
    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", no_rule)
    with pytest.raises(HTTPException) as excinfo:
        await _assigned_direction_portfolios(
            object(), SimpleNamespace(id="legacy-role", rol="cliente", activo=True)
        )
    assert excinfo.value.status_code == 403
    assert default_allows("direccion.tableros_ejecutivos", "cliente") is False
    assert default_allows("direccion.tableros_ejecutivos", "admin") is False


@pytest.mark.asyncio
async def test_explicit_denial_blocks_superadmin(monkeypatch):
    async def denied(*_args, **_kwargs):
        return False

    async def unexpected_portfolios(*_args, **_kwargs):
        raise AssertionError("Denied superadmin must not resolve scope.")

    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", denied)
    monkeypatch.setattr(
        client_executive_routes,
        "authorized_direction_portfolio_ids",
        unexpected_portfolios,
    )
    with pytest.raises(HTTPException) as excinfo:
        await _assigned_direction_portfolios(
            object(), SimpleNamespace(id="super", rol="superadmin", activo=True)
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_permission_lookup_error_fails_closed_with_403(monkeypatch):
    async def failed_lookup(*_args, **_kwargs):
        raise AccessControlLookupError("database unavailable")

    monkeypatch.setattr(
        client_executive_routes, "explicit_tool_decision", failed_lookup
    )
    with pytest.raises(HTTPException) as excinfo:
        await _assigned_direction_portfolios(
            object(), SimpleNamespace(id="holder", rol="coordinador", activo=True)
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_explicit_permission_query_failure_is_not_treated_as_no_rule():
    class BrokenSession:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    with pytest.raises(AccessControlLookupError):
        await explicit_tool_decision(
            BrokenSession(),
            SimpleNamespace(id="holder", rol="coordinador", activo=True),
            "direccion.tableros_ejecutivos",
        )


def test_direction_dashboard_link_keeps_selected_edition_year():
    html = _render_dashboard(
        {
            "edition_year": 2028,
            "cards": [
                {
                    "tournament_id": "t-1",
                    "tournament_name": "Torneo",
                    "budget": 0,
                    "actual": 0,
                    "committed": 0,
                    "projected": 0,
                    "source": "test",
                    "as_of": "now",
                }
            ],
        }
    )
    assert "/direccion/tableros/torneos/t-1?edition_year=2028" in html
    assert "Tablero ejecutivo" in html
    assert "/cliente/" not in html


def test_direction_dashboard_renders_requested_executive_sections_and_contrast():
    html = _render_dashboard(
        {
            "edition_year": 2026,
            "cards": [
                {
                    "tournament_id": "t-1",
                    "tournament_name": "Copa Telmex",
                    "budget": 100,
                    "actual": 25,
                    "committed": 40,
                    "projected": 90,
                    "source": "test",
                    "as_of": "2026-09-12",
                    "dossier": {
                        "source_status": "available",
                        "entities": [],
                        "national_phase": {"status": "pending_data"},
                        "marketing": {"media": {}},
                    },
                }
            ],
        }
    )

    for label in (
        "Responsables, equipos, jugadores y avance",
        "Operación y finanzas de finales",
        "Activaciones y evidencia",
        "Reportes publicados",
        "Presupuesto",
        "Ejercido",
    ):
        assert label in html
    assert "color:#fff" in html
    assert "prefers-color-scheme:dark" in html
    assert "no están disponibles en esta superficie" not in html


def test_schema_guard_creates_position_dependencies_before_legacy_portfolios():
    patch_names = [name for name, _sql in SCHEMA_PATCHES]
    assert patch_names.index(
        "create_authorization_positions_table"
    ) < patch_names.index("create_client_executive_portfolio_positions_table")


def test_schema_guard_requires_legacy_reporting_tables_without_renaming_them():
    required = {(item.table, item.column) for item in REQUIRED_COLUMNS}
    assert ("client_report_drafts", "snapshot") in required


@pytest.mark.asyncio
async def test_published_reports_query_is_limited_to_assigned_portfolios(monkeypatch):
    async def assigned(*_args, **_kwargs):
        return ["portfolio-a"]

    monkeypatch.setattr(
        client_executive_routes, "_assigned_direction_portfolios", assigned
    )

    class Result:
        def __iter__(self):
            return iter([])

    class Session:
        statement = ""
        params = None

        async def execute(self, statement, params=None):
            self.statement = str(statement)
            self.params = params
            return Result()

    session = Session()
    await client_executive_routes.direction_published_reports(
        session=session,
        current_empleado=SimpleNamespace(rol="coordinador", id="holder"),
    )
    assert "d.portfolio_id = ANY(:portfolio_ids)" in session.statement
    assert session.params == {"portfolio_ids": ["portfolio-a"]}


@pytest.mark.asyncio
async def test_report_draft_uses_in_scope_schedule_portfolio_not_form_value(
    monkeypatch,
):
    captured = {}

    async def required_scope(*_args, **_kwargs):
        return ["canonical-portfolio"]

    async def build(_session, *, portfolio_id, edition_year):
        captured["build_portfolio_id"] = portfolio_id
        return {"edition_year": edition_year, "snapshot": {}, "summary": {}}

    async def save(_session, *, schedule_id, portfolio_id, draft, actor_id):
        captured["save"] = (schedule_id, portfolio_id, actor_id)

    monkeypatch.setattr(
        client_reporting_routes, "_require_direction_reporting_scope", required_scope
    )
    monkeypatch.setattr(client_reporting_routes, "build_report_draft", build)
    monkeypatch.setattr(client_reporting_routes, "save_draft", save)

    class Result:
        def first(self):
            return SimpleNamespace(portfolio_id="canonical-portfolio")

    class Session:
        async def execute(self, _statement, _params=None):
            return Result()

        async def commit(self):
            return None

    response = await client_reporting_routes.direction_report_draft_create(
        schedule_id="schedule",
        portfolio_id="forged-portfolio",
        session=Session(),
        current_empleado=SimpleNamespace(rol="admin", id="actor"),
    )

    assert response.status_code == 303
    assert captured["build_portfolio_id"] == "canonical-portfolio"
    assert captured["save"] == ("schedule", "canonical-portfolio", "actor")


@pytest.mark.asyncio
async def test_read_permission_does_not_authorize_report_writes(monkeypatch):
    captured = []

    async def decision(_session, _employee, _tool, action_key):
        captured.append(action_key)
        return True if action_key == "ver" else None

    async def portfolios(*_args, **_kwargs):
        return ["portfolio-a"]

    async def unexpected_schedule(*_args, **_kwargs):
        raise AssertionError("Read authority must not invoke a write.")

    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", decision)
    monkeypatch.setattr(
        client_executive_routes, "authorized_direction_portfolio_ids", portfolios
    )
    monkeypatch.setattr(client_reporting_routes, "create_schedule", unexpected_schedule)

    with pytest.raises(HTTPException) as excinfo:
        await client_reporting_routes.direction_report_schedule_create(
            portfolio_id="portfolio-a",
            frequency="weekly",
            session=object(),
            current_empleado=SimpleNamespace(
                id="holder", rol="coordinador", activo=True
            ),
        )
    assert excinfo.value.status_code == 403
    assert captured == ["editar"]


@pytest.mark.asyncio
async def test_report_transition_is_internal_and_uses_audited_actor(monkeypatch):
    captured = {}

    async def required_scope(*_args, **_kwargs):
        return ["portfolio-a"]

    async def transition(_session, *, draft_id, target, actor_id):
        captured.update(draft_id=draft_id, target=target, actor_id=actor_id)

    monkeypatch.setattr(
        client_reporting_routes, "_require_direction_reporting_scope", required_scope
    )
    monkeypatch.setattr(client_reporting_routes, "transition_draft", transition)

    class Result:
        def first(self):
            return SimpleNamespace(portfolio_id="portfolio-a")

    class Session:
        async def execute(self, _statement, _params=None):
            return Result()

        async def commit(self):
            return None

    response = await client_reporting_routes.direction_report_draft_transition(
        draft_id="draft-a",
        target="reviewed",
        session=Session(),
        current_empleado=SimpleNamespace(rol="coordinador", id="internal-actor"),
    )
    assert response.status_code == 303
    assert captured == {
        "draft_id": "draft-a",
        "target": "reviewed",
        "actor_id": "internal-actor",
    }


@pytest.mark.asyncio
async def test_legacy_reads_preserve_multiple_query_parameters():
    def request(path: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": path,
                "query_string": b"edition_year=2028&view=compact",
                "headers": [],
            }
        )

    dashboard = await client_executive_routes.legacy_client_dashboards_redirect(
        request("/cliente/tableros")
    )
    summary = await client_executive_routes.legacy_client_dashboard_summary_redirect(
        request("/cliente/tableros/asistente/resumen")
    )
    reports = await client_executive_routes.legacy_client_reports_redirect(
        request("/cliente/reportes")
    )
    tournament = await client_executive_routes.legacy_client_tournament_redirect(
        "t-1", request("/cliente/tableros/torneos/t-1")
    )
    management = await client_reporting_routes.legacy_client_report_management_redirect(
        request("/admin/reportes-cliente")
    )
    responses = [dashboard, summary, reports, tournament, management]
    assert all(response.status_code == 307 for response in responses)
    assert (
        dashboard.headers["location"]
        == "/direccion/tableros?edition_year=2028&view=compact"
    )
    assert summary.headers["location"].endswith("edition_year=2028&view=compact")
    assert (
        reports.headers["location"]
        == "/direccion/reportes?edition_year=2028&view=compact"
    )
    assert tournament.headers["location"].startswith("/direccion/tableros/torneos/t-1?")
    assert management.headers["location"].endswith("edition_year=2028&view=compact")
