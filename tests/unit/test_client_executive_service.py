import pytest

from samchat.client_executive import service


@pytest.mark.asyncio
async def test_portfolio_dashboard_only_aggregates_assigned_tournaments(monkeypatch):
    async def authorized(_session, _empleado_id):
        return [{"id": "allowed", "name": "Proyecto autorizado"}]

    async def snapshot(_session, *, tournament_id, edition_year):
        assert tournament_id == "allowed"
        assert edition_year == 2026
        return {
            "summary": {"budget_total": 100},
            "comparison": {"actual_total": 30, "committed_total": 20},
            "forecast": {"projected_total": 80},
            "executive_alerts": [{"severity": "high", "title": "Riesgo"}],
        }

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)
    monkeypatch.setattr(service, "build_budget_snapshot", snapshot)

    payload = await service.build_client_dashboard(
        object(), empleado_id="client", edition_year=2026
    )

    assert payload["scope"] == "portfolio"
    assert payload["cards"] == [
        {
            "tournament_id": "allowed",
            "tournament_name": "Proyecto autorizado",
            "budget": 100.0,
            "actual": 30.0,
            "committed": 20.0,
            "projected": 80.0,
            "alerts": [{"severity": "high", "title": "Riesgo"}],
            "source": "samchat.budgets.service.build_budget_snapshot",
            "as_of": payload["cards"][0]["as_of"],
        }
    ]
    assert "cashflow" in payload["unavailable_metrics"]
    assert service.build_client_executive_summary(payload)["read_only"] is True
    assert service.build_client_executive_summary(payload)["high_alert_count"] == 1


@pytest.mark.asyncio
async def test_tournament_outside_client_portfolio_is_denied(monkeypatch):
    async def authorized(_session, _empleado_id):
        return [{"id": "allowed", "name": "Proyecto autorizado"}]

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)

    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(
            object(), empleado_id="client", edition_year=2026, tournament_id="other"
        )
