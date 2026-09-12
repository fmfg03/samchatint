from unittest.mock import AsyncMock

import pytest

from samchat.client_executive import service, ui
from samchat.tournaments_v2.supabase_client import TournamentsV2Error


class _MappingResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _RowsMappingResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


def test_budget_scope_unavailable_is_not_rendered_as_real_zero():
    card = service._executive_card(
        {"id": "tor-1", "name": "Copa Telmex", "slug": ""},
        {
            "source": "budget_scope_unavailable",
            "summary": {"budget_total": 0.0},
            "comparison": {"actual_total": 0.0, "committed_total": 0.0},
            "forecast": {"projected_close_total": 0.0},
            "version": {"id": "version-1"},
        },
    )

    assert card["budget"] is None
    assert card["actual"] is None
    assert card["committed"] is None
    assert card["projected"] is None
    assert card["budget_source_status"] == "unavailable"
    assert ui._money(card["budget"]) == "No disponible"
    assert ui._money(0) == "$0.00"


@pytest.mark.asyncio
async def test_direction_budget_uses_guarded_legacy_alias_bridge(monkeypatch):
    strict = {
        "source": "budget_scope_unavailable",
        "version": {"id": "11111111-1111-1111-1111-111111111111"},
        "summary": {"budget_total": 0},
        "comparison": {},
        "forecast": {},
    }
    bridged = {
        "source": "budget_db",
        "version": {"id": "11111111-1111-1111-1111-111111111111"},
        "summary": {"budget_total": 1000},
        "comparison": {"actual_total": 400, "committed_total": 600},
        "forecast": {"projected_close_total": 900},
        "breakdowns": {"by_concept": []},
    }
    source = AsyncMock(side_effect=[strict, bridged])
    monkeypatch.setattr(service, "build_budget_snapshot", source)
    monkeypatch.setattr(service, "budget_alias_candidates", lambda *_args: {"CTT"})

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            assert "FROM budget_lines l" in sql
            assert "line_direction" not in sql
            assert params["aliases"] == ["CTT"]
            assert params["tournament_id"] == "tor-ctt"
            return _MappingResult(
                {
                    "line_count": 5,
                    "foreign_name_count": 0,
                    "foreign_id_count": 0,
                }
            )

    result = await service._build_direction_budget_snapshot(
        Session(),
        tournament={
            "id": "tor-ctt",
            "name": "Copa Telmex Telcel de Fútbol",
            "slug": "",
        },
        edition_year=2026,
    )

    assert result["source"] == "budget_db"
    assert result["summary"]["budget_total"] == 1000
    assert result["direction_scope_bridge"]["status"] == "exact_name_alias_bridge"
    assert source.await_count == 2
    assert source.await_args_list[0].kwargs["strict_tournament_scope"] is True
    assert source.await_args_list[1].kwargs["strict_tournament_scope"] is False
    assert isinstance(
        source.await_args_list[1].args[0], service._DirectionBudgetReadSession
    )


@pytest.mark.asyncio
async def test_direction_budget_alias_bridge_fails_closed_on_foreign_tournament(
    monkeypatch,
):
    strict = {
        "source": "budget_scope_unavailable",
        "version": {"id": "11111111-1111-1111-1111-111111111111"},
    }
    source = AsyncMock(return_value=strict)
    monkeypatch.setattr(service, "build_budget_snapshot", source)
    monkeypatch.setattr(service, "budget_alias_candidates", lambda *_args: {"CTT"})

    class Session:
        async def execute(self, statement, _params=None):
            assert "line_direction" not in str(statement)
            return _MappingResult(
                {
                    "line_count": 5,
                    "foreign_name_count": 0,
                    "foreign_id_count": 1,
                }
            )

    result = await service._build_direction_budget_snapshot(
        Session(),
        tournament={
            "id": "tor-ctt",
            "name": "Copa Telmex Telcel de Fútbol",
            "slug": "",
        },
        edition_year=2026,
    )

    assert result is strict
    assert source.await_count == 1


@pytest.mark.asyncio
async def test_direction_budget_consumer_rejects_concurrent_foreign_alias_row(
    monkeypatch,
):
    strict = {
        "source": "budget_scope_unavailable",
        "version": {"id": "11111111-1111-1111-1111-111111111111"},
        "summary": {"budget_total": 0},
        "comparison": {},
        "forecast": {},
    }
    calls = 0

    async def snapshot(snapshot_session, *, strict_tournament_scope, **_kwargs):
        nonlocal calls
        calls += 1
        if strict_tournament_scope:
            return strict
        result = await snapshot_session.execute(
            service.text("""
                SELECT l.tournament_id, l.tournament_name, l.budget_amount
                FROM budget_lines l
                WHERE l.budget_version_id = :version_id
                """),
            {
                "version_id": "11111111-1111-1111-1111-111111111111",
                "aliases": ["CTT"],
                "tournament_id": "tor-ctt",
            },
        )
        rows = result.mappings().all()
        return {
            "source": "budget_db",
            "summary": {
                "budget_total": sum(float(row["budget_amount"]) for row in rows)
            },
        }

    monkeypatch.setattr(service, "build_budget_snapshot", snapshot)
    monkeypatch.setattr(service, "budget_alias_candidates", lambda *_args: {"CTT"})

    class Session:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "COUNT(*) AS line_count" in sql:
                return _MappingResult(
                    {
                        "line_count": 1,
                        "foreign_name_count": 0,
                        "foreign_id_count": 0,
                    }
                )
            if "l.budget_version_id = :version_id" in sql:
                return _RowsMappingResult(
                    [
                        {
                            "tournament_id": "foreign-tournament",
                            "tournament_name": "Copa Telmex Telcel de Fútbol",
                            "budget_amount": 999_999,
                        }
                    ]
                )
            raise AssertionError(f"unexpected SQL: {sql}")

    result = await service._build_direction_budget_snapshot(
        Session(),
        tournament={
            "id": "tor-ctt",
            "name": "Copa Telmex Telcel de Fútbol",
            "slug": "",
        },
        edition_year=2026,
    )

    assert result is strict
    assert calls == 2


@pytest.mark.asyncio
async def test_operational_bridge_accepts_only_unique_exact_name_and_edition(
    monkeypatch,
):
    async def snapshot(*, tournament_slug, **_kwargs):
        if tournament_slug == "local-id":
            raise TournamentsV2Error("different UUID namespace")
        assert tournament_slug == "Copa Telmex Telcel de Fútbol"
        return {
            "tournaments": [
                {
                    "id": "supabase-id",
                    "name": "Copa Telmex Telcel de Futbol",
                    "start_date": "2026-01-15",
                }
            ],
            "soul": {
                "tournament": {"id": "supabase-id"},
                "national_phase": {},
                "marketing": {},
            },
        }

    monkeypatch.setattr(service, "build_tournament_soul_snapshot", snapshot)
    monkeypatch.setattr(
        service,
        "build_director_general_entity_dossier",
        lambda _snapshot: {"entities": [{"entity_name": "Morelos"}]},
    )

    dossier = await service._build_operational_dossier(
        {
            "id": "local-id",
            "name": "Copa Telmex Telcel de Fútbol",
            "slug": "",
        },
        edition_year=2026,
    )

    assert dossier["source_status"] == "available"
    assert dossier["source_bridge"] == "exact_name_edition_bridge"
    assert dossier["entities"][0]["entity_name"] == "Morelos"


@pytest.mark.asyncio
async def test_operational_name_bridge_rejects_ambiguous_matches(monkeypatch):
    async def snapshot(*, tournament_slug, **_kwargs):
        if tournament_slug == "local-id":
            raise TournamentsV2Error("different UUID namespace")
        return {
            "tournaments": [
                {
                    "id": "one",
                    "name": "Copa Telmex Telcel de Fútbol",
                    "start_date": "2026-01-01",
                },
                {
                    "id": "two",
                    "name": "Copa Telmex Telcel de Fútbol",
                    "start_date": "2026-02-01",
                },
            ],
            "soul": {},
        }

    monkeypatch.setattr(service, "build_tournament_soul_snapshot", snapshot)

    dossier = await service._build_operational_dossier(
        {
            "id": "local-id",
            "name": "Copa Telmex Telcel de Fútbol",
            "slug": "",
        },
        edition_year=2026,
    )

    assert dossier["source_status"] == "unavailable"
    assert dossier["entities"] == []


def test_ui_v2_surfaces_partidas_and_accounts_without_fake_zeroes():
    card = {
        "tournament_id": "tor-1",
        "tournament_name": "Copa Telmex Telcel de Fútbol",
        "budget": 1_000_000,
        "actual": 420_000,
        "committed": 600_000,
        "paid": 350_000,
        "projected": 920_000,
        "budget_source_status": "available",
        "budget_scope_bridge": {"status": "exact_name_alias_bridge"},
        "budget_breakdowns": {
            "by_concept": [
                {
                    "label": "Uniformes",
                    "budget_total": 300_000,
                    "actual_total": 120_000,
                    "committed_total": 150_000,
                }
            ],
            "by_account": [
                {
                    "label": "5300-012-018 · ALIMENTOS",
                    "budget_total": 100_000,
                    "actual_total": 50_000,
                    "committed_total": 60_000,
                }
            ],
        },
        "dossier": {"source_status": "unavailable"},
        "as_of": "2026-09-12T00:00:00+00:00",
    }

    rendered = ui._tournament(card, 2026, 0)

    assert "Presupuesto vs. real" in rendered
    assert "Uniformes" in rendered
    assert "5300-012-018" in rendered
    assert "$1,000,000.00" in rendered
    assert "Identidad reconciliada" in rendered
    assert "No se muestran ceros" not in rendered


def test_ui_v2_keeps_unavailable_sections_compact_and_explicit():
    card = {
        "tournament_id": "tor-1",
        "tournament_name": "Copa Telmex Telcel de Fútbol",
        "budget": None,
        "actual": None,
        "committed": None,
        "projected": None,
        "budget_source_status": "unavailable",
        "dossier": {
            "source_status": "unavailable",
            "marketing": {"status": "unavailable"},
        },
        "as_of": "2026-09-12T00:00:00+00:00",
    }

    rendered = ui._tournament(card, 2026, 0)

    assert "No disponible" in rendered
    assert "$0.00" not in rendered
    assert "No se muestran ceros" in rendered
    assert "La identidad operativa del torneo aún no pudo reconciliarse" in rendered


def test_marketing_requires_explicit_available_source_before_showing_counts():
    rendered = ui._marketing({"marketing": {"media": {"photos_count": 0}}}, 0)

    assert 'class="status status-unavailable"' in rendered
    assert "<h4>Fotografías</h4><p>Fuente no disponible</p>" in rendered
    assert "<h4>Videos</h4><p>Fuente no disponible</p>" in rendered
