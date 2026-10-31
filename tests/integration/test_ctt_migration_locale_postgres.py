"""Schema-only locale portability; never connects to an operational database."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_legacy_write_boundary_postgres import command
from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database/migrations"
BIN = "/usr/lib/postgresql/16/bin/"
ORDER = (
    "20261028_ctt_staff_security",
    "20261028_ctt_staff_review_state",
    "20261030_ctt_capture_anchors",
    "20261030_ctt_signed_assertion_guard",
    "20261030_ctt_capture_payloads",
    "20261030_ctt_staff_effect_gate",
    "20261030_ctt_transport_nonces",
    "20261030_ctt_platform_passkey",
    "20261030_ctt_assertion_issuer",
    "20261030_ctt_source_admission",
    "20261030_ctt_review_plan_scope",
    "20261030_ctt_legacy_write_boundary",
    "20261030_ctt_photo_visibility",
    "20261031_ctt_capture_finality",
    "20261031_ctt_passkey_enrollment",
    "20261031_ctt_assertion_proof_recovery",
    "20261031_ctt_review_proposals",
)


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
@pytest.mark.parametrize("drift", [None, "constraint", "index"])
def test_all_migrations_keep_exact_schema_guard(isolated_pg, locale, drift):
    source_value = os.environ.get("CTT_SCHEMA_ONLY_DIRECTORY")
    if not source_value:
        pytest.skip("Set CTT_SCHEMA_ONLY_DIRECTORY to reviewed schema-only export")
    source = Path(source_value).resolve(strict=True)
    assert source.is_relative_to(Path("/tmp"))
    receipt = json.loads((source / "receipt.json").read_text())
    assert receipt["schema_only"] is True
    for name, digest in receipt["hashes"].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest

    async def run():
        database = "ctt_locale_" + uuid4().hex
        admin = await asyncpg.connect(
            host=isolated_pg, port=int(PORT), user="postgres", database="postgres"
        )
        conn = None
        try:
            for role in json.loads((source / "role-names.json").read_text()):
                if not await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role
                ):
                    quoted = '"' + role.replace('"', '""') + '"'
                    await admin.execute("CREATE ROLE " + quoted + " NOLOGIN")
            assert locale in {"C", "en_US.UTF-8"}
            await admin.execute(
                f"CREATE DATABASE {database} TEMPLATE template0 "
                f"LC_COLLATE '{locale}' LC_CTYPE '{locale}' ENCODING 'UTF8'"
            )
            conn = await asyncpg.connect(
                host=isolated_pg, port=int(PORT), user="postgres", database=database
            )
            await conn.execute("CREATE EXTENSION pgcrypto")
            base = ["-h", isolated_pg, "-p", PORT, "-U", "postgres", "-d", database]
            command(
                [BIN + "pg_restore", "--exit-on-error", "--section=pre-data"]
                + base
                + [str(source / "ctt.dump")]
            )
            command(
                [BIN + "psql", "-X", "-v", "ON_ERROR_STOP=1"]
                + base
                + [
                    "-c",
                    "SET search_path=public,pg_catalog",
                    "-f",
                    str(source / "functions.sql"),
                ]
            )
            command(
                [BIN + "pg_restore", "--exit-on-error", "--section=post-data"]
                + base
                + [str(source / "ctt.dump")]
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM "
                    "public.copa_telmex_registration_review_sessions"
                )
                == 0
            )
            for name in ORDER:
                if name == "20261030_ctt_legacy_write_boundary" and drift:
                    target = (
                        "public.copa_telmex_registration_human_field_edit_proposals"
                    )
                    if drift == "constraint":
                        await conn.execute(
                            f"ALTER TABLE {target} ADD CONSTRAINT "
                            "ctt_unknown_guard CHECK (true)"
                        )
                    else:
                        await conn.execute(
                            f"CREATE INDEX ctt_unknown_guard ON {target}(session_id)"
                        )
                    with pytest.raises(
                        asyncpg.PostgresError, match="definition differs"
                    ):
                        await conn.execute((MIGRATIONS / (name + ".sql")).read_text())
                    await conn.execute("ROLLBACK")
                    assert (
                        await conn.fetchval(
                            "SELECT pg_get_userbyid(relowner) FROM pg_class "
                            "WHERE oid='public.copa_telmex_teams'::regclass"
                        )
                        == "devnous_user"
                    )
                    return
                await conn.execute((MIGRATIONS / (name + ".sql")).read_text())
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_proposals"
                )
                == 0
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_proposal_baseline"
                )
                == 3
            )
            for name in reversed(ORDER):
                await conn.execute((MIGRATIONS / (name + ".rollback.sql")).read_text())
            assert (
                await conn.fetchval("SELECT to_regnamespace('ctt_staff_private')::text")
                is None
            )
        finally:
            if conn:
                await conn.close()
            await admin.execute("DROP DATABASE IF EXISTS " + database)
            await admin.close()

    asyncio.run(run())
