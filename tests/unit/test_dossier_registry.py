"""Exact-identity roster, anonymous directory and observation-only history."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

import devnous.gastos  # noqa: F401 - initialize the owning runtime first
from samchat.client_executive import dossier_registry as registry

PROJECT = "6e0992d9-57b8-43e4-8ca9-614674a734b7"
FIRST = "10d27944-96e1-452f-8968-eba4af84eb72"
SECOND = "894807db-be49-4153-a2ed-6437f4ec95d4"
THIRD = "8df31d1a-da97-414d-9c65-310ac58406cf"
SCOPED = {
    "status": "CONFIGURED",
    "tournament_id": PROJECT,
    "edition_year": 2026,
    "roster_slug": "copa-telmex-2026",
}
TEAM = {
    "id": FIRST,
    "name": "Equipo del Valle",
    "state": "Edo. México",
    "category": "Juvenil",
    "gender": "varonil",
    "player_registrations": 4,
    "updated_at": datetime(2026, 11, 20, 12),
    "curp": "not-returned",
}


def _jwt(role: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"role": role}).encode())
    return f"eyJhbGciOiJIUzI1NiJ9.{claims.decode().rstrip('=')}.fixture-signature"


@pytest.mark.asyncio
async def test_roster_applies_active_exact_scope_and_preserves_declared_classification(
    monkeypatch,
):
    rows = AsyncMock(return_value=[TEAM])
    monkeypatch.setattr(registry, "_rows", rows)
    result = await registry._live_teams(object(), SCOPED)
    (team,) = result["rows"]
    assert team["name"] == TEAM["name"]
    assert team["state_code"] == "15"
    assert team["state_slug"] == "estado-de-mexico"
    assert team["declared_entity"] == "Edo. México"
    assert team["category"] == "Juvenil"
    assert team["branch"] == "varonil"
    assert team["phase"] is None
    assert team["player_registrations"] == 4
    assert "curp" not in team
    _, sql, bindings = rows.await_args.args
    assert "e.edition_year = :edition_year" in sql
    assert "e.roster_slug = :roster_slug" in sql
    assert "p.governance_state IN ('ACTIVE', 'LEGACY_ACTIVE')" in sql
    assert bindings["edition_year"] == 2026


@pytest.mark.asyncio
async def test_source_drift_differs_from_verified_empty_and_partial(monkeypatch):
    rows = AsyncMock(side_effect=[[], [{"id": None}], [TEAM, TEAM]])
    monkeypatch.setattr(registry, "_rows", rows)
    monkeypatch.setattr(registry, "ROSTER_LIMIT", 1)
    assert (await registry._live_teams(object(), SCOPED))["status"] == "scope_missing"
    empty = await registry._live_teams(object(), SCOPED)
    assert empty["status"] == "empty"
    assert empty["team_count"] == 0
    partial = await registry._live_teams(object(), SCOPED)
    assert partial["status"] == "partial"
    assert partial["team_count"] is None


@pytest.mark.asyncio
async def test_directory_counts_states_and_preserves_zones_and_placeholders(
    monkeypatch,
):
    raw = [
        {
            "id": FIRST,
            "state_slug": "estado-de-mexico",
            "zone": "Valle de México",
            "name": "Persona de prueba",
            "email": "test@example.invalid",
            "phone": "5000000000",
            "is_active": True,
            "updated_at": "2026-11-22T12:30:00+00:00",
            "spouse_name": "No divulgar",
        },
        {
            "id": SECOND,
            "state_slug": "estado-de-mexico",
            "zone": "Valle de Toluca",
            "name": "Segunda persona",
            "is_active": True,
        },
        {
            "id": THIRD,
            "state_slug": "sonora",
            "name": "Por confirmar",
            "is_active": True,
        },
    ]
    owner = AsyncMock(return_value=(raw, True))
    monkeypatch.setattr(registry, "_fetch_directory", owner)
    included = await registry._directory(contacts_allowed=True)
    assert included["status"] == "available"
    assert included["catalog_entities"] == 32
    assert included["observed_entities"] == 2
    assert len(included["rows"]) == 33
    mexico = [
        item for item in included["rows"] if item["state_slug"] == "estado-de-mexico"
    ]
    assert [item["zone"] for item in mexico] == ["Valle de México", "Valle de Toluca"]
    assert mexico[0]["status"] == "confirmed"
    assert mexico[0]["representative_id"] == FIRST
    assert mexico[0]["updated_at"] == "2026-11-22T12:30:00+00:00"
    assert "spouse_name" not in mexico[0]
    sonora = next(row for row in included["rows"] if row["state_slug"] == "sonora")
    assert sonora["representative_name"] == "Por confirmar"
    assert sonora["status"] == "pending"

    masked = await registry._directory(contacts_allowed=False)
    assert all(
        not {"representative_id", "representative_name", "email", "phone"}.intersection(
            row
        )
        for row in masked["rows"]
    )


@pytest.mark.asyncio
async def test_partial_or_unavailable_directory_does_not_confirm_missing_states(
    monkeypatch,
):
    fetch = AsyncMock(side_effect=[([{}], True), ValueError("failed")])
    monkeypatch.setattr(registry, "_fetch_directory", fetch)
    partial = await registry._directory(contacts_allowed=True)
    assert partial["status"] == "partial"
    assert {row["status"] for row in partial["rows"]} == {"partial"}
    missing = await registry._directory(contacts_allowed=True)
    assert missing["status"] == "unavailable"
    assert missing["rows"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("contacts_allowed", [False, True])
async def test_directory_requests_only_anonymous_exact_host_and_explicit_columns(
    monkeypatch,
    contacts_allowed,
):
    key = _jwt("anon")
    monkeypatch.setenv(registry.DIRECTORY_KEY_ENV, key)
    monkeypatch.setenv(registry.DIRECTORY_ORIGIN_ENV, registry.DIRECTORY_ORIGIN)
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json=[{}] if len(requests) == 1 else [])

    original = httpx.AsyncClient

    def client_with_mock(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr(registry.httpx, "AsyncClient", client_with_mock)
    result, complete = await registry._fetch_directory(
        contacts_allowed=contacts_allowed
    )
    assert result == [{}]
    assert complete is True
    assert len(requests) == 2
    request = requests[0]
    assert str(request.url).startswith(registry.DIRECTORY_ENDPOINT + "?")
    assert request.url.params["is_active"] == "eq.true"
    assert ("email" in request.url.params["select"]) is contacts_allowed
    assert ("phone" in request.url.params["select"]) is contacts_allowed
    assert "personal" not in request.url.params["select"]
    assert request.headers["apikey"] == key
    assert request.headers["authorization"] == f"Bearer {key}"
    assert requests[1].url.params["offset"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "key"),
    [
        ("https://unexpected.invalid", _jwt("anon")),
        (registry.DIRECTORY_ORIGIN, _jwt("service_role")),
        (registry.DIRECTORY_ORIGIN, "sb_secret_an-actual-secret-is-never-a-fixture"),
    ],
)
async def test_directory_fails_closed_for_bad_origin_or_credential(
    monkeypatch, origin, key
):
    monkeypatch.setenv(registry.DIRECTORY_ORIGIN_ENV, origin)
    monkeypatch.setenv(registry.DIRECTORY_KEY_ENV, key)

    def no_connection(**_):
        pytest.fail("Unauthorized directory construction")

    monkeypatch.setattr(registry.httpx, "AsyncClient", no_connection)
    with pytest.raises(ValueError, match="anonymous directory"):
        await registry._fetch_directory(contacts_allowed=False)


@pytest.mark.asyncio
async def test_other_project_unconfigured_and_denied_operations_skip_directory(
    monkeypatch,
):
    scope = AsyncMock(return_value={"status": "NOT_CONFIGURED"})
    fetch = AsyncMock()
    monkeypatch.setattr(registry, "_ctt_scope", scope)
    monkeypatch.setattr(registry, "_fetch_directory", fetch)
    denied = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2026,
        operations_allowed=False,
    )
    assert denied["directory"]["status"] == "denied"
    scope.assert_not_awaited()
    unsupported = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2026,
        operations_allowed=True,
    )
    assert unsupported["directory"]["status"] == "not_configured"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_prior_date_reads_only_exact_observation(monkeypatch):
    monkeypatch.setattr(registry, "_ctt_scope", AsyncMock(return_value=SCOPED))
    live, directory = AsyncMock(), AsyncMock()
    monkeypatch.setattr(registry, "_live_teams", live)
    monkeypatch.setattr(registry, "_directory", directory)
    stored = {
        "team_count": 1,
        "observed_at": datetime(2026, 11, 20, 20, tzinfo=timezone.utc),
        "team_roster": [
            {"id": FIRST, "name": "Nombre observado", "curp": "unprojected"}
        ],
    }
    rows = AsyncMock(side_effect=[[stored], []])
    monkeypatch.setattr(registry, "_rows", rows)
    first_date = date(2026, 11, 20)
    result = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2026,
        operations_allowed=True,
        cut_date=first_date,
    )
    assert result["team_roster"]["rows"] == [{"id": FIRST, "name": "Nombre observado"}]
    assert result["team_roster"]["observed_at"] == "2026-11-20T20:00:00+00:00"
    assert result["cut_date"] == "2026-11-20"
    assert result["directory"]["status"] == "not_recorded"
    assert rows.await_args.args[-1]["cut_date"] == first_date
    missing = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2026,
        operations_allowed=True,
        cut_date=date(2026, 11, 19),
    )
    assert missing["team_roster"]["status"] == "not_recorded"
    live.assert_not_awaited()
    directory.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_source_failure_never_becomes_zero(monkeypatch):
    monkeypatch.setattr(registry, "_rows", AsyncMock(side_effect=SQLAlchemyError()))
    roster = await registry._live_teams(object(), SCOPED)
    historical = await registry._historical_teams(object(), SCOPED, date(2026, 11, 20))
    assert roster["status"] == historical["status"] == "unavailable"
    assert "team_count" not in roster and "team_count" not in historical


@pytest.mark.asyncio
async def test_configured_sibling_program_does_not_read_ctt_directory(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_ctt_scope",
        AsyncMock(return_value={**SCOPED, "roster_slug": "otro-torneo-2026"}),
    )
    monkeypatch.setattr(
        registry,
        "_live_teams",
        AsyncMock(return_value=registry._section("Padrón", "empty")),
    )
    directory = AsyncMock()
    monkeypatch.setattr(registry, "_directory", directory)
    result = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2026,
        operations_allowed=True,
    )
    assert result["directory"]["status"] == "not_configured"
    directory.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_directory_can_outlive_missing_link_for_requested_edition(
    monkeypatch,
):
    monkeypatch.setattr(
        registry, "_ctt_scope", AsyncMock(return_value={"status": "SCOPE_MISSING"})
    )
    rows = AsyncMock(return_value=[{"?column?": 1}])
    directory = AsyncMock(return_value=registry._section("Public", "available"))
    monkeypatch.setattr(registry, "_rows", rows)
    monkeypatch.setattr(registry, "_directory", directory)
    result = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2027,
        operations_allowed=True,
        contacts_allowed=True,
    )
    assert result["team_roster"]["status"] == "scope_missing"
    assert "edition_verified" not in result["team_roster"]
    assert result["directory"]["status"] == "available"
    assert rows.await_args.args[-1] == {"tournament_id": PROJECT}
    assert "CONCAT('copa-telmex-', e.edition_year)" in rows.await_args.args[1]
    dated = await registry.load_dossier_registry(
        object(),
        tournament={"id": PROJECT},
        edition_year=2027,
        operations_allowed=True,
        cut_date=date(2026, 11, 20),
    )
    assert dated["directory"]["rows"] == []
    directory.assert_awaited_once_with(contacts_allowed=True)
