from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from devnous.gastos.routes import client_reporting_routes as routes
from samchat.client_executive import service as executive
from samchat.client_reporting import service as reporting


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["admin.finanzas", "admin.presupuestos"])
async def test_draft_generation_respects_specific_source_denials(monkeypatch, denied):
    from devnous.gastos.routes import client_executive_routes

    actor = SimpleNamespace(id="actor", rol="coordinador", activo=True)
    monkeypatch.setattr(
        routes,
        "_require_direction_reporting_scope",
        AsyncMock(return_value=["portfolio"]),
    )

    async def decision(_session, _actor, tool, _action):
        return False if tool == denied else None

    monkeypatch.setattr(client_executive_routes, "explicit_tool_decision", decision)

    class Result:
        def first(self):
            return SimpleNamespace(portfolio_id="portfolio")

    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result()), commit=AsyncMock()
    )
    reader = AsyncMock(return_value={"cards": [], "scope": "portfolio"})
    monkeypatch.setattr(reporting, "build_portfolio_dashboard", reader)
    save = AsyncMock()
    monkeypatch.setattr(routes, "save_draft", save)
    await routes.direction_report_draft_create(
        schedule_id="schedule",
        portfolio_id="untrusted",
        session=session,
        current_empleado=actor,
    )
    assert reader.call_args.kwargs["accounting_access"] is False
    assert reader.call_args.kwargs["portfolio_id"] == "portfolio"
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_draft_defaults_to_no_accounting_without_verified_actor_access(
    monkeypatch,
):
    reader = AsyncMock(return_value={"cards": [], "scope": "portfolio"})
    monkeypatch.setattr(reporting, "build_portfolio_dashboard", reader)
    await reporting.build_report_draft(object(), portfolio_id="p", edition_year=2026)
    assert reader.call_args.kwargs["accounting_access"] is False


@pytest.mark.asyncio
async def test_portfolio_denial_never_calls_historical_accounting(monkeypatch):
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=[SimpleNamespace(id="t", name="Torneo", slug="")]
        )
    )
    monkeypatch.setattr(
        executive,
        "_build_direction_budget_snapshot",
        AsyncMock(return_value={"source": "budget_db", "summary": {}}),
    )
    reader = AsyncMock(side_effect=AssertionError("denied"))
    monkeypatch.setattr(executive, "build_tournament_accounting", reader)
    result = await executive.build_portfolio_dashboard(
        session, portfolio_id="p", edition_year=2026, accounting_access=False
    )
    assert result["cards"][0]["accounting"]["source_status"] == "unavailable"
    reader.assert_not_awaited()


def test_stored_draft_redaction_preserves_snapshot_and_blocks_new_source_fields():
    snapshot = {
        "cards": [
            {
                "tournament_id": "t",
                "budget": 100,
                "accounting": {"expense": 123},
                "accounting_previous_year": {"expense": 456},
            }
        ]
    }
    before = deepcopy(snapshot)
    filtered = reporting.filter_report_accounting(snapshot, accounting_access=False)
    assert "accounting" not in filtered["cards"][0]
    assert "accounting_previous_year" not in filtered["cards"][0]
    assert filtered["cards"][0]["budget"] == 100
    assert snapshot == before
    assert (
        reporting.filter_report_accounting(snapshot, accounting_access=True) is snapshot
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_management_route_respects_current_source_access(monkeypatch, allowed):
    monkeypatch.setattr(
        routes, "_require_direction_reporting_scope", AsyncMock(return_value=["p"])
    )
    monkeypatch.setattr(
        routes,
        "_direction_source_access",
        AsyncMock(return_value={"finance": allowed, "budget": True}),
    )
    row = SimpleNamespace(
        id="draft",
        state="published",
        generated_at="today",
        label="P",
        summary={},
        snapshot={
            "cards": [
                {
                    "accounting": {"proof": "private-proof"},
                    "accounting_previous_year": {"proof": "old-proof"},
                }
            ]
        },
    )
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[row], [], []]))
    response = await routes.direction_report_management(
        session=session, current_empleado=SimpleNamespace(id="actor")
    )
    html = response.body.decode()
    assert ("private-proof" in html) is allowed
    assert ("old-proof" in html) is allowed
