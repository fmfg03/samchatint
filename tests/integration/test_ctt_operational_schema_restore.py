"""Restore actual schema definitions only; never copies production rows.

Set CTT_SCHEMA_ONLY_DIRECTORY to the private reviewed snapshot directory.
Restores into a fresh disposable database on the isolated test cluster.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess

import asyncpg
import pytest

from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)


def _run(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        pytest.fail(result.stderr.decode("utf-8", "replace")[:3000])


def test_operational_schema_snapshot_restores_without_data(isolated_pg):
    configured = os.environ.get("CTT_SCHEMA_ONLY_DIRECTORY")
    if not configured:
        pytest.skip("Set CTT_SCHEMA_ONLY_DIRECTORY to reviewed schema snapshot")
    root = Path(configured).resolve(strict=True)
    if not root.is_relative_to(Path("/tmp")):
        pytest.fail("Schema snapshot must be private and isolated under /tmp")
    receipt = json.loads((root / "receipt.json").read_text())
    assert receipt["schema_only"] is True
    for name in ("ctt.dump", "functions.sql", "role-names.json"):
        assert (
            hashlib.sha256((root / name).read_bytes()).hexdigest()
            == receipt["hashes"][name]
        )

    async def run():
        admin = await asyncpg.connect(
            host=isolated_pg,
            port=int(PORT),
            user="postgres",
            database="postgres",
        )
        created_roles = []
        try:
            for name in json.loads((root / "role-names.json").read_text()):
                if not await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)",
                    name,
                ):
                    quoted = '"' + name.replace('"', '""') + '"'
                    await admin.execute("CREATE ROLE " + quoted + " NOLOGIN")
                    created_roles.append(quoted)
            await admin.execute("CREATE DATABASE ctt_operational_schema")
            conn = await asyncpg.connect(
                host=isolated_pg,
                port=int(PORT),
                user="postgres",
                database="ctt_operational_schema",
            )
            try:
                await conn.execute("CREATE EXTENSION pgcrypto")
                base = [
                    "-h",
                    isolated_pg,
                    "-p",
                    PORT,
                    "-U",
                    "postgres",
                    "-d",
                    "ctt_operational_schema",
                ]
                for section in ("pre-data", "post-data"):
                    if section == "post-data":
                        _run(
                            [
                                "/usr/lib/postgresql/16/bin/psql",
                                "-X",
                                "-v",
                                "ON_ERROR_STOP=1",
                                *base,
                                "-c",
                                "SET search_path=public,pg_catalog",
                                "-f",
                                str(root / "functions.sql"),
                            ],
                        )
                    _run(
                        [
                            "/usr/lib/postgresql/16/bin/pg_restore",
                            "--exit-on-error",
                            "--section=" + section,
                            *base,
                            str(root / "ctt.dump"),
                        ],
                    )
                tables = await conn.fetch(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND "
                    "(tablename LIKE 'copa_telmex_%' "
                    "OR tablename IN ('tournaments','empleados'))"
                )
                assert len(tables) == 35
                for item in tables:
                    # Names come only from the PostgreSQL catalog.
                    quoted = '"' + item["tablename"].replace('"', '""') + '"'
                    assert (
                        await conn.fetchval("SELECT count(*) FROM public." + quoted)
                        == 0
                    )
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM pg_trigger " "WHERE NOT tgisinternal"
                    )
                    >= 27
                )
                # Semantic compatibility test only: temporary ownership and
                # grants below are not a deployable v1-compatible migration.
                import time
                import sys
                from devnous.copa_telmex.staff_capture_effect import (
                    bind_capture_effect,
                )
                from devnous.copa_telmex.staff_capture_persistence import (
                    PendingCaptureWriter,
                )

                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
                try:
                    from test_ctt_staff_capture_authorization import (
                        authorization,
                    )
                finally:
                    sys.path.pop(0)
                kwargs, body, sign = authorization()
                private = json.loads(kwargs["effect_bytes"])
                for entity, rows, revision in (
                    ("team", [private["team"]], 1),
                    ("player", private["players"], 0),
                ):
                    table = (
                        "copa_telmex_teams"
                        if entity == "team"
                        else "copa_telmex_players"
                    )
                    for row in rows:
                        row["postcommit_revision"] = revision
                        row["postcommit_snapshot_hash"] = await conn.fetchval(
                            "SELECT regs07_snapshot_hash("
                            f"regs07_{entity}_snapshot("
                            "jsonb_populate_record("
                            f"NULL::{table},$1::jsonb)))",
                            json.dumps(row),
                        )
                kwargs["effect_bytes"] = json.dumps(
                    private,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
                body["effect_binding"] = bind_capture_effect(
                    kwargs["effect_bytes"], kwargs["effect_key"]
                )
                body.update(
                    issued_at=int(time.time()) - 1,
                    expires_at=int(time.time()) + 60,
                )
                kwargs["envelope"] = sign(body)
                del kwargs["now_seconds"]
                service = PendingCaptureWriter(
                    public_keys=kwargs.pop("public_keys"),
                    review_key=kwargs.pop("review_key"),
                    effect_key=kwargs.pop("effect_key"),
                )
                await admin.execute("CREATE ROLE ctt_ops_owner NOLOGIN")
                await admin.execute("CREATE ROLE ctt_ops_writer LOGIN")
                created_roles.extend(["ctt_ops_writer", "ctt_ops_owner"])
                for table in (
                    "copa_telmex_teams",
                    "copa_telmex_players",
                    "copa_telmex_team_staff",
                ):
                    await conn.execute(
                        f"ALTER TABLE public.{table} OWNER TO ctt_ops_owner"
                    )
                    await conn.execute(
                        f"REVOKE ALL ON public.{table} " "FROM devnous_user, PUBLIC"
                    )
                    await conn.execute(
                        f"GRANT SELECT, INSERT ON public.{table} " "TO ctt_ops_writer"
                    )
                function_names = await conn.fetch(
                    "SELECT p.oid::regprocedure::text AS signature "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname IN ("
                    "'regs07_canonical_jsonb','regs07_team_snapshot',"
                    "'regs07_player_snapshot','regs07_snapshot_hash',"
                    "'enforce_registration_human_edit_atomicity',"
                    "'enforce_registration_review_draft_append_only',"
                    "'enforce_regs07_execution_atomicity',"
                    "'guard_registration_review_asset_immutability',"
                    "'regs07_append_committed_version','regs07_append_genesis',"
                    "'regs07_guard_committed_mutation','regs07_initialize_player',"
                    "'regs07_initialize_team',"
                    "'reject_registration_human_field_authority_mutation',"
                    "'reject_registration_ocr_evidence_mutation',"
                    "'reject_registration_page_composition_mutation',"
                    "'reject_regs07_authority_mutation')"
                )
                assert len(function_names) == 17
                for function in function_names:
                    signature = function["signature"]
                    await conn.execute(
                        f"ALTER FUNCTION {signature} OWNER TO ctt_ops_owner"
                    )
                    await conn.execute(
                        f"ALTER FUNCTION {signature} "
                        "SET search_path=pg_catalog,public"
                    )
                await conn.execute(
                    "GRANT INSERT ON "
                    "copa_telmex_registration_postcommit_entity_versions "
                    "TO ctt_ops_writer"
                )
                writer = await asyncpg.connect(
                    host=isolated_pg,
                    port=int(PORT),
                    user="ctt_ops_writer",
                    database="ctt_operational_schema",
                )
                try:
                    async with writer.transaction():
                        token = await service.persist(writer, **kwargs)
                        assert token.startswith("sha256:")
                finally:
                    await writer.close()
                for table in (
                    "copa_telmex_teams",
                    "copa_telmex_players",
                    "copa_telmex_team_staff",
                ):
                    assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 1
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM "
                        "copa_telmex_registration_postcommit_entity_versions"
                    )
                    == 1
                )
            finally:
                await conn.close()
        finally:
            await admin.execute("DROP DATABASE IF EXISTS ctt_operational_schema")
            for name in created_roles:
                await admin.execute("DROP ROLE " + name)
            await admin.close()

    asyncio.run(run())
