"""Catalogue/configuration races on an exact disposable PostgreSQL instance."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import devnous.gastos  # noqa: F401
from devnous.gastos.routes import admin_routes
from samchat.budgets import service


@pytest_asyncio.fixture
async def database(monkeypatch):
    local = "postgresql+asyncpg://postgres@127.0.0.1:55479/postgres"
    ci = "postgresql+asyncpg://test_user:test_password@127.0.0.1:55471/devnous_test"
    if os.environ.get("SAMCHAT_WEEKLY_TEST_DATABASE_URL") == local:
        server = local
    elif (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("TEST_AMEX_DATABASE_URL") == ci
    ):
        server = ci
    else:
        pytest.skip("Requires the exact disposable weekly-test PostgreSQL server")
    admin = await asyncpg.connect(server.replace("+asyncpg", ""))
    name = "budget_scope_config_test_" + uuid4().hex
    await admin.execute(
        f'CREATE DATABASE "{name}" WITH TEMPLATE template0 '
        "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
    )
    engine = create_async_engine(server.rsplit("/", 1)[0] + "/" + name)
    monkeypatch.setattr(admin_routes, "_ensure_tournaments_admin_schema", AsyncMock())
    monkeypatch.setattr(
        admin_routes, "_require_legacy_gastos_project_mutable", AsyncMock()
    )
    monkeypatch.setattr(
        admin_routes, "_find_local_tournament_by_name", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(service, "_budget_catalog_scope_index", lambda: {})
    tid = uuid4()
    try:
        async with engine.begin() as connection:
            raw = await connection.get_raw_connection()
            await raw.driver_connection.execute(
                """
                CREATE TABLE tournaments (
                    id uuid PRIMARY KEY, name varchar(200) NOT NULL,
                    description varchar(500), active boolean NOT NULL DEFAULT true,
                    display_order int DEFAULT 0, cuenta_contable_relacionada varchar(200),
                    etapas jsonb, categorias jsonb, form_visibility_areas jsonb,
                    created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp DEFAULT now()
                );
                CREATE TABLE cuentas_contables (
                    id uuid PRIMARY KEY, codigo text, nombre text, activo boolean
                );
                CREATE TABLE budget_concepts (
                    id uuid PRIMARY KEY, tournament_id uuid REFERENCES tournaments(id),
                    tournament_code text, tournament_name text, concept_name text,
                    concept_key text, budget_direction text, active boolean,
                    source text, metadata jsonb, cuenta_contable_id uuid,
                    pasivo_cuenta_contable_id uuid, cxc_cuenta_contable_id uuid,
                    created_by_empleado_id uuid, created_at timestamp DEFAULT now(),
                    updated_at timestamp DEFAULT now()
                );
            """
            )
            await connection.execute(
                text(
                    "INSERT INTO tournaments(id,name,etapas,categorias) VALUES(:id,'Fixture',CAST(:etapas AS jsonb),'[]')"
                ),
                {"id": tid, "etapas": json.dumps(["Montaje", "Operación"])},
            )
        yield engine, tid
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


class Request:
    async def form(self):
        return {"etapas": "Montaje", "active": "on"}


async def remove_phase(session, tid):
    return await admin_routes.update_tournament(
        tournament_id=tid,
        request=Request(),
        name="Fixture",
        description=None,
        display_order=0,
        cuenta_contable_relacionada=None,
        etapas="Montaje",
        categorias=None,
        current_empleado=SimpleNamespace(rol="superadmin"),
        _csrf=None,
        session=session,
    )


async def wait_for_database_block(engine, pid):
    async def probe():
        async with engine.connect() as connection:
            while True:
                blocked = await connection.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                )
                if blocked:
                    return
                await asyncio.sleep(0.01)

    await asyncio.wait_for(probe(), timeout=5)


async def new_or_updated_scope(session, tid, mode, concept_id):
    if mode == "create":
        return await service.create_budget_concept(
            session,
            tournament_id=str(tid),
            concept_name="Scoped operation",
            scope_labels=["Operación"],
            ensure_schema=False,
            commit=False,
        )
    return await service.update_budget_concept(
        session,
        concept_id=concept_id,
        scope_labels=["Operación"],
        ensure_schema=False,
        commit=False,
    )


async def existing_global(engine, tid, mode):
    if mode == "create":
        return None
    async with AsyncSession(engine) as session:
        row = await service.create_budget_concept(
            session,
            tournament_id=str(tid),
            concept_name="Initially global",
            scope_labels=[],
            ensure_schema=False,
        )
        return row["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "update"])
async def test_catalogue_scope_commit_prevents_concurrent_config_removal(
    database, mode
):
    engine, tid = database
    concept_id = await existing_global(engine, tid, mode)
    async with AsyncSession(engine, expire_on_commit=False) as catalogue, AsyncSession(
        engine, expire_on_commit=False
    ) as config:
        await new_or_updated_scope(catalogue, tid, mode, concept_id)
        # Catalogue owner holds FOR SHARE on the real project row until commit.
        pid = await config.scalar(text("SELECT pg_backend_pid()"))
        attempt = asyncio.create_task(remove_phase(config, tid))
        try:
            await wait_for_database_block(engine, pid)
            assert not attempt.done()
            await catalogue.commit()
            response = await asyncio.wait_for(attempt, timeout=5)
            error = parse_qs(urlsplit(response.headers["location"]).query)["error_msg"][
                0
            ]
            assert "Reasigne primero su alcance" in error
        finally:
            if not attempt.done():
                attempt.cancel()
                await asyncio.gather(attempt, return_exceptions=True)
    async with engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT etapas FROM tournaments WHERE id=:id"), {"id": tid}
        ) == ["Montaje", "Operación"]
        metadata = await connection.scalar(text("SELECT metadata FROM budget_concepts"))
        assert metadata["applicable_phase_labels"] == ["Operación"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "update"])
async def test_config_removal_commit_rejects_waiting_catalogue_scope(database, mode):
    engine, tid = database
    concept_id = await existing_global(engine, tid, mode)
    ready, release = asyncio.Event(), asyncio.Event()

    class PausedCommitSession(AsyncSession):
        async def commit(self):
            ready.set()
            await release.wait()
            return await super().commit()

    async with PausedCommitSession(
        engine, expire_on_commit=False
    ) as config, AsyncSession(engine, expire_on_commit=False) as catalogue:
        removal = asyncio.create_task(remove_phase(config, tid))
        assignment = None
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
            pid = await catalogue.scalar(text("SELECT pg_backend_pid()"))
            assignment = asyncio.create_task(
                new_or_updated_scope(catalogue, tid, mode, concept_id)
            )
            await wait_for_database_block(engine, pid)
            assert not assignment.done()
            release.set()
            response = await asyncio.wait_for(removal, timeout=5)
            assert "success_msg" in parse_qs(
                urlsplit(response.headers["location"]).query
            )
            with pytest.raises(ValueError, match="ya no está configurado"):
                await asyncio.wait_for(assignment, timeout=5)
            await catalogue.rollback()
        finally:
            release.set()
            tasks = [task for task in (removal, assignment) if task and not task.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    async with engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT etapas FROM tournaments WHERE id=:id"), {"id": tid}
        ) == ["Montaje"]
        scopes = list(
            (
                await connection.execute(text("SELECT metadata FROM budget_concepts"))
            ).scalars()
        )
        assert len(scopes) == (1 if mode == "update" else 0)
        assert all(item["scope_mode"] == "global" for item in scopes)


@pytest.mark.asyncio
async def test_stale_repeatable_read_catalogue_snapshot_cannot_orphan_scope(database):
    from sqlalchemy.exc import DBAPIError

    engine, tid = database
    ready, release = asyncio.Event(), asyncio.Event()

    class PausedCommitSession(AsyncSession):
        async def commit(self):
            ready.set()
            await release.wait()
            return await super().commit()

    async with PausedCommitSession(engine) as config, AsyncSession(engine) as catalogue:
        await catalogue.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await catalogue.execute(
            text("SELECT etapas FROM tournaments WHERE id=:id"), {"id": tid}
        )
        pid = await catalogue.scalar(text("SELECT pg_backend_pid()"))
        removal = asyncio.create_task(remove_phase(config, tid))
        assignment = None
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
            assignment = asyncio.create_task(
                new_or_updated_scope(catalogue, tid, "create", None)
            )
            await wait_for_database_block(engine, pid)
            release.set()
            await asyncio.wait_for(removal, timeout=5)
            with pytest.raises(DBAPIError) as error:
                await asyncio.wait_for(assignment, timeout=5)
            assert getattr(error.value.orig, "sqlstate", None) == "40001"
            await catalogue.rollback()
        finally:
            release.set()
            tasks = [task for task in (removal, assignment) if task and not task.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT count(*) FROM budget_concepts")) == 0
        )
        assert await connection.scalar(
            text("SELECT etapas FROM tournaments WHERE id=:id"), {"id": tid}
        ) == ["Montaje"]
