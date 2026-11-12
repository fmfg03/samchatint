"""Canonical photo inventory is checked at preparation and writer time."""

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
import test_ctt_batch_acceptance_postgres as acceptance

from devnous.copa_telmex.staff_batch_contracts import BatchContractError
from devnous.copa_telmex.staff_batch_source_adapter import (
    PrivateBatchSourceProvider,
    SourceCheckAdapterV2,
)
from devnous.copa_telmex.staff_batch_source_service import BatchCatalogSourceProvider
from devnous.copa_telmex.staff_capture_postgres import _CaptureTransaction

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = acceptance.full_pg


async def seed_existing(admin, slug, photo_sha, photo_ahash, *, state="LEGACY_ACTIVE"):
    """Superuser fixture only: represent an already registered legacy player."""
    team, player = uuid4(), uuid4()
    await admin.execute("SET session_replication_role=replica")
    try:
        await admin.execute(
            "INSERT INTO public.copa_telmex_teams("
            "id,name,tournament_slug,created_at,postcommit_revision,"
            "postcommit_snapshot_hash) "
            "VALUES($1,'Existing fixture',$2,clock_timestamp(),1,$3)",
            team,
            slug,
            "sha256:" + "0" * 64,
        )
        await admin.execute(
            "INSERT INTO public.copa_telmex_players("
            "id,team_id,first_name,last_name,photo_sha256,photo_ahash,"
            "created_at,governance_state,postcommit_revision,postcommit_snapshot_hash) "
            "VALUES($1,$2,'Existing','Fixture',$3,$4,clock_timestamp(),"
            "$6,0,$5)",
            player,
            team,
            photo_sha,
            photo_ahash,
            "sha256:" + "0" * 64,
            state,
        )
    finally:
        await admin.execute("SET session_replication_role=origin")
    return team, player


def source_photo(case):
    source = case["source_context"]["documents"][0]
    context = {
        **case["source_context"],
        **case["context"],
        "capability": case["source_context"]["capabilities"][0],
    }
    adapter = SourceCheckAdapterV2(
        PrivateBatchSourceProvider(case["cas"]),
        max_source_bytes=4_000_000,
        max_asset_bytes=4_000_000,
        max_manifest_bytes=4_000_000,
    )
    with adapter.open_verified_snapshot(
        BatchCatalogSourceProvider._source_scope(context, source)
    ) as snapshot:
        photo = snapshot.derive_player_photo(
            case["observations"][0]["players"][0]["slot_ref"]
        )
        snapshot.verify_unchanged()
    return photo


@pytest.mark.parametrize("kind", ["exact", "near"])
def test_preparation_rejects_registered_tournament_photo(
    full_pg, tmp_path, monkeypatch, kind
):
    original = acceptance.operational_case
    observed = {}

    async def prepare(*args, **kwargs):
        case = await original(*args, **kwargs)
        photo = source_photo(case)
        sha = photo.sha256 if kind == "exact" else "9" * 64
        ahash = photo.ahash if kind == "exact" else f"{int(photo.ahash, 16) ^ 1:016x}"
        slug = case["manifest"]["edition"]["roster_slug"]
        await seed_existing(case["admin"], slug, sha, ahash)
        fresh = await case["store"].preparation_context(
            UUID(case["context"]["account_id"]),
            UUID(case["context"]["edition_id"]),
            [case["manifest"]["manifest_sha256"]],
        )
        assert len(fresh["existing_photo_records"]) == 1
        assert fresh["existing_photo_records"][0]["photo_sha256"] == sha
        case["source_context"] = fresh
        case["context"]["existing_photo_records"] = fresh["existing_photo_records"]
        observed["case"] = case
        return case

    monkeypatch.setattr(acceptance, "operational_case", prepare)
    with pytest.raises(BatchContractError, match="BATCH_BLOCKING_INCIDENT"):
        run = getattr(
            acceptance,
            "test_actual_source_run_analysis_lease_" "budget_and_exception_completion",
        )
        run(full_pg, tmp_path, "capture_supplied_photos")
    assert "case" in observed
    asyncio.run(assert_no_new_capture(full_pg, existing=1))


async def assert_no_new_capture(database, existing=0):
    admin = await acceptance.connect(database)
    try:
        for table, count in (
            ("public.copa_telmex_teams", existing),
            ("public.copa_telmex_players", existing),
            ("public.copa_telmex_team_staff", 0),
            ("ctt_staff_private.capture_results", 0),
            ("ctt_staff_private.review_batch_consumptions", 0),
        ):
            assert await admin.fetchval(f"SELECT count(*) FROM {table}") == count
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.copa_telmex_registration_review_sessions "
                "WHERE committed_team_id IS NOT NULL"
            )
            == 0
        )
    finally:
        await admin.close()


@pytest.mark.parametrize(
    "kind", ["late_duplicate", "late_active_duplicate", "v2_lock_busy"]
)
def test_live_writer_rechecks_inventory_and_v2_photo_lock(
    full_pg, tmp_path, monkeypatch, kind
):
    original_case = acceptance.operational_case
    original_consume = _CaptureTransaction.consume_batch
    observed = {"errors": []}

    async def prepare(*args, **kwargs):
        case = await original_case(*args, **kwargs)
        # This is the exact empty inventory at preparation; a later commit is
        # injected only after the proof and staged payload have been checked.
        case["context"]["existing_photo_records"] = case["source_context"][
            "existing_photo_records"
        ]
        assert case["context"]["existing_photo_records"] == []
        observed["case"] = case
        return case

    async def consume(tx, decision):
        case = observed["case"]
        blocker = None
        if "duplicate" in kind:
            plan = json.loads(tx._payload["review_bytes"])
            player = next(p for p in plan["players"] if p.get("photo_sha256"))
            await seed_existing(
                case["admin"],
                plan["team"]["tournament_slug"],
                player["photo_sha256"],
                player["photo_ahash"],
                state="ACTIVE" if kind == "late_active_duplicate" else "LEGACY_ACTIVE",
            )
        else:
            blocker = await acceptance.connect(full_pg)
            await blocker.execute("BEGIN")
            await blocker.execute("SELECT pg_advisory_xact_lock_shared(1129600050,0)")
        try:
            return await original_consume(tx, decision)
        except asyncpg.InsufficientPrivilegeError as exc:
            observed["errors"].append(str(exc))
            raise
        finally:
            if blocker:
                await blocker.close()

    monkeypatch.setattr(acceptance, "operational_case", prepare)
    monkeypatch.setattr(_CaptureTransaction, "consume_batch", consume)
    with pytest.raises(AssertionError):
        run = getattr(
            acceptance,
            "test_actual_source_run_analysis_lease_" "budget_and_exception_completion",
        )
        run(full_pg, tmp_path, "capture_supplied_photos")
    expected = "duplicate photo" if "duplicate" in kind else "capture busy"
    assert len(observed["errors"]) == 1
    assert expected in observed["errors"][0]
    asyncio.run(assert_no_new_capture(full_pg, existing=int("duplicate" in kind)))


def test_missing_photo_inventory_cannot_prepare_supplied_photo(
    full_pg, tmp_path, monkeypatch
):
    original = acceptance.operational_case

    async def prepare(*args, **kwargs):
        case = await original(*args, **kwargs)
        case["context"].pop("existing_photo_records", None)
        case["source_context"].pop("existing_photo_records")
        return case

    monkeypatch.setattr(acceptance, "operational_case", prepare)
    with pytest.raises(BatchContractError, match="BATCH_PHOTO_INVENTORY_REQUIRED"):
        run = getattr(
            acceptance,
            "test_actual_source_run_analysis_lease_" "budget_and_exception_completion",
        )
        run(full_pg, tmp_path, "capture_supplied_photos")
    asyncio.run(assert_no_new_capture(full_pg))


def test_photo_read_recheck_is_lock_free_and_preserves_optional_absence(
    full_pg, tmp_path
):
    async def check():
        resources = []
        blocker = None
        try:
            case = await acceptance.operational_case(
                full_pg, tmp_path, resources, team_count=1, supplied_photos=True
            )
            photo = source_photo(case)
            slug = case["manifest"]["edition"]["roster_slug"]
            await seed_existing(
                case["admin"], "another-tournament", photo.sha256, photo.ahash
            )
            plan = dict(
                team=dict(id=str(uuid4()), tournament_slug=slug),
                players=[dict(photo_sha256=photo.sha256, photo_ahash=photo.ahash)],
            )
            blocker = await acceptance.connect(full_pg)
            await blocker.execute("BEGIN")
            await blocker.execute("SELECT pg_advisory_xact_lock(1129600050,0)")
            # Gate-side recheck is an MVCC read even while the writer lock is held.
            await asyncio.wait_for(
                case["admin"].execute(
                    "SELECT ctt_staff_private.batch_assert_capture_photo_policy"
                    "($1::jsonb,false)",
                    json.dumps(plan),
                ),
                timeout=2,
            )
            await seed_existing(case["admin"], slug, photo.sha256, photo.ahash)
            plan["players"] = [dict(photo_sha256=None, photo_ahash=None)]
            await case["admin"].execute(
                "SELECT ctt_staff_private.batch_assert_capture_photo_policy"
                "($1::jsonb,false)",
                json.dumps(plan),
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await case["admin"].execute(
                    "SELECT ctt_staff_private.batch_assert_capture_photo_policy"
                    "($1::jsonb,NULL)",
                    json.dumps(plan),
                )
        finally:
            if blocker:
                await blocker.close()
            for pool in reversed(resources):
                await pool.close()

    asyncio.run(check())
