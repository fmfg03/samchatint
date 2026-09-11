import pytest

from samchat.budgets import service as budget_service
from samchat.client_executive import service


@pytest.mark.asyncio
async def test_portfolio_dashboard_only_aggregates_assigned_tournaments(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        assert is_superadmin is False
        return [
            {"id": "allowed", "name": "Proyecto autorizado", "slug": "allowed"}
        ]

    async def snapshot(
        _session,
        *,
        tournament_id,
        tournament_name,
        tournament_slug,
        edition_year,
        ensure_schema,
        strict_tournament_scope,
    ):
        assert tournament_id == "allowed"
        assert tournament_name == "Proyecto autorizado"
        assert tournament_slug == "allowed"
        assert edition_year == 2026
        assert ensure_schema is False
        assert strict_tournament_scope is True
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
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        return [
            {"id": "allowed", "name": "Proyecto autorizado", "slug": "allowed"}
        ]

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)

    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(
            object(), empleado_id="client", edition_year=2026, tournament_id="other"
        )


@pytest.mark.asyncio
async def test_client_without_active_portfolio_position_is_denied(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        assert is_superadmin is False
        return []

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)

    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(object(), empleado_id="client", edition_year=2026)


@pytest.mark.asyncio
async def test_unscoped_tournament_never_requests_budget_snapshot(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        return [{"id": "allowed", "name": "", "slug": ""}]

    async def unexpected_snapshot(*_args, **_kwargs):
        raise AssertionError("A blank tournament selector must not load an artifact.")

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)
    monkeypatch.setattr(service, "build_budget_snapshot", unexpected_snapshot)

    payload = await service.build_client_dashboard(
        object(), empleado_id="client", edition_year=2026
    )

    assert payload["cards"] == []


@pytest.mark.asyncio
async def test_authorized_tournaments_performs_only_the_position_scope_query():
    class Result:
        def __iter__(self):
            return iter([])

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement, _params=None):
            self.statements.append(str(statement))
            return Result()

    session = Session()
    assert await service._authorized_tournaments(session, "client") == []
    assert len(session.statements) == 1
    assert "authorization_position_assignments" in session.statements[0]
    assert "CREATE" not in session.statements[0].upper()


@pytest.mark.asyncio
async def test_superadmin_reads_all_tournaments_without_a_portfolio_position():
    class Result:
        def __iter__(self):
            return iter([])

    class Session:
        async def execute(self, statement, _params=None):
            assert "FROM tournaments" in str(statement)
            return Result()

    assert await service._authorized_tournaments(Session(), "super", is_superadmin=True) == []


@pytest.mark.asyncio
async def test_portfolio_dashboard_excludes_inactive_tournaments():
    class Result:
        def __iter__(self):
            return iter([])

    class Session:
        statement = ""

        async def execute(self, statement, _params=None):
            self.statement = str(statement)
            return Result()

    session = Session()
    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_portfolio_dashboard(
            session, portfolio_id="portfolio", edition_year=2026
        )
    assert "t.active = TRUE" in session.statement


@pytest.mark.asyncio
async def test_budget_version_selection_can_skip_schema_setup(monkeypatch):
    async def versions(_session, *, edition_year, ensure_schema):
        assert edition_year == 2026
        assert ensure_schema is False
        return []

    monkeypatch.setattr(budget_service, "list_budget_versions", versions)

    assert (
        await budget_service._select_budget_version(
            object(), edition_year=2026, ensure_schema=False
        )
        is None
    )


@pytest.mark.asyncio
async def test_strict_tournament_scope_uses_only_uuid_and_never_falls_back(monkeypatch):
    async def selected_version(*_args, **_kwargs):
        return {
            "id": "version",
            "version_name": "Test",
            "status": "approved",
            "source": "test",
            "artifact_path": None,
            "edition_year": 2026,
        }

    class EmptyRows:
        def mappings(self):
            return self

        def all(self):
            return []

    class Session:
        def __init__(self):
            self.statement = ""

        async def execute(self, statement, _params=None):
            self.statement = str(statement)
            return EmptyRows()

    def unexpected_artifact():
        raise AssertionError("Strict client scope must not read the CSV fallback.")

    monkeypatch.setattr(budget_service, "_select_budget_version", selected_version)
    monkeypatch.setattr(budget_service, "load_budget_artifact_rows", unexpected_artifact)
    session = Session()

    snapshot = await budget_service.build_budget_snapshot(
        session,
        tournament_id="only-this-id",
        tournament_name="Possibly shared alias",
        tournament_slug="shared",
        edition_year=2026,
        ensure_schema=False,
        strict_tournament_scope=True,
    )

    assert " OR " not in session.statement
    assert snapshot["source"] == "budget_scope_unavailable"
    assert snapshot["summary"]["budget_total"] == 0.0
