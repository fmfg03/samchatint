"""Exact modeled Team/Player/Staff rows in a disposable independent database.

Authority keys and identities are synthetic; production schema, v1 and REG-S07
compatibility and gate accreditation are not established by this fixture.
"""

import asyncio
import time

import asyncpg
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from devnous.copa_telmex.models import Player, Team, TeamStaff
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_persistence import (
    PendingCaptureWriter,
)
from test_ctt_staff_security_postgres import PORT

pytest_plugins = ("test_ctt_staff_security_postgres",)


@pytest.fixture(scope="module")
def exact_pg(isolated_pg):
    async def setup():
        admin = await asyncpg.connect(
            host=isolated_pg,
            port=int(PORT),
            user="postgres",
            database="postgres",
        )
        await admin.execute("CREATE DATABASE ctt_effect_exact")
        await admin.execute("CREATE ROLE ctt_effect_writer LOGIN")
        conn = await asyncpg.connect(
            host=isolated_pg,
            port=int(PORT),
            user="postgres",
            database="ctt_effect_exact",
        )
        try:
            for model in (Team, Player, TeamStaff):
                await conn.execute(
                    str(
                        CreateTable(model.__table__).compile(
                            dialect=postgresql.dialect()
                        )
                    )
                )
                await conn.execute(
                    f"GRANT SELECT, INSERT ON {model.__tablename__} "
                    "TO ctt_effect_writer"
                )
        finally:
            await conn.close()
            await admin.close()

    asyncio.run(setup())
    yield isolated_pg

    async def cleanup():
        admin = await asyncpg.connect(
            host=isolated_pg,
            port=int(PORT),
            user="postgres",
            database="postgres",
        )
        try:
            await admin.execute("DROP DATABASE ctt_effect_exact")
            await admin.execute("DROP ROLE ctt_effect_writer")
        finally:
            await admin.close()

    asyncio.run(cleanup())


@pytest.mark.parametrize("corrupt", [False, True])
def test_exact_signed_rows_or_atomic_rollback(exact_pg, monkeypatch, corrupt):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "unit"))
    from test_ctt_staff_capture_authorization import authorization

    async def run():
        admin = await asyncpg.connect(
            host=exact_pg,
            port=int(PORT),
            user="postgres",
            database="ctt_effect_exact",
        )
        writer = await asyncpg.connect(
            host=exact_pg,
            port=int(PORT),
            user="ctt_effect_writer",
            database="ctt_effect_exact",
        )
        try:
            kwargs, body, sign = authorization()
            body.update(
                issued_at=int(time.time()) - 1,
                expires_at=int(time.time()) + 60,
            )
            kwargs["envelope"] = sign(body)
            del kwargs["now_seconds"]
            writer_service = PendingCaptureWriter(
                public_keys=kwargs.pop("public_keys"),
                review_key=kwargs.pop("review_key"),
                effect_key=kwargs.pop("effect_key"),
            )
            with pytest.raises(CaptureDenied):
                await writer_service.persist(writer, **kwargs)
            app = await asyncpg.connect(
                host=exact_pg,
                port=int(PORT),
                user="devnous_user",
                database="ctt_effect_exact",
            )
            try:
                async with app.transaction():
                    with pytest.raises(CaptureDenied):
                        await writer_service.persist(app, **kwargs)
            finally:
                await app.close()
            if corrupt:
                await admin.execute(
                    """
CREATE FUNCTION corrupt_player() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public AS $$
BEGIN NEW.last_name := 'changed by trigger'; RETURN NEW; END $$;
CREATE TRIGGER corrupt_player BEFORE INSERT ON copa_telmex_players
FOR EACH ROW EXECUTE FUNCTION corrupt_player();
"""
                )
            async with writer.transaction():
                if corrupt:
                    with pytest.raises(CaptureDenied):
                        await writer_service.persist(writer, **kwargs)
                else:
                    assert (await writer_service.persist(writer, **kwargs)).startswith(
                        "sha256:"
                    )
            for model in (Team, Player, TeamStaff):
                assert await admin.fetchval(
                    f"SELECT count(*) FROM {model.__tablename__}"
                ) == int(not corrupt)
            if not corrupt:
                assert (
                    await admin.fetchval(
                        "SELECT governance_state FROM copa_telmex_players"
                    )
                    == "PENDING_FINALITY"
                )
                assert (
                    await admin.fetchval(
                        "SELECT governance_state " "FROM copa_telmex_team_staff"
                    )
                    == "PENDING_FINALITY"
                )
        finally:
            if corrupt:
                await admin.execute(
                    "DROP TRIGGER IF EXISTS corrupt_player " "ON copa_telmex_players"
                )
                await admin.execute("DROP FUNCTION IF EXISTS corrupt_player()")
            for model in (TeamStaff, Player, Team):
                await admin.execute(f"DELETE FROM {model.__tablename__}")
            await writer.close()
            await admin.close()

    asyncio.run(run())
