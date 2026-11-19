"""Synthetic app using real Batiseñal routes, models, and profile resolution.

Only the enclosing dashboard, login and unrelated routers are excluded.
"""

from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.models import (
    AccessControlRule,
    Empleado,
    SupportTicket,
    SupportTicketComment,
)
from devnous.gastos.routes import batisenal_routes as routes
from devnous.gastos.routes import dependencies, support_routes
from devnous.gastos.services import batisenal_agent_service as agent_service
from devnous.gastos.services import batisenal_service as service

__all__ = ["IDS", "make_app", "routes", "service"]

ROOT = Path(__file__).resolve().parents[3]

IDS = {
    name: uuid5(NAMESPACE_URL, "synthetic/" + name)
    for index, name in enumerate(
        [
            "alice",
            "bob",
            "support",
            "other_org",
            "other_project",
            "no_scope",
            "inactive",
        ],
        1,
    )
}
TOKENS = {
    "alice": ["batisenal:acme:demo:request"],
    "bob": ["batisenal:acme:demo:request"],
    "support": ["batisenal:acme:demo:support"],
    "other_org": ["batisenal:other:demo:support"],
    "other_project": ["batisenal:acme:other:support"],
    "no_scope": [],
    "inactive": ["batisenal:acme:demo:request"],
}


async def make_app(db_path, *, database_url=None, schema=None):
    import json

    engine = create_async_engine(
        database_url or "sqlite+aiosqlite:///" + str(db_path),
        **(
            {"connect_args": {"server_settings": {"search_path": schema}}}
            if schema
            else {}
        ),
    )
    async with engine.begin() as conn:
        if schema:
            # Schema is generated internally by the native test fixture, never user
            # input.
            assert schema.startswith("bs_test_") and schema.replace("_", "").isalnum()
            await conn.execute(text('CREATE SCHEMA "' + schema + '"'))
        for table in [
            Empleado.__table__,
            AccessControlRule.__table__,
            SupportTicket.__table__,
            SupportTicketComment.__table__,
            service.cases,
            service.events,
            agent_service.drafts,
            agent_service.publications,
            agent_service.activity,
        ]:
            if database_url and table in (
                service.cases,
                service.events,
                agent_service.drafts,
                agent_service.publications,
                agent_service.activity,
            ):
                continue
            await conn.run_sync(
                lambda connection, table=table: table.create(
                    connection, checkfirst=True
                )
            )
        if database_url:
            raw = await conn.get_raw_connection()
            sql = (
                ROOT / "database/migrations/20261023_support_batisenal.sql"
            ).read_text()
            # The surrounding transaction owns fixture DDL; execute the actual migration
            # body.
            await raw.driver_connection.execute(
                sql.replace("BEGIN;", "").replace("COMMIT;", "")
            )
            await raw.driver_connection.execute(
                (ROOT / "database/migrations/20261023_support_batisenal_agent.sql")
                .read_text()
                .replace("BEGIN;", "")
                .replace("COMMIT;", "")
            )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS access_profiles (id TEXT PRIMARY KEY,"
                " permissions TEXT, active BOOLEAN)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS empleado_access_profiles (empleado_id "
                + ("UUID" if database_url else "TEXT")
                + ", profile_id TEXT)"
            )
        )
        for name, identifier in IDS.items():
            found = (
                await conn.execute(select(Empleado.id).where(Empleado.id == identifier))
            ).first()
            if found:
                continue
            await conn.execute(
                insert(Empleado.__table__).values(
                    id=identifier,
                    nombre="Synthetic " + name,
                    rol=(
                        "superadmin"
                        if name in {"support", "other_org", "other_project", "no_scope"}
                        else "empleado"
                    ),
                    activo=name != "inactive",
                )
            )
            await conn.execute(
                text("INSERT INTO access_profiles VALUES (:id, :permissions, true)"),
                dict(id=name, permissions=json.dumps(TOKENS[name])),
            )
            await conn.execute(
                text(
                    "INSERT INTO empleado_access_profiles VALUES (:employee, :profile)"
                ),
                dict(employee=str(identifier), profile=name),
            )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.state.engine, app.state.maker = engine, maker
    # Synthetic local secret; never a real login or production session.
    app.add_middleware(
        SessionMiddleware,
        secret_key="synthetic-fixture-only-000000000",
        same_site="strict",
    )

    async def session_dep():
        async with maker() as session:
            yield session

    async def employee_dep(request: Request):
        name = request.session.get("fixture_user")
        if name not in IDS:
            raise HTTPException(401, "LOGIN_REQUIRED")
        return SimpleNamespace(
            id=str(IDS[name]),
            activo=name != "inactive",
            rol=(
                "superadmin"
                if name in {"support", "other_org", "other_project", "no_scope"}
                else "empleado"
            ),
        )

    app.dependency_overrides[dependencies.get_current_empleado] = employee_dep
    app.dependency_overrides[dependencies.get_db_session] = session_dep
    app.include_router(support_routes.router)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/fixture/login/{name}")
    async def login(name: str, request: Request):
        if name not in IDS:
            raise HTTPException(404)
        request.session.clear()
        request.session["fixture_user"] = name
        request.session["batisenal_csrf"] = (
            "synthetic-csrf-000000000000000000000000000000000000"
        )
        return {"synthetic": True}

    return app
