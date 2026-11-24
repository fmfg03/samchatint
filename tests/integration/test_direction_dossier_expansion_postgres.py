"""Real PostgreSQL acceptance for isolated extended captures and expense lines."""

from datetime import date
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive.dossier_finance import read_expense_lines
from samchat.client_executive.dossier_records import (
    DossierRecordError,
    read_records,
    save_record,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "database" / "migrations"


@pytest.mark.asyncio
async def test_expense_associations_use_canonical_scope_and_keep_legacy_reader_usable(
    dossier_postgres,
):
    url, connection = dossier_postgres
    owner = await asyncpg.connect(**connection)
    schema = f"dossier_expansion_{uuid4().hex}"
    engine = create_async_engine(
        url, connect_args={**connection, "server_settings": {"search_path": schema}}
    )
    project, employee, report, line, second, off_year = (uuid4() for _ in range(6))
    up = (MIGRATIONS / "20261124_direction_dossier_details.sql").read_text()
    down = (MIGRATIONS / "20261124_direction_dossier_details.rollback.sql").read_text()
    try:
        await owner.execute(
            "DO $$ BEGIN CREATE ROLE devnous_user NOLOGIN; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        await owner.execute(f'SET search_path TO "{schema}"')
        await owner.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO devnous_user')
        await owner.execute("""
            CREATE TABLE tournaments (id uuid PRIMARY KEY, active boolean);
            CREATE TABLE empleados (id uuid PRIMARY KEY, activo boolean);
            CREATE TABLE documentos (
                id uuid PRIMARY KEY, torneo_id uuid, edicion int, fase text, tipo text,
                categorias jsonb, numero_referencia text, concepto_pago text
            );
            CREATE TABLE expense_reports (
                id uuid PRIMARY KEY, numero_referencia text, gasto_cantidad numeric,
                currency text, fecha date, updated_at timestamptz, fase_torneo text,
                categorias jsonb, edicion int, estado_gasto text, informe_documento_id uuid,
                documento_id uuid
            );
            GRANT SELECT, UPDATE ON tournaments, documentos, expense_reports TO devnous_user;
            GRANT SELECT ON empleados TO devnous_user;
        """)
        await owner.execute(
            (MIGRATIONS / "20261118_direction_living_dossier.sql").read_text()
        )
        await owner.execute(up)
        await owner.execute(up)
        await owner.execute(down)
        await owner.execute(up)
        await owner.execute("INSERT INTO tournaments VALUES ($1, true)", project)
        await owner.execute("INSERT INTO empleados VALUES ($1, true)", employee)
        await owner.execute(
            """
            INSERT INTO documentos VALUES
            ($1, $2, 2026, 'Fase Nacional', 'INFORME', '["Juvenil"]', 'INF-1', NULL)
            """,
            report,
            project,
        )
        await owner.executemany(
            """
            INSERT INTO expense_reports VALUES (
                $1, $2, $3::numeric, 'MXN', $4, now(), 'Nacional',
                '["Juvenil"]', $5, 'activo', $6, NULL
            )
            """,
            [
                (line, "G1", 24.25, date(2027, 1, 5), 2026, report),
                (second, "G2", 100, date(2027, 1, 5), 2026, report),
                (off_year, "G3", 999, date(2027, 1, 5), 2027, report),
            ],
        )
        params = dict(
            tournament_id=str(project),
            edition_year=2026,
            employee_id=str(employee),
            scope={"phase": "nacional", "entity": "", "category": "Juvenil"},
            evidence_reference="Informe de prueba",
        )
        fields = {
            "expense_id": str(line),
            "concept": "hotel_liquidacion",
            "service_type": "Salón",
        }
        factory = async_sessionmaker(engine)
        async with factory() as session:
            await session.execute(text("SET LOCAL ROLE devnous_user"))
            with pytest.raises(DossierRecordError, match="partida no coincide"):
                await save_record(
                    session,
                    **params,
                    kind="expense_line_link",
                    fields={**fields, "expense_id": str(off_year)},
                )
            request_id = str(uuid4())
            one = await save_record(
                session,
                **params,
                kind="expense_line_link",
                fields=fields,
                request_id=request_id,
            )
            assert (
                await save_record(
                    session,
                    **params,
                    kind="expense_line_link",
                    fields=fields,
                    request_id=request_id,
                )
                == one
            )
            await save_record(
                session,
                **params,
                kind="expense_line_link",
                fields={
                    **fields,
                    "expense_id": str(second),
                    "service_type": "Hospedaje",
                },
            )
            with pytest.raises(DossierRecordError, match="partidas vinculadas"):
                await save_record(
                    session,
                    **params,
                    kind="financial_link",
                    fields={"document_id": str(report), "concept": "hotel_liquidacion"},
                )
            await session.commit()
        async with factory() as session:
            await session.execute(text("SET LOCAL ROLE devnous_user"))
            captures = await read_records(
                session,
                tournament_id=str(project),
                edition_year=2026,
                allowed_domains={"finance"},
            )
            assert {row["fields"]["expense_id"] for row in captures["rows"]} == {
                str(line),
                str(second),
            }
            assert (
                await read_records(
                    session,
                    tournament_id=str(project),
                    edition_year=2026,
                    allowed_domains={"operations"},
                )
            )["rows"] == []
            observed = await read_expense_lines(
                session,
                tournament_id=str(project),
                edition_year=2026,
                document_ids=(str(report),),
                expense_ids=(str(line),),
            )
            assert [row["amount"] for row in observed["rows"]] == ["24.25", "100"]
            assert observed["rows"][0]["expense_date"] == "2027-01-05"
        assert (
            await owner.fetchval("SELECT count(*) FROM direction_dossier_records") == 0
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM direction_dossier_extended_revisions"
            )
            == 2
        )
        with pytest.raises(asyncpg.RaiseError, match="Preserve the extended"):
            await owner.execute(down)
        await owner.execute("ROLLBACK")
        assert await owner.fetchval("SELECT count(*) FROM expense_reports") == 3
    finally:
        await engine.dispose()
        await owner.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await owner.close()
