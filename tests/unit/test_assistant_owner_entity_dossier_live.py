from __future__ import annotations

import pytest

from samchat.assistant.owner_entity_dossier_live import (
    OWNER_ENTITY_DOSSIER_LIVE_ONLY,
    build_owner_entity_dossier_live_from_tournament_source,
)


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _source():
    return _Obj(
        schema_version="local.v1",
        source_hash="sha256:entity-live",
        domain_write_performed=False,
        project=_Obj(
            id="tor-1",
            name="Copa Local",
            categorias=["Sub 15"],
            etapas=["Inscripcion"],
        ),
        operations_link=_Obj(operations_tournament_slug="copa-local"),
        observed_operations=_Obj(
            scope_slug="copa-local",
            teams_count=3,
            players_count=42,
            categories=["Sub 15"],
            branches=["Varonil"],
            states=["CDMX"],
            municipalities=["Benito Juarez"],
        ),
        unavailable_components=["rich_tournament_dates", "communications"],
    )


def test_owner_entity_dossier_live_is_read_only_and_local_source_bound() -> None:
    report = build_owner_entity_dossier_live_from_tournament_source(_source(), entity_name="CDMX")

    assert report.report_id == "owner_entity_dossier_live_v1"
    assert report.audit_language == OWNER_ENTITY_DOSSIER_LIVE_ONLY
    assert report.status == "partial_aggregate_only"
    assert report.entity_name == "CDMX"
    assert report.source_summary["source"] == "samchat_local_tournament_db"
    assert report.source_summary["source_hash"] == "sha256:entity-live"
    assert report.source_summary["aggregate_only"] is True
    assert report.safety_summary["read_only"] is True
    assert report.safety_summary["writes_enabled"] is False
    assert report.safety_summary["fallback_to_legacy_supabase"] is False
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0


def test_owner_entity_dossier_live_does_not_overclaim_entity_finance_or_detail() -> None:
    report = build_owner_entity_dossier_live_from_tournament_source(_source(), entity_name="CDMX")

    assert "Detalle financiero por entidad" in report.missing_evidence
    assert "Contacto completo de entidad" in report.missing_evidence
    assert any("agregados" in item.lower() for item in report.non_claims)
    assert report.audit["entity_count"] == 1
    assert report.audit["entities"][0]["source_summary"]["players_count"] == 42


def test_owner_entity_dossier_live_fail_closed_when_no_teams_or_players() -> None:
    source = _source()
    source.observed_operations.teams_count = 0
    source.observed_operations.players_count = 0

    report = build_owner_entity_dossier_live_from_tournament_source(source)

    assert report.status in {"partial", "not_found"}
    assert report.source_summary["aggregate_only"] is False
    assert "Equipos/jugadores por entidad" in report.missing_evidence
    assert report.safety_summary["raw_dossier_exposed"] is False

async def _fake_inspect_tournament_source(session, *, tournament_id=None, tournament_name=None):
    assert tournament_name == "Copa Local"
    assert tournament_id is None
    return _source()


async def _fake_inspect_tournament_not_found(session, *, tournament_id=None, tournament_name=None):
    from samchat.assistant.tournament_goal_source import TournamentSourceNotFoundError

    raise TournamentSourceNotFoundError("local tournament was not found")


async def _fake_inspect_tournament_ambiguous(session, *, tournament_id=None, tournament_name=None):
    from samchat.assistant.tournament_goal_source import TournamentSourceAmbiguousError

    raise TournamentSourceAmbiguousError("tournament name resolves to more than one local project")


@pytest.mark.asyncio
async def test_router_runs_owner_entity_dossier_live_from_local_source(monkeypatch) -> None:
    import pytest
    import samchat.assistant.router as assistant_router

    monkeypatch.setattr(assistant_router, "inspect_tournament_source", _fake_inspect_tournament_source)

    result = await assistant_router._run_read_tool(
        "assistant_owner_entity_dossier_live",
        {"tournament_name": "Copa Local", "entity_name": "CDMX"},
        gastos_session=object(),
        tournament_key_default=None,
        current_role="admin",
    )

    assert result["report_id"] == "owner_entity_dossier_live_v1"
    assert result["source_summary"]["source"] == "samchat_local_tournament_db"
    assert result["safety_summary"]["read_only"] is True
    assert result["writes_attempted"] == 0


@pytest.mark.asyncio
async def test_router_owner_entity_dossier_live_requires_one_selector() -> None:
    import pytest
    from fastapi import HTTPException
    import samchat.assistant.router as assistant_router

    with pytest.raises(HTTPException) as excinfo:
        await assistant_router._run_read_tool(
            "assistant_owner_entity_dossier_live",
            {},
            gastos_session=object(),
            tournament_key_default=None,
            current_role="admin",
        )

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_router_owner_entity_dossier_live_reports_not_found(monkeypatch) -> None:
    import pytest
    from fastapi import HTTPException
    import samchat.assistant.router as assistant_router

    monkeypatch.setattr(assistant_router, "inspect_tournament_source", _fake_inspect_tournament_not_found)

    with pytest.raises(HTTPException) as excinfo:
        await assistant_router._run_read_tool(
            "assistant_owner_entity_dossier_live",
            {"tournament_name": "No Existe"},
            gastos_session=object(),
            tournament_key_default=None,
            current_role="admin",
        )

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_router_owner_entity_dossier_live_reports_ambiguous(monkeypatch) -> None:
    import pytest
    from fastapi import HTTPException
    import samchat.assistant.router as assistant_router

    monkeypatch.setattr(assistant_router, "inspect_tournament_source", _fake_inspect_tournament_ambiguous)

    with pytest.raises(HTTPException) as excinfo:
        await assistant_router._run_read_tool(
            "assistant_owner_entity_dossier_live",
            {"tournament_name": "Copa"},
            gastos_session=object(),
            tournament_key_default=None,
            current_role="admin",
        )

    assert excinfo.value.status_code == 409
