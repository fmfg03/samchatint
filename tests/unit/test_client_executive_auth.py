from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import client_reporting_routes
from devnous.gastos.routes.client_executive_routes import (
    _render_dashboard,
    _require_client,
    client_published_reports,
)
from devnous.gastos.schema_guard import SCHEMA_PATCHES
from devnous.gastos.services.access_control_service import default_allows
from samchat.assistant.router import _derive_empleado_role


def test_supabase_customer_maps_to_client_not_finance():
    assert _derive_empleado_role(user_payload={}, supabase_roles=["customer"]) == "cliente"


def test_supabase_finance_role_remains_finance():
    assert _derive_empleado_role(user_payload={}, supabase_roles=["finanzas"]) == "finanzas"


def test_client_dashboard_authorization_allows_client_and_superadmin_only():
    _require_client(SimpleNamespace(rol="cliente"))
    _require_client(SimpleNamespace(rol="superadmin"))
    with pytest.raises(HTTPException, match="Client executive access required"):
        _require_client(SimpleNamespace(rol="finanzas"))


def test_client_role_has_only_the_client_dashboard_default_tool():
    assert default_allows("cliente.tableros_ejecutivos", "cliente") is True
    assert default_allows("panel.home", "cliente") is False
    assert default_allows("gastos.informes", "cliente") is False


def test_client_dashboard_link_keeps_selected_edition_year():
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
    assert "/cliente/tableros/torneos/t-1?edition_year=2028" in html


def test_schema_guard_creates_position_dependencies_before_client_portfolios():
    patch_names = [name for name, _sql in SCHEMA_PATCHES]
    assert patch_names.index("create_authorization_positions_table") < patch_names.index(
        "create_client_executive_portfolio_positions_table"
    )


@pytest.mark.asyncio
async def test_client_published_reports_requires_an_active_portfolio():
    class Result:
        def __iter__(self):
            return iter([])

    class Session:
        statement = ""

        async def execute(self, statement, _params=None):
            self.statement = str(statement)
            return Result()

    session = Session()
    await client_published_reports(
        session=session, current_empleado=SimpleNamespace(rol="cliente", id="client")
    )
    assert "p.active = TRUE" in session.statement


@pytest.mark.asyncio
async def test_draft_route_uses_schedule_portfolio_not_form_value(monkeypatch):
    captured = {}

    async def build(_session, *, portfolio_id, edition_year):
        captured["build_portfolio_id"] = portfolio_id
        return {"edition_year": edition_year, "snapshot": {}, "summary": {}}

    async def save(_session, *, schedule_id, portfolio_id, draft, actor_id):
        captured["save"] = (schedule_id, portfolio_id, actor_id)

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

    response = await client_reporting_routes.client_report_draft_create(
        schedule_id="schedule",
        portfolio_id="forged-portfolio",
        session=Session(),
        current_empleado=SimpleNamespace(rol="admin", id="actor"),
    )

    assert response.status_code == 303
    assert captured["build_portfolio_id"] == "canonical-portfolio"
    assert captured["save"] == ("schedule", "canonical-portfolio", "actor")
