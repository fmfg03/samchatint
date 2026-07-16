import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from devnous.copa_telmex.supabase_authority import (
    SupabaseAuthorityDenied,
    issue_supabase_replication_capability,
    replica_roster_hash,
)
from devnous.tournaments.core.supabase_sync import (
    SupabaseAdminClient,
    SupabaseConfig,
)
from samchat.tournaments_v2.adapters import commands
from samchat.tournaments_v2.supabase_client import SupabaseRestClient


PLAYERS = [
    {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "birth_date": "10/12/2012",
        "curp": "abcd121210mxyzff01",
    }
]


def _hash(value):
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _governance_result(*, operation="append_players", players=PLAYERS):
    scope = {
        "operation": operation,
        "tournament_key": "copa_telmex",
        "tournament_slug": "copa-telmex-2026",
        "tournament_name": "",
        "category_id": "",
        "category_name": "Femenil",
        "target_team_id": "",
        "source_team_id": "local-team-1",
        "team_name": "Academicos",
    }
    event = {
        "event_type": "samchat_registration_supabase_replication_v1",
        "tenant_id": "samchat-prod",
        "decision": "AUTHORIZE_REPLICA_WRITE",
        "scope": scope,
        "roster_identity_hash": replica_roster_hash(players),
        "roster_draft_binding": "hmac-sha256:" + "2" * 64,
        "finality_receipt_ids": ["sha256:" + "3" * 64 for _ in players],
    }
    return {
        "authorized": True,
        "replication_event": event,
        "replication_receipt": {
            "receipt_type": "EvidenceReceipt.v1",
            "receipt_id": "sha256:" + "4" * 64,
            "event_hash": _hash(event),
            "event_type": event["event_type"],
            "tenant_id": event["tenant_id"],
            "verified": True,
        },
    }


def _capability(*, operation="append_players", players=PLAYERS):
    return issue_supabase_replication_capability(
        _governance_result(operation=operation, players=players),
        operation=operation,
        tournament_key="copa_telmex",
        tournament_slug="copa-telmex-2026",
        tournament_name=None,
        category_id=None,
        category_name="Femenil",
        target_team_id=None,
        source_team_id="local-team-1",
        team_name="Academicos",
        players=players,
    )


def _configure(monkeypatch):
    monkeypatch.setattr(
        commands,
        "load_tournaments_v2_config",
        lambda: SimpleNamespace(
            writes_enabled=True,
            supabase_url="https://example.invalid",
            service_role_key="opaque",
        ),
    )


def _resolve(monkeypatch):
    async def tournament(*_args, **_kwargs):
        return {"id": "t1", "name": "Copa Telmex", "slug": "copa-telmex-2026"}

    async def category(*_args, **_kwargs):
        return {"id": "c1", "name": "Femenil"}

    async def team(*_args, **_kwargs):
        return {"id": "supa-team-1", "team_name": "Academicos"}

    monkeypatch.setattr(commands, "resolve_primary_tournament", tournament)
    monkeypatch.setattr(commands, "resolve_category_for_tournament", category)
    monkeypatch.setattr(commands, "resolve_team_for_tournament", team)


def test_replica_write_fails_before_client_access_without_authority(monkeypatch):
    _configure(monkeypatch)

    class ForbiddenClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("client access must not occur without authority")

    monkeypatch.setattr(commands, "SupabaseRestClient", ForbiddenClient)
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        asyncio.run(
            commands.append_players_to_team_v2(
                tournament_key="copa_telmex",
                tournament_slug="copa-telmex-2026",
                category_name="Femenil",
                team_name="Academicos",
                players=PLAYERS,
            )
        )
    assert exc.value.reason_code == "SUPABASE_REPLICATION_AUTHORITY_REQUIRED"


def test_authority_binds_exact_backend_scope_and_is_one_use():
    authority = _capability()
    tampered = [dict(PLAYERS[0], first_name="Different")]
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        authority.consume(
            operation="append_players",
            tournament_key="copa_telmex",
            tournament_slug="copa-telmex-2026",
            tournament_name=None,
            category_id=None,
            category_name="Femenil",
            target_team_id=None,
            source_team_id="local-team-1",
            team_name="Academicos",
            players=tampered,
        )
    assert exc.value.reason_code == "SUPABASE_REPLICATION_SCOPE_MISMATCH"

    authority.consume(
        operation="append_players",
        tournament_key="copa_telmex",
        tournament_slug="copa-telmex-2026",
        tournament_name=None,
        category_id=None,
        category_name="Femenil",
        target_team_id=None,
        source_team_id="local-team-1",
        team_name="Academicos",
        players=PLAYERS,
    )
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        authority.consume(
            operation="append_players",
            tournament_key="copa_telmex",
            tournament_slug="copa-telmex-2026",
            tournament_name=None,
            category_id=None,
            category_name="Femenil",
            target_team_id=None,
            source_team_id="local-team-1",
            team_name="Academicos",
            players=PLAYERS,
        )
    assert exc.value.reason_code == "SUPABASE_REPLICATION_AUTHORITY_CONSUMED"


def test_append_dry_run_performs_no_supabase_write(monkeypatch):
    _configure(monkeypatch)
    _resolve(monkeypatch)

    class ReadOnlyClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def insert_rows(self, **_kwargs):
            raise AssertionError("dry_run must not write registrations or players")

    monkeypatch.setattr(commands, "SupabaseRestClient", ReadOnlyClient)
    result = asyncio.run(
        commands.append_players_to_team_v2(
            tournament_key="copa_telmex",
            tournament_slug="copa-telmex-2026",
            category_name="Femenil",
            team_name="Academicos",
            players=PLAYERS,
            dry_run=True,
        )
    )
    assert result["dry_run"] is True
    assert result["registration"]["id"] is None


def test_partial_backend_failure_consumes_authority_and_blocks_blind_retry(monkeypatch):
    _configure(monkeypatch)
    _resolve(monkeypatch)
    authority = _capability()

    class PartialClient:
        def __init__(self, *_args, **_kwargs):
            self.inserts = 0

        async def insert_rows(self, *, table, **_kwargs):
            self.inserts += 1
            if table == "registrations":
                return [{"id": "registration-1"}]
            raise RuntimeError("backend failed after registration upsert")

        async def fetch_all_rows(self, **_kwargs):
            return []

    monkeypatch.setattr(commands, "SupabaseRestClient", PartialClient)
    kwargs = {
        "tournament_key": "copa_telmex",
        "tournament_slug": "copa-telmex-2026",
        "category_name": "Femenil",
        "team_name": "Academicos",
        "source_team_id": "local-team-1",
        "players": PLAYERS,
        "replication_authority": authority,
    }
    with pytest.raises(RuntimeError):
        asyncio.run(commands.append_players_to_team_v2(**kwargs))
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        asyncio.run(commands.append_players_to_team_v2(**kwargs))
    assert exc.value.reason_code == "SUPABASE_REPLICATION_AUTHORITY_CONSUMED"


def test_invalid_or_incomplete_finality_cannot_issue_replication_authority():
    result = _governance_result()
    result["replication_event"]["finality_receipt_ids"] = []
    result["replication_receipt"]["event_hash"] = _hash(result["replication_event"])
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        issue_supabase_replication_capability(
            result,
            operation="append_players",
            tournament_key="copa_telmex",
            tournament_slug="copa-telmex-2026",
            tournament_name=None,
            category_id=None,
            category_name="Femenil",
            target_team_id=None,
            source_team_id="local-team-1",
            team_name="Academicos",
            players=PLAYERS,
        )
    assert exc.value.reason_code == "SUPABASE_REPLICATION_AUTHORITY_INVALID"


def test_low_level_rest_client_blocks_direct_governed_table_write():
    client = SupabaseRestClient(
        SimpleNamespace(
            supabase_url="https://example.invalid",
            service_role_key="opaque",
            anon_key="",
            request_timeout_sec=1,
        )
    )
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        client._request_sync(method="POST", path="players", payload=[])
    assert exc.value.reason_code == "SUPABASE_REPLICATION_AUTHORITY_REQUIRED"


def test_legacy_admin_fallback_cannot_mutate_without_opaque_permit(tmp_path):
    client = SupabaseAdminClient(
        SupabaseConfig(
            url="https://example.invalid",
            service_role_key="opaque",
        ),
        cache_dir=str(tmp_path),
    )
    with pytest.raises(SupabaseAuthorityDenied) as exc:
        asyncio.run(client.upsert_registration({"team_id": "t1", "category_id": "c1"}))
    assert exc.value.reason_code == "SUPABASE_REPLICATION_AUTHORITY_REQUIRED"
