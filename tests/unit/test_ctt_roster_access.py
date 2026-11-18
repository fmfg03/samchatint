import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

os.environ.setdefault(
    "SESSION_SECRET_KEY", "ctt-roster-unit-test-key-no-production-access"
)

import copa_telmex_dashboard as dashboard  # noqa: E402

from devnous.copa_telmex import ctt_report_access  # noqa: E402
from devnous.copa_telmex.models import Player, Team  # noqa: E402


@pytest.fixture
def roster(monkeypatch):
    employee_id = uuid4()
    employee = SimpleNamespace(id=employee_id, rol="coordinador")
    monkeypatch.setattr(
        dashboard, "get_current_empleado", AsyncMock(return_value=employee)
    )
    edition = ctt_report_access.CttEditionAccess(
        tournament_id=str(uuid4()),
        edition_year=2026,
        roster_slug="copa-telmex-2026",
        age_cutoff=None,
        state_codes=frozenset({"14"}),
        edit_state_codes=frozenset({"14"}),
    )
    load = AsyncMock(return_value=[edition])
    monkeypatch.setattr(ctt_report_access, "list_ctt_report_editions", load)
    managed = AsyncMock(
        return_value=frozenset({"copa-telmex-2025", "copa-telmex-2026"})
    )
    monkeypatch.setattr(ctt_report_access, "list_managed_ctt_roster_slugs", managed)
    return SimpleNamespace(
        employee_id=employee_id,
        edition=edition,
        load=load,
        managed=managed,
        assigned=Team(id=uuid4(), tournament_slug=edition.roster_slug, state="Jalisco"),
        outside=Team(id=uuid4(), tournament_slug=edition.roster_slug, state="Puebla"),
        other_edition=Team(
            id=uuid4(), tournament_slug="copa-telmex-2025", state="Jalisco"
        ),
        legacy=Team(id=uuid4(), tournament_slug="copa-america", state="Puebla"),
    )


@pytest.mark.asyncio
async def test_global_roster_keeps_legacy_and_limits_ctt_to_assigned_geography(roster):
    session = object()
    result = await dashboard._visible_ctt_roster_teams(
        session,
        object(),
        [roster.outside, roster.legacy, roster.other_edition, roster.assigned],
    )
    assert result == [roster.legacy, roster.assigned]
    roster.load.assert_awaited_once_with(
        session,
        employee_id=str(roster.employee_id),
        role="coordinador",
        roster_slug=None,
    )


@pytest.mark.asyncio
async def test_explicit_state_scope_rejects_out_of_territory_even_when_empty(roster):
    with pytest.raises(HTTPException) as denied:
        await dashboard._visible_ctt_roster_teams(
            object(),
            object(),
            [],
            roster_slug=roster.edition.roster_slug,
            state_code="21",
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_explicit_roster_never_falls_back_before_scope_migration(roster):
    roster.managed.return_value = frozenset()
    with pytest.raises(HTTPException) as unavailable:
        await dashboard._visible_ctt_roster_teams(
            object(), object(), [roster.outside], roster_slug=roster.edition.roster_slug
        )
    assert unavailable.value.status_code == 503


@pytest.mark.asyncio
async def test_global_roster_preserves_unactivated_ctt_edition(roster):
    roster.managed.return_value = frozenset({roster.edition.roster_slug})
    response = await dashboard._visible_ctt_roster_teams(
        object(), object(), [roster.outside, roster.other_edition]
    )
    assert response == [roster.other_edition]


@pytest.mark.parametrize("updates", [{"state": "Puebla"}, {"state": None}])
@pytest.mark.asyncio
async def test_scoped_editor_cannot_move_team_outside_assignment(roster, updates):
    actor = {"user_id": str(roster.employee_id), "role": "coordinador"}
    with pytest.raises(HTTPException) as denied:
        await dashboard._regs07_ctt_scope(object(), actor, roster.assigned, updates)
    assert denied.value.status_code == 403


def test_governed_edit_rejects_missing_and_stale_browser_revisions():
    with pytest.raises(HTTPException) as required:
        dashboard._regs07_check_expected_revision(None, 4, scoped_ctt=True)
    assert required.value.status_code == 428
    with pytest.raises(HTTPException) as stale:
        dashboard._regs07_check_expected_revision(3, 4, scoped_ctt=True)
    assert stale.value.detail["error"] == "STALE_POSTCOMMIT_BASE"
    dashboard._regs07_check_expected_revision(4, 4, scoped_ctt=True)
    dashboard._regs07_check_expected_revision(None, 4, scoped_ctt=False)


def test_legacy_boundary_reserves_the_write_lock_for_the_sql_cas():
    session = SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )
    query = dashboard._regs07_entity_query(session, Team, uuid4())
    assert "FOR UPDATE" not in str(query)

    session.bind = None
    query = dashboard._regs07_entity_query(session, Team, uuid4())
    assert "FOR UPDATE" in str(query)


@pytest.fixture
def authenticated_roster_view(roster, monkeypatch):
    request = SimpleNamespace(
        session={"empleado_id": str(roster.employee_id), "rol": "coordinador"}
    )

    def use_results(items):
        result = SimpleNamespace(all=lambda: items)
        result.scalars = lambda: result
        execute_mock = AsyncMock(return_value=result)

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            execute = staticmethod(execute_mock)

        monkeypatch.setattr(dashboard, "async_session_maker", Session)
        return execute_mock

    def render(template, context, **kwargs):
        return SimpleNamespace(
            template=template, context=context, headers=kwargs.get("headers", {})
        )

    monkeypatch.setattr(dashboard.templates, "TemplateResponse", render)
    return request, use_results


@pytest.mark.parametrize("needs_review", [False, True])
@pytest.mark.asyncio
async def test_players_page_hides_out_of_scope_and_uncaptured_players(
    roster, authenticated_roster_view, needs_review
):
    request, use_results = authenticated_roster_view

    def player(team, state):
        return Player(
            id=uuid4(),
            team_id=team.id,
            first_name="Prueba",
            last_name="Ficticia",
            created_at=datetime(2026, 1, 1),
            governance_state=state,
        )

    visible = player(roster.assigned, "ACTIVE")
    legacy = player(roster.legacy, "PENDING_FINALITY")
    execute = use_results(
        [
            (visible, roster.assigned),
            (player(roster.assigned, "PENDING_FINALITY"), roster.assigned),
            (player(roster.outside, "ACTIVE"), roster.outside),
            (legacy, roster.legacy),
        ]
    )

    page = await dashboard.list_players(request, needs_review=needs_review)

    assert page.template == "players.html"
    assert page.headers["Cache-Control"] == "private, no-store"
    assert [row["id"] for row in page.context["players"]] == [
        str(visible.id),
        str(legacy.id),
    ]
    assert page.context["filter"] == ("needs_review" if needs_review else "all")
    where = execute.await_args.args[0].whereclause
    assert (where is not None) == needs_review


@pytest.mark.asyncio
async def test_teams_page_renders_only_selected_entitled_roster(
    roster, authenticated_roster_view, monkeypatch
):
    request, use_results = authenticated_roster_view
    roster.assigned.created_at = datetime(2026, 1, 1)
    roster.assigned.municipality = "Guadalajara"
    execute = use_results([roster.assigned, roster.outside])
    load_players = AsyncMock(return_value=[])
    monkeypatch.setattr(
        dashboard,
        "CopaTelmexDB",
        lambda _session: SimpleNamespace(get_players_by_team=load_players),
    )

    page = await dashboard.list_teams(request, roster.edition.roster_slug, "14")

    assert page.template == "teams.html"
    assert page.context["roster_slug"] == roster.edition.roster_slug
    assert [row["municipality"] for row in page.context["teams"]] == ["Guadalajara"]
    load_players.assert_awaited_once_with(roster.assigned.id)
    query = execute.await_args.args[0]
    assert roster.edition.roster_slug in query.compile().params.values()

    with pytest.raises(HTTPException) as denied:
        await dashboard.list_teams(request, roster.edition.roster_slug, "21")
    assert denied.value.status_code == 403
    load_players.assert_awaited_once_with(roster.assigned.id)


@pytest.mark.parametrize(
    ("route", "database_error", "expected_error"),
    [
        (dashboard.edit_team, "CTT legacy successor stale", "STALE_POSTCOMMIT_BASE"),
        (
            dashboard.edit_player,
            "CTT legacy cannot mutate protected v2 evidence",
            "POSTCOMMIT_PROTECTED_CAPTURE",
        ),
        (
            dashboard.verify_player,
            "CTT legacy successor stale",
            "STALE_POSTCOMMIT_BASE",
        ),
    ],
)
@pytest.mark.asyncio
async def test_mutation_routes_forward_revision_and_translate_governed_denial(
    route, database_error, expected_error, monkeypatch
):
    request = SimpleNamespace(
        session={"empleado_id": str(uuid4()), "rol": "coordinador"},
        form=AsyncMock(
            return_value={
                "mutation_request_id": str(uuid4()),
                "expected_revision": "7",
                "name": "Equipo ficticio",
                "first_name": "Nombre",
                "last_name": "Ficticio",
            }
        ),
    )
    mutate = AsyncMock(side_effect=DBAPIError(None, None, Exception(database_error)))
    monkeypatch.setattr(dashboard, "_execute_regs07_mutation", mutate)

    with pytest.raises(HTTPException) as denied:
        await route(str(uuid4()), request)

    assert denied.value.status_code == 409
    assert denied.value.detail["error"] == expected_error
    assert mutate.await_args.kwargs["expected_revision"] == 7


@pytest.mark.parametrize(
    ("category", "gender", "expected"),
    [
        ("Juvenil", "Varonil", ["RENAPO", "FMF / Liga MX"]),
        ("Libre", "Femenil", ["RENAPO"]),
    ],
)
@pytest.mark.asyncio
async def test_scoped_dossier_keeps_official_validation_pending(
    roster, monkeypatch, category, gender, expected
):
    team = roster.assigned
    team.category = category
    team.gender = gender
    team.created_at = datetime(2026, 1, 1)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return []

    db = SimpleNamespace(
        get_team_by_id=AsyncMock(return_value=team),
        get_players_by_team=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(dashboard, "async_session_maker", Session)
    monkeypatch.setattr(dashboard, "CopaTelmexDB", lambda _session: db)
    monkeypatch.setattr(
        dashboard, "_ensure_legacy_copa_dashboard_access", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(
        dashboard.templates,
        "TemplateResponse",
        lambda _template, context, **_kwargs: context,
    )
    dossier = await dashboard.view_team(object(), str(team.id))
    assert dossier["scoped_ctt"] is True
    assert dossier["external_checks"] == expected
