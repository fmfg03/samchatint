import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "SESSION_SECRET_KEY", "ctt-roster-unit-test-key-no-production-access"
)

import copa_telmex_dashboard as dashboard  # noqa: E402

from devnous.copa_telmex import ctt_report_access  # noqa: E402
from devnous.copa_telmex.models import Team  # noqa: E402


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
