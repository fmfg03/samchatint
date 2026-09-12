from types import SimpleNamespace

import pytest

from samchat.budgets import service as budget_service
from samchat.client_executive import service, ui


@pytest.mark.asyncio
async def test_portfolio_dashboard_only_aggregates_assigned_tournaments(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        assert is_superadmin is False
        return [{"id": "allowed", "name": "Proyecto autorizado", "slug": "allowed"}]

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
        object(), empleado_id="direction-holder", edition_year=2026
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
async def test_dashboard_can_attach_tournament_scoped_operational_dossier(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        return [{"id": "allowed", "name": "Torneo", "slug": "torneo-2026"}]

    async def budget(*_args, **_kwargs):
        return {"summary": {}, "comparison": {}, "forecast": {}}

    async def dossier(tournament, *, edition_year):
        assert tournament["id"] == "allowed"
        assert tournament["slug"] == "torneo-2026"
        assert edition_year == 2026
        return {"source_status": "available", "entities": [{"entity_name": "CDMX"}]}

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)
    monkeypatch.setattr(service, "build_budget_snapshot", budget)
    monkeypatch.setattr(service, "_build_operational_dossier", dossier)

    payload = await service.build_client_dashboard(
        object(),
        empleado_id="direction-holder",
        edition_year=2026,
        include_operational_detail=True,
    )

    assert payload["cards"][0]["dossier"]["entities"][0]["entity_name"] == "CDMX"
    assert payload["data_boundary"]["writes"] is False


@pytest.mark.asyncio
async def test_operational_source_failure_is_explicit_and_does_not_expand_scope(
    monkeypatch,
):
    async def unavailable(**_kwargs):
        raise service.TournamentsV2Error("secret connection detail")

    monkeypatch.setattr(service, "build_tournament_soul_snapshot", unavailable)

    dossier = await service._build_operational_dossier(
        {"id": "allowed", "name": "Torneo", "slug": "torneo-2026"},
        edition_year=2026,
    )

    assert dossier["source_status"] == "unavailable"
    assert dossier["entities"] == []
    assert "secret connection detail" not in str(dossier)


@pytest.mark.asyncio
async def test_operational_dossier_uses_authorized_uuid_not_display_name(monkeypatch):
    async def snapshot(**kwargs):
        assert kwargs["tournament_slug"] == "authorized-uuid"
        return {
            "tournaments": [{"id": "authorized-uuid", "start_date": "2026-01-01"}],
            "soul": {"national_phase": {}, "marketing": {}},
        }

    monkeypatch.setattr(service, "build_tournament_soul_snapshot", snapshot)
    monkeypatch.setattr(
        service,
        "build_director_general_entity_dossier",
        lambda _snapshot: {"entities": []},
    )

    dossier = await service._build_operational_dossier(
        {
            "id": "authorized-uuid",
            "name": "Liga Telmex Telcel de Béisbol",
            "slug": "",
        },
        edition_year=2026,
    )

    assert dossier["source_status"] == "available"


@pytest.mark.asyncio
async def test_operational_dossier_fails_closed_for_another_or_unknown_edition(
    monkeypatch,
):
    async def snapshot(**_kwargs):
        return {
            "tournaments": [{"id": "authorized-uuid", "start_date": "2026-01-01"}],
            "soul": {"national_phase": {"matches": [{"secret": "2026"}]}},
        }

    def unexpected_dossier(_snapshot):
        raise AssertionError("Another edition must not reach the dossier renderer.")

    monkeypatch.setattr(service, "build_tournament_soul_snapshot", snapshot)
    monkeypatch.setattr(
        service, "build_director_general_entity_dossier", unexpected_dossier
    )

    dossier = await service._build_operational_dossier(
        {"id": "authorized-uuid", "name": "Torneo", "slug": ""},
        edition_year=2024,
    )

    assert dossier["source_status"] == "edition_unavailable"
    assert dossier["entities"] == []
    assert "2026" not in str(dossier)


def test_unavailable_marketing_never_renders_missing_counts_as_zero():
    rendered = ui._marketing(
        {"marketing": {"status": "unavailable", "media": {}}},
        0,
    )

    assert 'class="status status-unavailable"' in rendered
    assert "<h4>Fotografías</h4><p>Fuente no disponible</p>" in rendered
    assert "<h4>Videos</h4><p>Fuente no disponible</p>" in rendered


def test_tournament_section_ids_are_unique():
    first = ui._tournament({"dossier": {}}, 2026, 0)
    second = ui._tournament({"dossier": {}}, 2026, 1)

    assert 'id="fase-nacional-0"' in first
    assert 'id="mercadotecnia-0"' in first
    assert 'id="fase-nacional-1"' in second
    assert 'id="mercadotecnia-1"' in second


@pytest.mark.asyncio
async def test_tournament_outside_assigned_portfolio_is_denied(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        return [{"id": "allowed", "name": "Proyecto autorizado", "slug": "allowed"}]

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)

    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(
            object(),
            empleado_id="direction-holder",
            edition_year=2026,
            tournament_id="other",
        )


@pytest.mark.asyncio
async def test_internal_identity_without_active_portfolio_position_is_denied(
    monkeypatch,
):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        assert is_superadmin is False
        return []

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)

    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(
            object(), empleado_id="employee", edition_year=2026
        )


@pytest.mark.asyncio
async def test_unscoped_tournament_never_requests_budget_snapshot(monkeypatch):
    async def authorized(_session, _empleado_id, *, is_superadmin=False):
        return [{"id": "allowed", "name": "", "slug": ""}]

    async def unexpected_snapshot(*_args, **_kwargs):
        raise AssertionError("A blank tournament selector must not load an artifact.")

    monkeypatch.setattr(service, "_authorized_tournaments", authorized)
    monkeypatch.setattr(service, "build_budget_snapshot", unexpected_snapshot)

    payload = await service.build_client_dashboard(
        object(), empleado_id="direction-holder", edition_year=2026
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
    assert await service._authorized_tournaments(session, "direction-holder") == []
    assert len(session.statements) == 1
    assert "authorization_position_assignments" in session.statements[0]
    assert "holder.position_key = ANY(:position_keys)" in session.statements[0]
    assert "CREATE" not in session.statements[0].upper()


@pytest.mark.asyncio
async def test_authorized_tournaments_does_not_require_a_tournament_slug_column():
    class Result:
        def __iter__(self):
            return iter(
                [SimpleNamespace(id="t-1", name="Copa Telmex Telcel", slug=None)]
            )

    class Session:
        async def execute(self, statement, _params=None):
            rendered = str(statement)
            assert "t.slug" not in rendered
            assert "NULL::text AS slug" in rendered
            return Result()

    assert await service._authorized_tournaments(Session(), "direction-holder") == [
        {"id": "t-1", "name": "Copa Telmex Telcel", "slug": ""}
    ]


@pytest.mark.asyncio
async def test_superadmin_reads_all_tournaments_without_a_portfolio_position():
    class Result:
        def __iter__(self):
            return iter([])

    class Session:
        async def execute(self, statement, _params=None):
            rendered = str(statement)
            assert "client_executive_portfolio_tournaments assignment" in rendered
            assert "portfolio.active = TRUE" in rendered
            assert "assignment.active = TRUE" in rendered
            assert "t.active = TRUE" in rendered
            return Result()

    assert (
        await service._authorized_tournaments(Session(), "super", is_superadmin=True)
        == []
    )


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
    monkeypatch.setattr(
        budget_service, "load_budget_artifact_rows", unexpected_artifact
    )
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
