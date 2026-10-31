"""Candidate staff closure on an isolated PostgreSQL cluster.

No staff writer is enabled and tenant/human authority remains unproven.
"""

import asyncio
import hashlib
import hmac
import os
import pwd
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "database/migrations/20261028_ctt_staff_security.sql"
ROLLBACK = ROOT / "database/migrations/20261028_ctt_staff_security.rollback.sql"
BIN = Path("/usr/lib/postgresql/16/bin")
PORT = "55492"
TEAM = UUID("11111111-1111-4111-8111-111111111111")
STAFF = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(scope="module")
def isolated_pg():
    if not (BIN / "initdb").is_file():
        pytest.skip("PostgreSQL 16 binaries unavailable")
    root = Path(tempfile.mkdtemp(prefix="ctt-staff-security-test-"))
    data, socket = root / "data", root / "socket"
    socket.mkdir()
    prefix = []
    if os.geteuid() == 0:
        try:
            owner = pwd.getpwnam("postgres")
        except KeyError:
            pytest.skip("postgres OS account unavailable")
        os.chown(root, owner.pw_uid, owner.pw_gid)
        os.chown(socket, owner.pw_uid, owner.pw_gid)
        prefix = ["runuser", "-u", "postgres", "--"]
    started = False

    def command(args):
        subprocess.run(
            args,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    try:
        command(
            prefix
            + [
                str(BIN / "initdb"),
                "-D",
                str(data),
                "-U",
                "postgres",
                "--encoding=UTF8",
                "--locale=C",
                "--auth=trust",
            ]
        )
        command(
            prefix
            + [
                str(BIN / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(root / "server.log"),
                "-o",
                f"-k {socket} -p {PORT} -c listen_addresses=",
                "-w",
                "start",
            ]
        )
        started = True
        base = [
            str(BIN / "psql"),
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            str(socket),
            "-p",
            PORT,
            "-U",
            "postgres",
            "-d",
            "postgres",
        ]
        subprocess.run(
            base,
            input="""
CREATE EXTENSION pgcrypto;
CREATE ROLE devnous_user LOGIN;
GRANT CREATE ON SCHEMA public TO devnous_user;
CREATE TABLE public.copa_telmex_teams(
    id uuid PRIMARY KEY, name text NOT NULL
);
ALTER TABLE public.copa_telmex_teams OWNER TO devnous_user;
CREATE TABLE public.copa_telmex_team_staff(
    id uuid PRIMARY KEY,
    team_id uuid NOT NULL REFERENCES public.copa_telmex_teams(id)
        ON DELETE CASCADE,
    staff_slot integer NOT NULL CHECK(staff_slot IN (1,2)),
    role varchar(40) NOT NULL,
    first_name varchar(100), last_name varchar(200), birth_date date,
    curp varchar(18), photo_path varchar(500), evidence jsonb NOT NULL,
    governance_state varchar(30) NOT NULL,
    governance_draft_id varchar(80) NOT NULL,
    governance_draft_version integer NOT NULL,
    governance_decision_id varchar(80) NOT NULL,
    roster_draft_binding varchar(80) NOT NULL,
    preauthorization_receipt_id varchar(120) NOT NULL,
    finality_receipt_id varchar(120),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.copa_telmex_team_staff OWNER TO devnous_user;
INSERT INTO public.copa_telmex_teams
VALUES ('11111111-1111-4111-8111-111111111111', 'Synthetic');
INSERT INTO public.copa_telmex_team_staff(
    id, team_id, staff_slot, role, evidence, governance_state,
    governance_draft_id, governance_draft_version,
    governance_decision_id, roster_draft_binding,
    preauthorization_receipt_id
) VALUES (
    '22222222-2222-4222-8222-222222222222',
    '11111111-1111-4111-8111-111111111111',
    1, 'director_tecnico', '{}', 'PENDING_FINALITY',
    'draft', 1, 'decision', 'binding', 'receipt'
);
""",
            text=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        command(base + ["-f", str(MIGRATION)])
        yield str(socket)
    finally:
        if started:
            subprocess.run(
                prefix
                + [
                    str(BIN / "pg_ctl"),
                    "-D",
                    str(data),
                    "-m",
                    "immediate",
                    "-w",
                    "stop",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        shutil.rmtree(root, ignore_errors=True)


async def connect(socket, user="postgres"):
    return await asyncpg.connect(
        user=user, database="postgres", host=socket, port=int(PORT)
    )


def test_app_role_cannot_write_disable_or_reach_crypto(isolated_pg):
    async def run():
        c = await connect(isolated_pg, user="devnous_user")
        try:
            for sql in (
                ("UPDATE public.copa_telmex_team_staff SET first_" "name='synthetic'"),
                (
                    "INSERT INTO public.copa_telmex_team_staff(id,team_id,"
                    "staff_slot,role,evidence,governance_state,governance_"
                    "draft_id,governance_draft_version,governance_"
                    "decision_id,roster_draft_binding,preauthorization_"
                    "receipt_id) VALUES ('33333333-3333-4333-8333-33333333"
                    "3333','11111111-1111-4111-8111-111111111111',2,"
                    "'auxiliar','{}','PENDING_FINALITY','draft',1,"
                    "'decision','binding','receipt')"
                ),
                "DELETE FROM public.copa_telmex_team_staff",
                "TRUNCATE public.copa_telmex_team_staff",
                (
                    "ALTER TABLE public.copa_telmex_team_staff DISABLE "
                    "TRIGGER ctt_staff_write_closed"
                ),
                "SELECT * FROM ctt_staff_private.verification_keys",
                (
                    "SELECT ctt_staff_private.verify_hmac_v2('fake',"
                    "decode('00','hex'),decode('00','hex'))"
                ),
                "SET ROLE samchat_ctt_staff_owner",
            ):
                with pytest.raises(asyncpg.PostgresError):
                    await c.execute(sql)
            assert (
                await c.fetchval("SELECT count(*) FROM public.copa_telmex_team_staff")
                == 1
            )
        finally:
            await c.close()

    asyncio.run(run())


def test_team_delete_and_truncate_cascade_cannot_remove_staff(isolated_pg):
    async def run():
        c = await connect(isolated_pg, user="devnous_user")
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(
                    "DELETE FROM public.copa_telmex_teams WHERE id=$1", TEAM
                )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute("TRUNCATE public.copa_telmex_teams CASCADE")
            assert (
                await c.fetchval("SELECT count(*) FROM public.copa_telmex_teams") == 1
            )
            assert (
                await c.fetchval("SELECT count(*) FROM public.copa_telmex_team_staff")
                == 1
            )
        finally:
            await c.close()

    asyncio.run(run())


def test_private_framing_and_hmac_match_python_and_resist_shadows(isolated_pg):
    async def run():
        c = await connect(isolated_pg)
        key = bytes(range(32))
        values = ["v"] * 32
        values[0] = "kid-a"
        values[4] = "Oaxaca á 🦅"
        values[7] = ""
        values[25] = None
        expected = b"ctt-staff-sql-proof-v2"
        for value in values:
            raw = None if value is None else value.encode("utf-8")
            expected += (-1 if raw is None else len(raw)).to_bytes(
                4, "big", signed=True
            )
            if raw is not None:
                expected += raw
        try:
            now = datetime.now(timezone.utc)
            await c.execute(
                (
                    "INSERT INTO ctt_staff_private.verification_keys "
                    "VALUES ($1,$2,$3,$4,false)"
                ),
                "kid-a",
                key,
                now - timedelta(minutes=1),
                now + timedelta(minutes=1),
            )
            actual = await c.fetchval(
                "SELECT ctt_staff_private.frame_v2($1::text[])", values
            )
            assert actual == expected
            tag = hmac.new(key, expected, hashlib.sha256).digest()
            assert (
                await c.fetchval(
                    "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                    "kid-a",
                    expected,
                    tag,
                )
                is True
            )
            await c.execute(
                (
                    "UPDATE ctt_staff_private.verification_keys SET "
                    "revoked=true WHERE kid='kid-a'"
                )
            )
            assert not await c.fetchval(
                "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                "kid-a",
                expected,
                tag,
            )
            await c.execute(
                (
                    "UPDATE ctt_staff_private.verification_keys SET "
                    "revoked=false, not_after=$1 WHERE kid='kid-a'"
                ),
                now - timedelta(seconds=1),
            )
            assert not await c.fetchval(
                "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                "kid-a",
                expected,
                tag,
            )
            await c.execute(
                (
                    "UPDATE ctt_staff_private.verification_keys SET not_"
                    "after=$1 WHERE kid='kid-a'"
                ),
                now + timedelta(minutes=1),
            )
            for offset in (0, 31):
                altered = bytearray(tag)
                altered[offset] ^= 1
                assert (
                    await c.fetchval(
                        "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                        "kid-a",
                        expected,
                        bytes(altered),
                    )
                    is False
                )
            assert (
                await c.fetchval(
                    "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                    "unknown",
                    expected,
                    tag,
                )
                is False
            )
            assert (
                await c.fetchval(
                    "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                    "kid-a",
                    expected,
                    b"bad",
                )
                is False
            )
            attacker = await connect(isolated_pg, user="devnous_user")
            try:
                await attacker.execute(
                    (
                        "CREATE FUNCTION public.hmac(varchar,varchar,"
                        "varchar) RETURNS bytea LANGUAGE SQL AS 'SELECT "
                        "decode(repeat(''00'',32),''hex'')'"
                    )
                )
                await attacker.execute(
                    (
                        "CREATE FUNCTION pg_temp.hmac(bytea,bytea,text) "
                        "RETURNS bytea LANGUAGE SQL AS 'SELECT "
                        "decode(repeat(''00'',32),''hex'')'"
                    )
                )
                # Temporary grants are synthetic test setup, never production.
                await c.execute(
                    "GRANT USAGE ON SCHEMA ctt_staff_private TO devnous_user"
                )
                await c.execute(
                    (
                        "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                        "verify_hmac_v2(text,bytea,bytea) TO devnous_user"
                    )
                )
                assert (
                    await attacker.fetchval(
                        "SELECT ctt_staff_private.verify_hmac_v2($1,$2,$3)",
                        "kid-a",
                        expected,
                        tag,
                    )
                    is True
                )
                await c.execute(
                    (
                        "REVOKE EXECUTE ON FUNCTION ctt_staff_private."
                        "verify_hmac_v2(text,bytea,bytea) FROM devnous_"
                        "user"
                    )
                )
                await c.execute(
                    ("REVOKE USAGE ON SCHEMA ctt_staff_private FROM " "devnous_user")
                )
            finally:
                await attacker.close()
            await c.execute("DELETE FROM ctt_staff_private.verification_keys")
        finally:
            await c.close()

    asyncio.run(run())


def test_rollback_refuses_existing_staff_and_keys(isolated_pg):
    async def run():
        c = await connect(isolated_pg)
        try:
            with pytest.raises(asyncpg.PostgresError, match="rollback forbidden"):
                await c.execute(ROLLBACK.read_text())
            await c.execute("ROLLBACK")
            assert (
                await c.fetchval(
                    (
                        "SELECT pg_get_userbyid(relowner) FROM pg_class "
                        "WHERE oid='public.copa_telmex_team_"
                        "staff'::regclass"
                    )
                )
                == "samchat_ctt_staff_owner"
            )
        finally:
            await c.close()

    asyncio.run(run())


@pytest.mark.parametrize("grantee", ["public", "app", "inherited"])
def test_preflight_rejects_column_grants(isolated_pg, grantee):
    async def run():
        admin = await connect(isolated_pg)
        dbname = "ctt_acl_" + grantee
        if grantee == "inherited":
            await admin.execute("CREATE ROLE ctt_acl_group NOLOGIN")
            await admin.execute("GRANT ctt_acl_group TO devnous_user")
        try:
            await admin.execute("CREATE DATABASE " + dbname)
            c = await asyncpg.connect(
                user="postgres",
                database=dbname,
                host=isolated_pg,
                port=int(PORT),
            )
            try:
                await c.execute(
                    (
                        "CREATE TABLE public.copa_telmex_team_staff(id "
                        "integer, first_name text)"
                    )
                )
                await c.execute(
                    (
                        "ALTER TABLE public.copa_telmex_team_staff OWNER "
                        "TO devnous_user"
                    )
                )
                recipient = {
                    "public": "PUBLIC",
                    "app": "devnous_user",
                    "inherited": "ctt_acl_group",
                }[grantee]
                await c.execute(
                    ("GRANT UPDATE(first_name) ON public.copa_telmex_" "team_staff TO ")
                    + recipient
                )
                with pytest.raises(
                    asyncpg.PostgresError,
                    match="column ACL requires separate reconciliation",
                ):
                    await c.execute(MIGRATION.read_text())
                await c.execute("ROLLBACK")
                assert (
                    await c.fetchval(
                        (
                            "SELECT pg_get_userbyid(relowner) FROM pg_"
                            "class WHERE oid='public.copa_telmex_team_"
                            "staff'::regclass"
                        )
                    )
                    == "devnous_user"
                )
                assert (
                    await c.fetchval("SELECT to_regnamespace('ctt_staff_private')")
                    is None
                )
            finally:
                await c.close()
        finally:
            await admin.execute("DROP DATABASE IF EXISTS " + dbname)
            if grantee == "inherited":
                await admin.execute("REVOKE ctt_acl_group FROM devnous_user")
                await admin.execute("DROP ROLE ctt_acl_group")
            await admin.close()

    asyncio.run(run())


def test_team_owner_can_remove_fk_with_ddl_documented_limitation(isolated_pg):
    async def run():
        c = await connect(isolated_pg, user="devnous_user")
        try:
            transaction = c.transaction()
            await transaction.start()
            await c.execute(
                (
                    "ALTER TABLE public.copa_telmex_teams DROP CONSTRAINT "
                    "copa_telmex_teams_pkey CASCADE"
                )
            )
            await c.execute("DELETE FROM public.copa_telmex_teams WHERE id=$1", TEAM)
            assert (
                await c.fetchval(
                    (
                        "SELECT count(*) FROM pg_constraint WHERE "
                        "conrelid='public.copa_telmex_team_"
                        "staff'::regclass AND contype='f'"
                    )
                )
                == 0
            )
            assert (
                await c.fetchval("SELECT count(*) FROM public.copa_telmex_team_staff")
                == 1
            )
            await transaction.rollback()
            assert (
                await c.fetchval("SELECT count(*) FROM public.copa_telmex_teams") == 1
            )
            assert (
                await c.fetchval(
                    (
                        "SELECT count(*) FROM pg_constraint WHERE "
                        "conrelid='public.copa_telmex_team_"
                        "staff'::regclass AND contype='f'"
                    )
                )
                == 1
            )
        finally:
            await c.close()

    asyncio.run(run())


@pytest.mark.parametrize("direction", ["member", "granted"])
def test_preflight_rejects_existing_owner_role_membership(isolated_pg, direction):
    async def run():
        admin = await connect(isolated_pg)
        dbname = "ctt_owner_" + direction
        await admin.execute("CREATE ROLE ctt_other_role NOLOGIN")
        grant = (
            "GRANT ctt_other_role TO samchat_ctt_staff_owner"
            if direction == "member"
            else "GRANT samchat_ctt_staff_owner TO ctt_other_role"
        )
        revoke = grant.replace("GRANT ", "REVOKE ", 1).replace(" TO ", " FROM ", 1)
        try:
            await admin.execute(grant)
            await admin.execute("CREATE DATABASE " + dbname)
            c = await asyncpg.connect(
                user="postgres",
                database=dbname,
                host=isolated_pg,
                port=int(PORT),
            )
            try:
                await c.execute("CREATE EXTENSION pgcrypto")
                await c.execute(
                    "CREATE TABLE public.copa_telmex_team_staff(id integer)"
                )
                await c.execute(
                    (
                        "ALTER TABLE public.copa_telmex_team_staff OWNER "
                        "TO devnous_user"
                    )
                )
                with pytest.raises(
                    asyncpg.PostgresError, match="owner role is not isolated"
                ):
                    await c.execute(MIGRATION.read_text())
                await c.execute("ROLLBACK")
                assert (
                    await c.fetchval("SELECT to_regnamespace('ctt_staff_private')")
                    is None
                )
            finally:
                await c.close()
        finally:
            await admin.execute("DROP DATABASE IF EXISTS " + dbname)
            await admin.execute(revoke)
            await admin.execute("DROP ROLE ctt_other_role")
            await admin.close()

    asyncio.run(run())


def test_preflight_rejects_app_membership_in_pgcrypto_owner(isolated_pg):
    async def run():
        admin = await connect(isolated_pg)
        dbname = "ctt_extension_owner"
        await admin.execute("GRANT postgres TO devnous_user")
        try:
            await admin.execute("CREATE DATABASE " + dbname)
            c = await asyncpg.connect(
                user="postgres",
                database=dbname,
                host=isolated_pg,
                port=int(PORT),
            )
            try:
                await c.execute("CREATE EXTENSION pgcrypto")
                await c.execute(
                    "CREATE TABLE public.copa_telmex_team_staff(id integer)"
                )
                await c.execute(
                    "ALTER TABLE public.copa_telmex_team_staff " "OWNER TO devnous_user"
                )
                with pytest.raises(
                    asyncpg.PostgresError,
                    match="trusted pgcrypto unavailable",
                ):
                    await c.execute(MIGRATION.read_text())
                await c.execute("ROLLBACK")
                assert (
                    await c.fetchval("SELECT to_regnamespace('ctt_staff_private')")
                    is None
                )
            finally:
                await c.close()
        finally:
            await admin.execute("DROP DATABASE IF EXISTS " + dbname)
            await admin.execute("REVOKE postgres FROM devnous_user")
            await admin.close()

    asyncio.run(run())


def test_preflight_rejects_nondefault_owner_acl(isolated_pg):
    async def run():
        admin = await connect(isolated_pg)
        dbname = "ctt_owner_acl"
        try:
            await admin.execute("CREATE DATABASE " + dbname)
            c = await asyncpg.connect(
                user="postgres",
                database=dbname,
                host=isolated_pg,
                port=int(PORT),
            )
            try:
                await c.execute(
                    "CREATE TABLE public.copa_telmex_team_staff(id integer)"
                )
                await c.execute(
                    "ALTER TABLE public.copa_telmex_team_staff " "OWNER TO devnous_user"
                )
                await c.execute(
                    "REVOKE SELECT ON public.copa_telmex_team_staff "
                    "FROM devnous_user"
                )
                with pytest.raises(
                    asyncpg.PostgresError,
                    match="owner ACL requires separate reconciliation",
                ):
                    await c.execute(MIGRATION.read_text())
                await c.execute("ROLLBACK")
                assert (
                    await c.fetchval("SELECT to_regnamespace('ctt_staff_private')")
                    is None
                )
            finally:
                await c.close()
        finally:
            await admin.execute("DROP DATABASE IF EXISTS " + dbname)
            await admin.close()

    asyncio.run(run())


def test_rollback_succeeds_only_after_synthetic_records_and_keys_are_removed(
    isolated_pg,
):
    async def run():
        c = await connect(isolated_pg)
        try:
            original_acl = await c.fetchval(
                "SELECT COALESCE(original_acl::aclitem[], "
                "acldefault('r', original_owner::regrole))::text "
                "FROM ctt_staff_private.migration_metadata"
            )
            await c.execute(
                (
                    "ALTER TABLE public.copa_telmex_team_staff DISABLE "
                    "TRIGGER ctt_staff_write_closed"
                )
            )
            await c.execute("DELETE FROM public.copa_telmex_team_staff")
            await c.execute(
                (
                    "ALTER TABLE public.copa_telmex_team_staff ENABLE "
                    "TRIGGER ctt_staff_write_closed"
                )
            )
            now = datetime.now(timezone.utc)
            await c.execute(
                (
                    "INSERT INTO ctt_staff_private.verification_keys "
                    "VALUES($1,$2,$3,$4,false)"
                ),
                "synthetic",
                bytes(range(32)),
                now - timedelta(minutes=1),
                now + timedelta(minutes=1),
            )
            with pytest.raises(asyncpg.PostgresError, match="rollback forbidden"):
                await c.execute(ROLLBACK.read_text())
            await c.execute("ROLLBACK")
            await c.execute("DELETE FROM ctt_staff_private.verification_keys")
            await c.execute(
                (
                    "CREATE VIEW public.synthetic_external_dependency AS "
                    "SELECT ctt_staff_private.frame_v2(ARRAY[]::text[]) "
                    "AS payload"
                )
            )
            with pytest.raises(asyncpg.DependentObjectsStillExistError):
                await c.execute(ROLLBACK.read_text())
            await c.execute("ROLLBACK")
            assert (
                await c.fetchval(
                    ("SELECT to_regclass('public.synthetic_external_" "dependency')")
                )
                is not None
            )
            assert (
                await c.fetchval(
                    (
                        "SELECT pg_get_userbyid(relowner) FROM pg_class "
                        "WHERE oid='public.copa_telmex_team_"
                        "staff'::regclass"
                    )
                )
                == "samchat_ctt_staff_owner"
            )
            await c.execute("DROP VIEW public.synthetic_external_dependency")
            await c.execute(ROLLBACK.read_text())
            restored_acl = await c.fetchval(
                "SELECT COALESCE(relacl, acldefault('r',relowner))::text "
                "FROM pg_class WHERE "
                "oid='public.copa_telmex_team_staff'::regclass"
            )
            assert restored_acl == original_acl
            assert (
                await c.fetchval(
                    (
                        "SELECT pg_get_userbyid(relowner) FROM pg_class "
                        "WHERE oid='public.copa_telmex_team_"
                        "staff'::regclass"
                    )
                )
                == "devnous_user"
            )
            assert (
                await c.fetchval("SELECT to_regnamespace('ctt_staff_private')") is None
            )
            assert (
                await c.fetchval(
                    (
                        "SELECT count(*) FROM pg_roles WHERE "
                        "rolname='samchat_ctt_staff_owner'"
                    )
                )
                == 0
            )
        finally:
            await c.close()

    asyncio.run(run())
