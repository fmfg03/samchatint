"""Actual model graph and native PostgreSQL, only a synthetic installation."""

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend_harness import IDS, make_app
from backend_web_helpers import create, login
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, insert, text

from devnous.gastos.models import (
    Adjunto,
    Aprobacion,
    CFDIReport,
    CuentaDeGastos,
    Documento,
    ExpenseReport,
    Tournament,
)
from devnous.gastos.services.project_authorization_service import (
    ensure_project_authorization_schema,
)
from samchat.batisenal_synthetic_protocol import SyntheticProtocol

SOCKET = os.environ.get("BATISENAL_TEST_SOCKET", "")
if not SOCKET:
    pytest.skip(
        "Run scripts/batisenal/run_isolated_acceptance.py", allow_module_level=True
    )
if not Path(SOCKET).is_absolute() or not Path(SOCKET).is_dir():
    raise RuntimeError(
        "BATISENAL_TEST_SOCKET must name the runner-owned Unix socket directory"
    )
SQL = Path("database/migrations/20261025_batisenal_v01_synthetic_protocol.sql")


@pytest.fixture
async def app(monkeypatch):
    monkeypatch.setenv("BATISENAL_ENABLED", "1")
    schema = "bs_test_v01_" + uuid4().hex
    app = await make_app(
        None,
        database_url="postgresql+asyncpg://fixture@/postgres?host=" + SOCKET,
        schema=schema,
    )
    tables = {}

    def visit(table):
        if table.fullname in tables:
            return
        tables[table.fullname] = table
        for foreign in table.foreign_keys:
            visit(foreign.column.table)

    for model in (
        Documento,
        ExpenseReport,
        Adjunto,
        CuentaDeGastos,
        CFDIReport,
        Tournament,
        Aprobacion,
    ):
        visit(model.__table__)
    async with app.state.engine.begin() as c:
        await c.run_sync(
            lambda sync: Documento.metadata.create_all(
                sync, tables=list(tables.values())
            )
        )
    async with app.state.maker() as session:
        await ensure_project_authorization_schema(session)
        await session.commit()
    app.state.native_schema = schema
    app.state.native_sync_url = "postgresql+psycopg://fixture@/postgres?host=" + SOCKET
    try:
        yield app
    finally:
        await app.state.engine.dispose()


async def bind_graph(app, case_id, *, prerequisites=True, agent_kind="synthetic_stub"):
    report, project = uuid4(), uuid4()
    async with app.state.engine.begin() as c:
        await c.execute(
            insert(Tournament).values(
                id=project, name="SIMULACION Plataforma Sports", active=True
            )
        )
        await c.execute(
            insert(Documento).values(
                id=report,
                empleado_id=IDS["alice"],
                tipo="INFORME",
                numero_referencia="SIM-V01-001",
                estado="borrador",
                torneo_id=project,
                currency="MXN",
                monto_total=1200,
            )
        )
    await migrate_policy(app.state.engine, prerequisites=prerequisites)
    async with app.state.engine.begin() as c:
        await c.execute(
            text("INSERT INTO support_batisenal_v01_policy_guard VALUES(true,0)")
        )
        raw = await c.get_raw_connection()
        await raw.driver_connection.execute(
            SQL.read_text().replace("BEGIN;", "").replace("COMMIT;", "")
        )
        await raw.driver_connection.execute(
            Path("database/migrations/20261109_batisenal_real_client_binding.sql")
            .read_text()
            .replace("BEGIN;", "")
            .replace("COMMIT;", "")
        )
        await c.execute(
            text(
                "INSERT INTO support_batisenal_v01_case_binding"
                " VALUES(true,:case,:report,:actor,:project,'synthetic-installation',"
                "'acme','demo',"
                ":hash,true,0,:kind,:client,:issuer,:resource,:registration)"
            ),
            dict(
                case=case_id,
                report=report,
                actor=IDS["alice"],
                project=project,
                hash="0" * 64,
                kind=agent_kind,
                client="synthetic-client" if agent_kind == "ai_agent" else None,
                issuer=(
                    "https://samchat.example.test/oauth"
                    if agent_kind == "ai_agent"
                    else None
                ),
                resource=(
                    "https://samchat.example.test/mcp"
                    if agent_kind == "ai_agent"
                    else None
                ),
                registration="a" * 64 if agent_kind == "ai_agent" else None,
            ),
        )
    schema = app.state.native_schema
    sync_url = app.state.native_sync_url
    kwargs = {
        "connect_args": {
            "options": (
                "-c search_path="
                + schema
                + " -c lock_timeout=2000 -c statement_timeout=4000"
            )
        }
    }
    raw_engine = create_engine(sync_url, **kwargs)
    composed = create_engine(sync_url, **kwargs)
    protocol = SyntheticProtocol(schema, case_id, enabled=True)
    return SimpleNamespace(
        app=app,
        schema=schema,
        case_id=case_id,
        report=report,
        project=project,
        raw=raw_engine,
        engine=composed,
        protocol=protocol,
        agent_kind=agent_kind,
    )


@pytest.fixture
async def graph(app, request):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as web:
        await login(web, "alice")
        case_id = await create(web)
    value = await bind_graph(
        app, case_id, agent_kind=getattr(request, "param", "synthetic_stub")
    )
    value.protocol.install(app.state.engine.sync_engine, synchronous=False)
    value.protocol.install(value.engine, synchronous=True)
    try:
        yield value
    finally:
        value.engine.dispose()
        value.raw.dispose()


MIGRATIONS = Path("database/migrations")


async def migrate_policy(engine, *, prerequisites=False):
    names = (
        [
            "20260919_agent_action_receipts_v01.sql",
            "20261001_private_oauth_storage_review.sql",
            "20261024_batisenal_pilot.sql",
        ]
        if prerequisites
        else []
    ) + ["20261025_batisenal_v01_policy_fence.sql"]
    async with engine.begin() as c:
        raw = await c.get_raw_connection()
        for name in names:
            await raw.driver_connection.execute(
                (MIGRATIONS / name)
                .read_text()
                .replace("BEGIN;", "")
                .replace("COMMIT;", "")
            )
