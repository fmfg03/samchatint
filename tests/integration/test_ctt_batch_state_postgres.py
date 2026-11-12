"""Real-role batch state/concurrency tests on a disposable PostgreSQL cluster.

This tests the new state functions, not sports eligibility or canonical roster
finality. Canonical writer integration has its own protected-chain tests.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_review_proposals_postgres import install_recovery
from test_ctt_staff_security_postgres import PORT

full_pg = _full_pg

pytest_plugins = ("test_ctt_staff_security_postgres",)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def batch_pg(full_pg):
    async def install():
        c = await asyncpg.connect(
            host=full_pg[0], port=int(PORT), user="postgres", database=full_pg[1]
        )
        try:
            await c.execute(
                (
                    ROOT / "database/migrations/20261031_ctt_passkey_enrollment.sql"
                ).read_text()
            )
            await install_recovery(c)
            await c.execute(
                (
                    ROOT / "database/migrations/20261031_ctt_review_proposals.sql"
                ).read_text()
            )
            for name in ("sources_v2", "review_state", "consent"):
                try:
                    await c.execute(
                        (
                            ROOT / f"database/migrations/20261109_ctt_batch_{name}.sql"
                        ).read_text()
                    )
                except asyncpg.PostgresError as exc:
                    print(name, exc.position, exc.context)
                    raise
        finally:
            await c.close()

    asyncio.run(install())
    return full_pg[0], int(PORT), full_pg[1]


def test_batch_schema_private_and_owner_separation(batch_pg):
    async def check():
        c = await asyncpg.connect(
            host=batch_pg[0], port=batch_pg[1], user="postgres", database=batch_pg[2]
        )
        try:
            assert not await c.fetchval(
                "SELECT "
                "has_schema_privilege('devnous_user','ctt_staff_private','USAGE')"
            )
            rows = await c.fetch(
                "SELECT p.oid::regprocedure::text AS "
                "signature,p.prosecdef,p.proconfig,r.rolname FROM pg_proc p JOIN "
                "pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON "
                "r.oid=p.proowner WHERE n.nspname='ctt_staff_private' AND "
                "p.proname LIKE 'batch_%'"
            )
            assert len(rows) > 20
            for row in rows:
                assert row["prosecdef"] and row["proconfig"] == [
                    "search_path=pg_catalog"
                ]
                assert row["rolname"] == "samchat_ctt_staff_owner"
                assert not await c.fetchval(
                    "SELECT has_function_privilege('devnous_user',$1,'EXECUTE')",
                    row["signature"],
                )
        finally:
            await c.close()

    asyncio.run(check())


def test_uuid_stability_and_physical_page_overlap(batch_pg):
    from uuid import NAMESPACE_URL, uuid5

    async def check():
        c = await asyncpg.connect(
            host=batch_pg[0], port=batch_pg[1], user="postgres", database=batch_pg[2]
        )
        try:
            expected = uuid5(NAMESPACE_URL, "ctt-test-op")
            assert (
                await c.fetchval(
                    "SELECT ctt_staff_private.batch_uuid($1,$2)",
                    NAMESPACE_URL,
                    "ctt-test-op",
                )
                == expected
            )
            edition, attempt, result, team = uuid4(), uuid4(), uuid4(), uuid4()
            pages = json.dumps([{"pdf_sha256": "a" * 64, "pdf_page": 1}])
            assert await c.fetchval(
                "SELECT "
                "ctt_staff_private.batch_lock_capture_source_pages"
                "($1,$2::jsonb,$3)",
                edition,
                pages,
                expected,
            )
            await c.execute(
                "INSERT INTO "
                "ctt_staff_private.review_batch_page_consumptions("
                "edition_id,pdf_sha256,pdf_page,operation_id,"
                "attempt_id,result_id,team_id) "
                "VALUES($1,$2,1,$3,$4,$5,$6)",
                edition,
                "a" * 64,
                expected,
                attempt,
                result,
                team,
            )
            assert await c.fetchval(
                "SELECT "
                "ctt_staff_private.batch_lock_capture_source_pages"
                "($1,$2::jsonb,$3)",
                edition,
                pages,
                expected,
            )
            with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
                await c.fetchval(
                    "SELECT "
                    "ctt_staff_private.batch_lock_capture_source_pages"
                    "($1,$2::jsonb,$3)",
                    edition,
                    pages,
                    uuid4(),
                )
            with pytest.raises(asyncpg.PostgresError):
                await c.execute(
                    "DELETE FROM ctt_staff_private.review_batch_page_consumptions"
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetchval(
                    "SELECT "
                    "ctt_staff_private.batch_lock_capture_source_pages"
                    "($1,$2::jsonb,$3)",
                    edition,
                    json.dumps(
                        [{"pdf_sha256": "a" * 64, "pdf_page": 1, "approved": True}]
                    ),
                    expected,
                )
        finally:
            await c.close()

    asyncio.run(check())


def test_empty_rollback_restores_canonical_guard_bytes(batch_pg):
    async def check():
        c = await asyncpg.connect(
            host=batch_pg[0], port=batch_pg[1], user="postgres", database=batch_pg[2]
        )
        try:
            baseline = await c.fetch(
                "SELECT signature,original_definition FROM "
                "ctt_staff_private.batch_writer_baseline"
            )
            await c.execute(
                (
                    ROOT / "database/migrations/20261109_ctt_batch_consent_rollback.sql"
                ).read_text()
            )
            for row in baseline:
                assert (
                    await c.fetchval(
                        "SELECT pg_get_functiondef(to_regprocedure($1))",
                        row["signature"],
                    )
                    == row["original_definition"]
                )
            await c.execute(
                (
                    ROOT
                    / "database/migrations/20261109_ctt_batch_review_state_rollback.sql"
                ).read_text()
            )
            assert (
                await c.fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_batch_runs')"
                )
                is None
            )
            assert (
                await c.fetchval(
                    "SELECT to_regclass('ctt_staff_private.review_source_manifests_v2')"
                )
                is not None
            )
        finally:
            await c.close()

    asyncio.run(check())
