from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from samchat.assistant import registration_postgres_adapter as adapter
from samchat.assistant import tools
from samchat.client_executive import service, ui


@pytest.fixture
def snapshot():
    return {
        "source": "postgres_registration",
        "status": "AVAILABLE",
        "source_status": "AVAILABLE",
        "available": True,
        "tournament_id": "11111111-1111-1111-1111-111111111111",
        "tournament_name": "Copa Telmex",
        "edition_year": 2026,
        "roster_slug": "copa-telmex-2026",
        "summary": {
            "total_teams": 2,
            "active_players": 32,
            "provisional_players": 3,
            "pending_reviews": 1,
        },
        "groups": {
            "by_state": [{"state": "Oaxaca", "teams": 2, "active_players": 32}],
            "by_municipality": [{"municipality": "Oaxaca", "teams": 2}],
            "by_category": [{"category": "Juvenil", "teams": 2}],
            "by_gender": [{"gender": "varonil", "teams": 2}],
        },
        "teams": [],
        "executive_reports": {
            "summary": {"equipos": 2, "jugadores": 32, "estados": 1},
            "reports": {"juvenil_edades": [{"edad_15": 32}]},
            "as_of_date": "2026-10-05",
            "caveats": ["FMF no disponible"],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", ["operations", "breakdown", "executive_reports"])
async def test_projections_preserve_shared_counts_and_explicit_scope(
    monkeypatch, snapshot, projection
):
    read = AsyncMock(return_value=deepcopy(snapshot))
    monkeypatch.setattr(adapter, "dispatch_registration_snapshot", read)
    result = await adapter.registration_postgres_response(
        object(),
        projection=projection,
        tournament_key="futbol",
        tournament_id=snapshot["tournament_id"],
        tournament_slug="wrong-hint",
        edition_year=2026,
        state="Oaxaca",
        as_of_date="2025-01-01",
    )
    assert result["total_equipos"] == 2
    assert result["total_jugadores"] == 32
    assert result["registration_summary"]["provisional_players"] == 3
    assert read.await_args.kwargs["tournament_id"] == snapshot["tournament_id"]
    assert read.await_args.kwargs["tournament_slug"] is None
    assert read.await_args.kwargs["edition_year"] == 2026
    if projection == "executive_reports":
        assert result["reports"]["juvenil_edades"] == [{"edad_15": 32}]
        assert result["summary"]["estados"] == 1
    if projection == "operations":
        assert result["players"] == []


@pytest.mark.asyncio
async def test_unconfigured_selector_preserves_legacy_dispatch(monkeypatch):
    monkeypatch.setattr(
        adapter, "dispatch_registration_snapshot", AsyncMock(return_value=None)
    )
    assert (
        await adapter.registration_postgres_response(
            object(), projection="operations", tournament_key="another-tournament"
        )
        is None
    )


@pytest.mark.asyncio
async def test_unknown_projection_is_rejected(monkeypatch, snapshot):
    monkeypatch.setattr(
        adapter, "dispatch_registration_snapshot", AsyncMock(return_value=snapshot)
    )
    with pytest.raises(ValueError, match="Unknown registration projection"):
        await adapter.registration_postgres_response(
            object(), projection="invented", tournament_key="Copa Telmex"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,extra",
    [
        (tools.tournament_ops_query, {}),
        (tools.tournament_registration_breakdown, {"state": "Oaxaca"}),
        (tools.tournament_registration_executive_reports, {}),
    ],
)
async def test_configured_tools_never_query_supabase(
    monkeypatch, snapshot, tool, extra
):
    monkeypatch.setattr(
        adapter, "dispatch_registration_snapshot", AsyncMock(return_value=snapshot)
    )

    def forbidden():
        raise AssertionError("Configured reads must not choose Supabase")

    monkeypatch.setattr(tools, "_tournaments_v2_read_flags", forbidden)
    result = await tool(
        object(), tournament_key="Copa Telmex", edition_year=2026, **extra
    )
    assert result["source"] == "postgres_registration"
    assert result["total_jugadores"] == 32


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SCOPE_MISSING", "SOURCE_FAILED", "SCOPE_INACTIVE"])
async def test_failed_sources_do_not_become_zero_or_fallback(monkeypatch, status):
    unavailable = {
        "source": "postgres_registration",
        "status": status,
        "available": False,
        "summary": None,
        "next_action": "Revisar alcance y fuente",
    }
    monkeypatch.setattr(
        adapter, "dispatch_registration_snapshot", AsyncMock(return_value=unavailable)
    )
    monkeypatch.setattr(
        tools, "_tournaments_v2_read_flags", lambda: pytest.fail("fallback")
    )
    result = await tools.tournament_registration_executive_reports(
        object(), tournament_key="Copa Telmex", edition_year=2026
    )
    assert result["summary"]["equipos"] is None
    assert result["total_jugadores"] is None
    assert "Revisar alcance y fuente" in result["caveats"]


@pytest.mark.asyncio
async def test_dashboard_and_assistant_share_counts_after_scope_guard(
    monkeypatch, snapshot
):
    read = AsyncMock(return_value=deepcopy(snapshot))
    monkeypatch.setattr(service, "dispatch_registration_snapshot", read)
    monkeypatch.setattr(adapter, "dispatch_registration_snapshot", read)
    monkeypatch.setattr(
        service,
        "_authorized_tournaments",
        AsyncMock(
            return_value=[
                {"id": snapshot["tournament_id"], "name": "Copa Telmex", "slug": ""}
            ]
        ),
    )
    monkeypatch.setattr(
        service, "_build_direction_budget_snapshot", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        service,
        "_build_operational_dossier",
        AsyncMock(
            return_value={
                "source_status": "available",
                "entities": [{"entity_name": "STALE-ROSTER"}],
                "marketing": {"media": {"photos_count": 7}},
                "national_phase": {"matches": [{"phase": "Nacional"}]},
            }
        ),
    )
    board = await service.build_client_dashboard(
        object(),
        empleado_id="director",
        edition_year=2026,
        include_operational_detail=True,
    )
    answer = await tools.tournament_ops_query(
        object(), tournament_key="Copa Telmex", edition_year=2026
    )
    card = board["cards"][0]
    assert card["registration"]["summary"] == answer["registration_summary"]
    assert read.await_args_list[0].kwargs["tournament_id"] == snapshot["tournament_id"]
    html = ui._tournament(card, 2026, 0)
    assert "STALE-ROSTER" not in html
    assert "Equipos capturados" in html
    assert "Jugadores provisionales" in html
    assert "<h4>Fotografías</h4><p>7</p>" in html
    assert "Nacional" in html


@pytest.mark.asyncio
async def test_unauthorized_tournament_never_reaches_registration_source(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(service, "dispatch_registration_snapshot", read)
    monkeypatch.setattr(
        service,
        "_authorized_tournaments",
        AsyncMock(return_value=[{"id": "allowed", "name": "Allowed", "slug": ""}]),
    )
    with pytest.raises(service.ClientExecutiveAccessError):
        await service.build_client_dashboard(
            object(),
            empleado_id="director",
            edition_year=2026,
            tournament_id="forbidden",
            include_operational_detail=True,
        )
    read.assert_not_awaited()


def test_empty_and_failed_registration_render_distinctly():
    empty = ui._registration(
        {
            "available": True,
            "status": "EMPTY",
            "summary": {
                "total_teams": 0,
                "active_players": 0,
                "provisional_players": 0,
                "pending_reviews": 0,
            },
            "edition_year": 2026,
        },
        0,
    )
    failed = ui._registration(
        {
            "available": False,
            "status": "SOURCE_FAILED",
            "next_action": "Verificar fuente",
        },
        0,
    )
    assert "<strong>0</strong>" in empty
    assert "Sin equipos capturados" in empty
    assert "<strong>0</strong>" not in failed
    assert "Verificar fuente" in failed


def test_registration_html_escapes_untrusted_fields(snapshot):
    snapshot["groups"]["by_state"][0]["state"] = "<script>alert(1)</script>"
    html = ui._registration(snapshot, 0)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,extra",
    [
        ("tournament_ops_query", {}),
        ("tournament_registration_breakdown", {"state": "Oaxaca"}),
    ],
)
async def test_router_uses_operational_session_before_legacy_database(
    monkeypatch, snapshot, tool_name, extra
):
    from samchat.assistant import router

    operational_session = object()
    read = AsyncMock(return_value=deepcopy(snapshot))
    monkeypatch.setattr(adapter, "dispatch_registration_snapshot", read)
    monkeypatch.setattr(
        router,
        "get_tournament_session_maker",
        lambda *_: pytest.fail("legacy database"),
    )
    result = await router._run_read_tool(
        tool_name,
        {
            "tournament_key": "copa-telmex",
            "tournament_id": snapshot["tournament_id"],
            "edition_year": 2026,
            **extra,
        },
        gastos_session=operational_session,
        tournament_key_default=None,
    )
    assert read.await_args.args[0] is operational_session
    assert result["total_jugadores"] == 32


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,extra",
    [
        ("tournament_ops_query", {}),
        ("tournament_registration_breakdown", {"state": "Oaxaca"}),
        ("tournament_registration_executive_reports", {}),
    ],
)
async def test_unconfigured_tools_keep_existing_contract(monkeypatch, name, extra):
    monkeypatch.setattr(
        adapter, "dispatch_registration_snapshot", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(tools, "_tournaments_v2_read_flags", lambda: (False, True))
    legacy = AsyncMock(return_value={"source": "legacy_copa_telmex_db"})
    monkeypatch.setattr(tools, "_" + name + "_legacy", legacy)
    result = await getattr(tools, name)(
        object(), tournament_key="another-tournament", **extra
    )
    assert result["source"] == "legacy_copa_telmex_db"
    legacy.assert_awaited_once()


@pytest.mark.asyncio
async def test_executive_action_propagates_context_id_and_edition(
    monkeypatch, snapshot
):
    from samchat.assistant import adapters
    from samchat.assistant.context import AssistantContext

    read = AsyncMock(return_value={"tournament": {"id": snapshot["tournament_id"]}})
    monkeypatch.setattr(adapters, "tournament_registration_executive_reports", read)
    context = AssistantContext(tournament_id=snapshot["tournament_id"], edition="2026")
    await adapters.operations_tournament_registration_executive_reports_adapter(
        object(), context=context, payload={}
    )
    assert read.await_args.kwargs["tournament_id"] == snapshot["tournament_id"]
    assert read.await_args.kwargs["edition_year"] == 2026


@pytest.mark.asyncio
async def test_localized_date_filters_are_normalized_without_changing_edition(
    monkeypatch, snapshot
):
    read = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(adapter, "dispatch_registration_snapshot", read)
    await adapter.registration_postgres_response(
        object(),
        projection="operations",
        tournament_key="Copa Telmex",
        edition_year=2026,
        date_from="01/02/2025",
        date_to="28/02/2025",
    )
    assert read.await_args.kwargs["filters"]["date_from"] == "2025-02-01"
    assert read.await_args.kwargs["edition_year"] == 2026
    with pytest.raises(ValueError, match="date filter"):
        await adapter.registration_postgres_response(
            object(),
            projection="operations",
            tournament_key="Copa Telmex",
            date_from="invalid",
        )


def test_tournament_header_distinguishes_partial_sources(snapshot):
    html = ui._tournament(
        {"dossier": {"source_status": "unavailable"}, "registration": snapshot}, 2026, 0
    )
    assert 'class="status status-partial"' in html
    assert "Equipos capturados" in html
    html = ui._tournament(
        {
            "dossier": {"source_status": "available"},
            "registration": {"available": False, "status": "SOURCE_FAILED"},
        },
        2026,
        0,
    )
    assert 'class="status status-partial"' in html


@pytest.mark.asyncio
async def test_router_preserves_exact_configured_project_name(monkeypatch, snapshot):
    from samchat.assistant import router

    async def exact_selector(_session, **kwargs):
        assert kwargs["tournament_selector"] == "Copa Telmex"
        return snapshot

    monkeypatch.setattr(adapter, "dispatch_registration_snapshot", exact_selector)
    monkeypatch.setattr(
        router,
        "get_tournament_session_maker",
        lambda *_: pytest.fail("legacy database"),
    )
    answer = await router._run_read_tool(
        "tournament_ops_query",
        {"tournament_key": "Copa Telmex", "edition_year": 2026},
        gastos_session=object(),
        tournament_key_default=None,
    )
    assert answer["source"] == "postgres_registration"


@pytest.mark.asyncio
async def test_configured_dashboard_excludes_registration_before_soul_load(
    monkeypatch, snapshot
):
    calls = []

    async def read(_session, **_kwargs):
        calls.append("registration")
        return snapshot

    async def other_domains(tournament, *, edition_year, include_registration=True):
        calls.append("other_domains")
        assert include_registration is False
        return {"source_status": "available", "entities": []}

    monkeypatch.setattr(service, "dispatch_registration_snapshot", read)
    monkeypatch.setattr(
        service,
        "_authorized_tournaments",
        AsyncMock(
            return_value=[
                {"id": snapshot["tournament_id"], "name": "Copa Telmex", "slug": ""}
            ]
        ),
    )
    monkeypatch.setattr(
        service, "_build_direction_budget_snapshot", AsyncMock(return_value={})
    )
    monkeypatch.setattr(service, "_build_operational_dossier", other_domains)
    await service.build_client_dashboard(
        object(),
        empleado_id="director",
        edition_year=2026,
        include_operational_detail=True,
    )
    assert calls == ["registration", "other_domains"]


@pytest.mark.asyncio
async def test_dossier_exclusion_preserves_other_domains_without_roster_payload(
    monkeypatch,
):
    async def other_domains(**kwargs):
        assert kwargs["include_registration"] is False
        return {
            "tournaments": [{"id": "scope", "start_date": "2026-01-01"}],
            "entities": [{"team_count": 999}],
            "soul": {
                "national_phase": {"matches": [{"phase": "Nacional"}]},
                "marketing": {"media": {"photos_count": 7}},
            },
        }

    monkeypatch.setattr(service, "build_tournament_soul_snapshot", other_domains)
    monkeypatch.setattr(
        service,
        "build_director_general_entity_dossier",
        lambda *_: pytest.fail("roster dossier"),
    )
    dossier = await service._build_operational_dossier(
        {"id": "scope", "name": "Copa", "slug": ""},
        edition_year=2026,
        include_registration=False,
    )
    assert dossier["entities"] == []
    assert dossier["summary"] == {}
    assert "999" not in str(dossier)
    assert dossier["national_phase"]["matches"] == [{"phase": "Nacional"}]
    assert dossier["marketing"]["media"]["photos_count"] == 7
