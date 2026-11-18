"""Candidate canonical typed registry installation on a real restored schema."""

import asyncio
from pathlib import Path

import asyncpg
from test_ctt_full_runtime_postgres import full_pg as _full_pg
from test_ctt_review_proposals_postgres import install_recovery
from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg
ROOT = Path(__file__).resolve().parents[2]


def test_batch_bridge_installs_without_authority_or_parallel_results(full_pg):
    async def run():
        conn = await asyncpg.connect(
            host=full_pg[0],
            port=int(PORT),
            user="postgres",
            database="ctt_full_runtime",
        )
        try:
            await conn.execute(
                (
                    ROOT / "database/migrations/20261031_ctt_passkey_enrollment.sql"
                ).read_text()
            )
            await install_recovery(conn)
            await conn.execute(
                (
                    ROOT / "database/migrations/20261031_ctt_review_proposals.sql"
                ).read_text()
            )
            for name in ("sources_v2", "review_state", "consent"):
                await conn.execute(
                    (
                        ROOT / f"database/migrations/20261109_ctt_batch_{name}.sql"
                    ).read_text()
                )
            assert await conn.fetchval(
                "SELECT to_regclass('ctt_staff_private.capture_attempt_registry') "
                "IS NOT NULL"
            )
            consume_body = await conn.fetchval(
                "SELECT prosrc FROM pg_proc WHERE oid="
                "'ctt_staff_private.batch_consume_child(uuid,uuid,uuid,bigint)'"
                "::regprocedure"
            )
            assert "batch_capture_payloads" in consume_body
            assert "legacy_v2_reservations" in consume_body
            assert await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid="
                "'ctt_staff_private.capture_results'::regclass AND "
                "tgname='ctt_batch_source_result_consume' AND tgenabled='O')"
            )
            target = await conn.fetchval(
                "SELECT confrelid::regclass::text FROM pg_constraint WHERE "
                "conrelid='ctt_staff_private.capture_payloads'::regclass AND "
                "contype='f'"
            )
            assert target == "ctt_staff_private.capture_attempt_registry"
            result_target = await conn.fetchval(
                "SELECT confrelid::regclass::text FROM pg_constraint WHERE "
                "conrelid='ctt_staff_private.capture_results'::regclass AND "
                "contype='f' AND "
                "confrelid='ctt_staff_private.capture_payloads'::regclass"
            )
            assert result_target == "ctt_staff_private.capture_payloads"
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_attempt_registry"
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_services"
                )
                == 0
            )
            assert not await conn.fetchval(
                "SELECT "
                "has_function_privilege('devnous_user',"
                "'ctt_staff_private.batch_consume_child(uuid,uuid,uuid,bigint)',"
                "'EXECUTE')"
            )
            assert not await conn.fetchval(
                "SELECT "
                "has_table_privilege('devnous_user',"
                "'ctt_staff_private.capture_attempt_registry',"
                "'INSERT,UPDATE,DELETE')"
            )
        finally:
            await conn.close()

    asyncio.run(run())
