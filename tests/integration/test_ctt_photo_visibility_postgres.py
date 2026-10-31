"""Read gate on actual PostgreSQL, with synthetic protected state fixtures.

The fixture models stored outcomes explicitly. It never implements, bypasses or
claims to verify the separate finality writer/receipt validation protocol.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database/migrations/20261030_ctt_photo_visibility.sql"
)
PHOTO = "a" * 64


@pytest.mark.parametrize("kind", ["player", "staff"])
def test_photo_visibility_requires_exact_committed_capture_and_finality(
    isolated_pg, kind
):
    async def run():
        database = "ctt_photo_" + uuid4().hex
        control = await asyncpg.connect(
            host=isolated_pg, port=int(PORT), user="postgres", database="postgres"
        )
        admin = reader = None
        await control.execute('CREATE DATABASE "' + database + '"')
        try:
            admin = await asyncpg.connect(
                host=isolated_pg, port=int(PORT), user="postgres", database=database
            )
            await admin.execute(
                """
                CREATE SCHEMA ctt_staff_private AUTHORIZATION samchat_ctt_staff_owner;
                CREATE TABLE ctt_staff_private.capture_attempt_anchors
                    (attempt_id uuid, challenge_id uuid);
                CREATE TABLE ctt_staff_private.capture_payloads
                    (attempt_id uuid, effect_bytes bytea);
                CREATE TABLE ctt_staff_private.capture_results (attempt_id uuid);
                CREATE TABLE ctt_staff_private.review_challenge_consumptions
                    (challenge_id uuid);
                CREATE TABLE ctt_staff_private.signed_assertion_guards
                    (challenge_id uuid);
                CREATE TABLE public.copa_telmex_players
                    (id uuid,team_id uuid,photo_path text,governance_state text,
                     finality_receipt_id text);
                CREATE TABLE public.copa_telmex_team_staff
                    (LIKE public.copa_telmex_players);
            """
            )
            tables = [
                "ctt_staff_private.capture_attempt_anchors",
                "ctt_staff_private.capture_payloads",
                "ctt_staff_private.capture_results",
                "ctt_staff_private.review_challenge_consumptions",
                "ctt_staff_private.signed_assertion_guards",
                "public.copa_telmex_players",
                "public.copa_telmex_team_staff",
            ]
            for table in tables:
                await admin.execute(
                    f"ALTER TABLE {table} OWNER TO samchat_ctt_staff_owner"
                )
            await admin.execute(MIGRATION.read_text())
            reader = await asyncpg.connect(
                host=isolated_pg, port=int(PORT), user="devnous_user", database=database
            )

            async def visible(value=PHOTO, connection=None):
                return await (connection or reader).fetchval(
                    "SELECT public.ctt_photo_is_committed($1)", value
                )

            assert await visible() is False  # staging alone has no database evidence
            attempt, challenge, team, row_id = (uuid4() for _ in range(4))
            row = {"id": str(row_id), "photo_path": "ctt-photo:" + PHOTO}
            effect = {
                "team": {"id": str(team)},
                "players": [row] if kind == "player" else [],
                "staff": (
                    [{"presence": "PRESENT", "row": row}]
                    if kind == "staff"
                    else [{"presence": "ABSENT", "row": None}]
                ),
            }
            table = "public.copa_telmex_" + (
                "players" if kind == "player" else "team_staff"
            )
            await admin.execute(
                f"INSERT INTO {table} VALUES($1,$2,$3,'PENDING_FINALITY',NULL)",
                row_id,
                team,
                row["photo_path"],
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.capture_attempt_anchors VALUES($1,$2)",
                attempt,
                challenge,
            )
            await admin.execute(
                "INSERT INTO ctt_staff_private.capture_payloads VALUES($1,$2)",
                attempt,
                json.dumps(effect).encode(),
            )
            assert await visible() is False  # a protected plan is still not a result
            await admin.execute(
                "INSERT INTO ctt_staff_private.capture_results VALUES($1)", attempt
            )
            assert await visible() is False
            await admin.execute(
                "INSERT INTO ctt_staff_private.review_challenge_consumptions "
                "VALUES($1)",
                challenge,
            )
            assert await visible() is False
            await admin.execute(
                "INSERT INTO ctt_staff_private.signed_assertion_guards VALUES($1)",
                challenge,
            )
            assert await visible() is False  # committed pending rows remain hidden
            await admin.execute(f"UPDATE {table} SET governance_state='ACTIVE'")
            assert await visible() is False
            async with admin.transaction():
                await admin.execute(
                    f"UPDATE {table} SET finality_receipt_id='synthetic-final'"
                )
                assert await visible() is False  # uncommitted outcome stays hidden
            assert await visible() is True
            for value in (
                None,
                "",
                "A" * 64,
                "../" + PHOTO,
                "b" * 64,
                "ctt-photo:" + PHOTO,
            ):
                assert await visible(value) is False

            for column, wrong in (
                ("team_id", uuid4()),
                ("id", uuid4()),
                ("photo_path", "ctt-photo:" + "b" * 64),
                ("governance_state", "PENDING_FINALITY"),
                ("finality_receipt_id", None),
            ):
                transaction = admin.transaction()
                await transaction.start()
                try:
                    await admin.execute(f"UPDATE {table} SET {column}=$1", wrong)
                    await admin.execute("SET LOCAL ROLE devnous_user")
                    assert await visible(connection=admin) is False
                finally:
                    await transaction.rollback()
            for missing in (
                "capture_results",
                "capture_payloads",
                "capture_attempt_anchors",
                "review_challenge_consumptions",
                "signed_assertion_guards",
            ):
                transaction = admin.transaction()
                await transaction.start()
                try:
                    await admin.execute(f"DELETE FROM ctt_staff_private.{missing}")
                    await admin.execute("SET LOCAL ROLE devnous_user")
                    assert await visible(connection=admin) is False
                finally:
                    await transaction.rollback()
            if kind == "staff":
                effect["staff"][0]["presence"] = "ABSENT"
                await admin.execute(
                    "UPDATE ctt_staff_private.capture_payloads SET effect_bytes=$1",
                    json.dumps(effect).encode(),
                )
                assert await visible() is False

            for protected in tables:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await reader.fetchval(f"SELECT count(*) FROM {protected}")
            properties = await admin.fetchrow(
                "SELECT p.prosecdef,p.proconfig,r.rolname FROM pg_proc p "
                "JOIN pg_roles r ON r.oid=p.proowner "
                "WHERE p.oid='public.ctt_photo_is_committed(text)'::regprocedure"
            )
            assert dict(properties) == {
                "prosecdef": True,
                "proconfig": ["search_path=pg_catalog"],
                "rolname": "samchat_ctt_staff_owner",
            }
        finally:
            if reader is not None:
                await reader.close()
            if admin is not None:
                await admin.close()
            await control.execute('DROP DATABASE "' + database + '"')
            await control.close()

    asyncio.run(run())
