"""Atomic configuration CLI acceptance on an isolated Unix-socket PostgreSQL.

Uses actual canonical authority APIs, real transactions and independent readback.
No production DSN or business database is read; fixture DDL precedes tool use.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve()
unit = load_module(
    "policy_tool_unit_fixtures",
    HERE.parents[1] / "unit/gastos/test_provisional_approval_policy_tool.py",
)
native = load_module(
    "policy_tool_native_cluster",
    HERE.with_name("test_telegram_finance_recovery_postgres.py"),
)
before = unit.before
isolated_postgres_url = native.isolated_postgres_url
tool = unit.tool


@pytest_asyncio.fixture
async def pg_tool(isolated_postgres_url, before, monkeypatch, tmp_path):
    schema = "policy_tool_" + uuid4().hex
    admin = create_async_engine(isolated_postgres_url, hide_parameters=True)
    async with admin.begin() as connection:
        assert await connection.scalar(text("SHOW listen_addresses")) == ""
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    def engine_factory(url, **kwargs):
        return create_async_engine(
            url,
            **kwargs,
            connect_args={"server_settings": {"search_path": schema}},
        )

    engine = engine_factory(isolated_postgres_url, hide_parameters=True)
    definitions = [
        "CREATE TABLE empleados(id uuid PRIMARY KEY,nombre text,rol text,activo boolean)",
        "CREATE TABLE tournaments(id uuid PRIMARY KEY,name text,active boolean)",
        "CREATE TABLE authorization_positions(position_key varchar(100) PRIMARY KEY,label varchar(200) NOT NULL,active boolean NOT NULL)",
        "CREATE TABLE authorization_position_assignments(position_key varchar(100) REFERENCES authorization_positions,empleado_id uuid REFERENCES empleados,active boolean,PRIMARY KEY(position_key,empleado_id))",
        "CREATE TABLE project_authorization_rules(tournament_id uuid PRIMARY KEY REFERENCES tournaments,eligible_position_keys jsonb,requires_operations_reference boolean,active boolean)",
        "CREATE TABLE documentos(id uuid PRIMARY KEY,numero_referencia text,tipo text DEFAULT 'SOLICITUD',estado text,referencia_operaciones text,torneo_id uuid REFERENCES tournaments,empleado_id uuid,beneficiario_empleado_id uuid,monto_total numeric(14,2),aprobado_en timestamptz,pagado_en timestamptz)",
        "CREATE TABLE documento_authorization_routes(documento_id uuid PRIMARY KEY,eligible_position_keys jsonb,eligible_empleado_ids jsonb,requires_operations_reference boolean,source varchar(100),resolved_at timestamptz DEFAULT now())",
        "CREATE TABLE aprobaciones(id uuid PRIMARY KEY,tipo_entidad text DEFAULT 'documento',entidad_id uuid,aprobador_id uuid REFERENCES empleados,accion text,comentario text,fecha timestamptz DEFAULT now())",
    ]
    async with engine.begin() as connection:
        for sql in definitions:
            await connection.execute(text(sql))
        table_names = {
            "employees": "empleados",
            "projects": "tournaments",
            "positions": "authorization_positions",
            "assignments": "authorization_position_assignments",
            "rules": "project_authorization_rules",
            "document": "documentos",
            "routes": "documento_authorization_routes",
            "approvals": "aprobaciones",
        }
        for key, table in table_names.items():
            for original in before[key]:
                row = original.copy()
                # Let PostgreSQL generate its real timestamp, rather than a unit label.
                row.pop("resolved_at", None)
                columns = list(row)
                values = []
                for column in columns:
                    value = row[column]
                    if isinstance(value, list):
                        row[column] = json.dumps(value)
                        values.append(f"CAST(:{column} AS jsonb)")
                    elif column.endswith("_id") or column == "id":
                        values.append(f"CAST(:{column} AS uuid)")
                    else:
                        values.append(f":{column}")
                await connection.execute(
                    text(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join(values)})"
                    ),
                    row,
                )

    statements = []

    def tracked_engine(url, **kwargs):
        result = engine_factory(url, **kwargs)

        @event.listens_for(result.sync_engine, "before_cursor_execute")
        def capture(conn, cursor, statement, parameters, context, many):
            statements.append(statement)

        return result

    monkeypatch.setenv(
        "DATABASE_URL",
        isolated_postgres_url._replace(host=None, port=None)
        .update_query_dict(
            {
                "host": isolated_postgres_url.host,
                "port": str(isolated_postgres_url.port),
            }
        )
        .render_as_string(hide_password=False),
    )
    monkeypatch.setattr(tool, "create_async_engine", tracked_engine)
    args = argparse.Namespace(
        apply=False,
        expected_hash=None,
        expected_commit=None,
        actor_id=UUID(tool.ACTOR_ID),
        env_file=None,
        output_dir=tmp_path / "evidence",
    )
    try:
        yield SimpleNamespace(engine=engine, args=args, statements=statements)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def read_state(pg):
    async with AsyncSession(pg.engine) as session:
        return await tool.snapshot(session)


async def preview(pg):
    state = await read_state(pg)
    report = await tool.run(pg.args)
    assert report["status"] == "READ_ONLY_PREVIEW"
    assert state == await read_state(pg)
    pg.args.apply = True
    pg.args.expected_hash = report["plan_sha256"]
    pg.args.expected_commit = report["source_commit"]
    return state


@pytest.mark.asyncio
async def test_real_cli_atomic_apply_committed_readback_and_single_audit(pg_tool):
    pg = pg_tool
    state = await preview(pg)
    receipt = await tool.run(pg.args)
    assert receipt["status"] == "COMMITTED_VERIFIED"
    assert (
        receipt["configuration_positions"],
        receipt["configuration_assignments"],
        receipt["project_rules"],
    ) == (4, 8, 15)
    committed = await read_state(pg)
    assert committed["document"] == state["document"]
    assert committed["document"][0]["monto_total"] == "250.00"
    assert committed["document"][0]["referencia_operaciones"] == "363"
    assert committed["document"][0]["estado"] == "enviado"
    assert [
        r for r in committed["assignments"] if r["position_key"] not in tool.POSITIONS
    ] == state["assignments"]
    selected = next(
        r for r in committed["routes"] if r["documento_id"] == tool.DOCUMENT_ID
    )
    assert selected["eligible_empleado_ids"] == [tool.GN]
    assert selected["source"].startswith("p20261023:project:")
    added = [r for r in committed["approvals"] if r["accion"] == tool.AUDIT_ACTION]
    assert len(added) == 1
    assert added[0]["aprobador_id"] == tool.ACTOR_ID
    payload = json.loads(added[0]["comentario"])
    assert payload["audit_context"]["plan_sha256"] == receipt["plan_sha256"]
    assert json.loads((pg.args.output_dir / "receipt.json").read_text()) == receipt
    assert json.loads((pg.args.output_dir / "after.json").read_text()) == committed
    assert all(
        path.stat().st_mode & 0o777 == 0o600 for path in pg.args.output_dir.iterdir()
    )
    assert not any(
        sql.lstrip()
        .lower()
        .startswith(("create", "alter", "drop", "delete", "truncate"))
        for sql in pg.statements
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hash", "state", "context"])
async def test_stale_hash_final_state_or_context_blocks_all_mutations(pg_tool, change):
    pg = pg_tool
    await preview(pg)
    if change == "hash":
        pg.args.expected_hash = "0" * 64
    else:
        async with pg.engine.begin() as connection:
            if change == "state":
                await connection.execute(
                    text(
                        "UPDATE documentos SET estado='aprobado' WHERE id=CAST(:id AS uuid)"
                    ),
                    {"id": tool.DOCUMENT_ID},
                )
            else:
                await connection.execute(
                    text(
                        "UPDATE documentos SET beneficiario_empleado_id=CAST(:person AS uuid) WHERE id=CAST(:id AS uuid)"
                    ),
                    {"id": tool.DOCUMENT_ID, "person": tool.OLOF},
                )
    before_attempt = await read_state(pg)
    with pytest.raises(ValueError):
        await tool.run(pg.args)
    assert await read_state(pg) == before_attempt


@pytest.mark.asyncio
async def test_error_after_actual_writes_rolls_back_configuration_route_and_audit(
    pg_tool, monkeypatch
):
    pg = pg_tool
    state = await preview(pg)
    original = tool.verify_after

    def fail_after_verify(before, after, plan):
        original(before, after, plan)
        raise ValueError("Synthetic evidence write/check failure before commit")

    monkeypatch.setattr(tool, "verify_after", fail_after_verify)
    with pytest.raises(ValueError, match="Synthetic evidence"):
        await tool.run(pg.args)
    assert await read_state(pg) == state
    assert any(sql.lstrip().lower().startswith("insert") for sql in pg.statements)
    assert not (pg.args.output_dir / "receipt.json").exists()
