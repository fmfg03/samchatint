"""Exercise dossier source SQL against a disposable PostgreSQL 16 schema."""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive import dossier_sources as sources


@pytest.mark.asyncio
async def test_canonical_roster_and_finance_are_scoped_by_edition(dossier_postgres):
    sqlalchemy_dsn, postgres_connect = dossier_postgres
    admin = await asyncpg.connect(**postgres_connect)
    schema = f"dossier_sources_test_{uuid4().hex}"
    engine = create_async_engine(
        sqlalchemy_dsn,
        connect_args={**postgres_connect, "server_settings": {"search_path": schema}},
    )
    project, other_project, cutoff_edition, next_edition, team, future_team = (
        uuid4() for _ in range(6)
    )
    employee, paid, report, wrong_year, wrong_project = (uuid4() for _ in range(5))
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}"')
        await admin.execute("""
            CREATE TABLE tournaments (id uuid PRIMARY KEY, name text, active boolean);
            CREATE TABLE copa_telmex_tournament_editions (
                id uuid PRIMARY KEY, tournament_id uuid, edition_year int,
                roster_slug text, active boolean
            );
            CREATE TABLE copa_telmex_report_settings (edition_id uuid, age_cutoff date);
            CREATE TABLE copa_telmex_teams (
                id uuid, tournament_slug text, state text,
                category text, gender text, updated_at timestamp
            );
            CREATE TABLE copa_telmex_players (
                id uuid, team_id uuid, governance_state text, updated_at timestamp,
                birth_date date, curp_valid boolean, curp text
            );
            CREATE TABLE empleados (id uuid, nombre text);
            CREATE TABLE proveedores_clientes (id uuid, nombre text);
            CREATE TABLE payment_run_closure_items (documento_id uuid, closure_id uuid);
            CREATE TABLE payment_run_closures (
                id uuid, closed_at timestamp, run_date date
            );
            CREATE TABLE documentos (
                id uuid, tipo text, estado text, numero_referencia text,
                referencia_operaciones text, fase text, categorias jsonb,
                monto_total numeric, monto_solicitado numeric, concepto_pago text,
                currency text, is_supplier_advance boolean, creado_en timestamp,
                enviado_en timestamp, aprobado_en timestamp, pagado_en timestamp,
                fecha_pago_efectiva date, fecha_pago date, pago_urgente boolean,
                empleado_id uuid, beneficiario_empleado_id uuid,
                beneficiario_proveedor_cliente_id uuid, proveedor_cliente_id uuid,
                torneo_id uuid, edicion int
            );
            CREATE TABLE adjuntos (
                documento_id uuid, categoria text,
                activo boolean, eliminado_en timestamp
            );
            CREATE TABLE expense_reports (
                documento_id uuid, informe_documento_id uuid,
                currency text, fecha timestamp,
                gasto_cantidad double precision, updated_at timestamp, edicion int,
                fase_torneo text, estado_gasto text
            );
        """)
        await admin.execute(
            "INSERT INTO tournaments VALUES ($1,'CTT',true), ($2,'Otro',true)",
            project,
            other_project,
        )
        await admin.execute(
            """
            INSERT INTO copa_telmex_tournament_editions VALUES
                ($1,$3,2026,'copa-2026',true), ($2,$3,2027,'copa-2027',true)
            """,
            cutoff_edition,
            next_edition,
            project,
        )
        await admin.execute(
            "INSERT INTO copa_telmex_report_settings VALUES ($1,'2026-12-31')",
            cutoff_edition,
        )
        await admin.execute(
            """
            INSERT INTO copa_telmex_teams VALUES
                ($1,'copa-2026','Puebla','Juvenil','Varonil','2026-10-01'),
                ($2,'copa-2027','Jalisco','Juvenil','Varonil','2027-10-01')
            """,
            team,
            future_team,
        )
        await admin.execute(
            """
            INSERT INTO copa_telmex_players VALUES
                ($1,$5,'ACTIVE','2026-11-01','2010-10-14',true,'AAAA101014HAAAAA00'),
                ($2,$5,'ACTIVE','2026-05-01',NULL,false,NULL),
                ($3,$5,'PROVISIONAL','2026-10-01','2026-01-01',false,NULL),
                ($4,$6,'ACTIVE','2027-10-01','2010-02-03',false,NULL)
            """,
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            team,
            future_team,
        )
        await admin.execute("INSERT INTO empleados VALUES ($1,'Operador')", employee)
        await admin.execute(
            """
            INSERT INTO documentos
                (id, tipo, estado, numero_referencia, fase, categorias,
                 monto_solicitado, currency, creado_en, fecha_pago_efectiva,
                 empleado_id, torneo_id, edicion)
                VALUES
                ($1,'SOLICITUD','pagado','Pago','Nacional','["Juvenil"]',123.45,'MXN',
                 '2027-01-01','2027-01-12',$5,$6,2026),
                ($2,'INFORME','aprobado','Visita','Estatal','{"legacy":"categoria"}',NULL,'MXN',
                 '2027-01-02',NULL,$5,$6,NULL),
                ($3,'SOLICITUD','aprobado','Otra edición','Estatal','[]',124.45,'MXN',
                 '2027-01-03',NULL,$5,$6,2027),
                ($4,'SOLICITUD','aprobado','Otro torneo',NULL,'[]',125.45,'MXN',
                 '2027-01-04',NULL,$5,$7,NULL)
            """,
            paid,
            report,
            wrong_year,
            wrong_project,
            employee,
            project,
            other_project,
        )
        await admin.execute(
            "INSERT INTO adjuntos VALUES ($1,'comprobante_pago',true,NULL)", paid
        )
        await admin.execute(
            """
            INSERT INTO expense_reports VALUES
                ($1,$1,'MXN','2027-01-02',55.25,'2027-01-03',2026,'Estatal','activo'),
                ($1,NULL,'USD','2026-12-04',10,'2026-12-09',NULL,'estatal','activo'),
                ($1,NULL,'MXN','2027-01-04',100,'2027-01-09',2027,'Estatal','activo'),
                ($1,NULL,'MXN','2027-01-05',200,'2027-01-09',2026,'Nacional','activo'),
                ($1,NULL,'MXN','2027-01-06',300,'2027-01-09',2026,NULL,'activo'),
                ($1,NULL,'MXN','2027-01-07',400,'2027-01-09',2026,'Estatal','cancelado')
            """,
            report,
        )

        async with AsyncSession(engine) as session:
            scope = await sources.probe_registration_scope(
                session, tournament_key=str(project), edition_year=2026
            )
            roster = await sources._registration(session, scope)
            (state,) = roster["rows"]
            assert state["entity"] == "Puebla"
            assert state["registrations"] == 2
            assert state["unique_people_identified"] == 1
            assert state["registrations_without_verified_identity"] == 1
            assert state["ages"] == [
                {"age": 16, "registrations": 1},
                {"age": None, "registrations": 1},
            ]
            scope = await sources.probe_registration_scope(
                session, tournament_key=str(project), edition_year=2027
            )
            future = await sources._registration(session, scope)
            (state,) = future["rows"]
            assert state["entity"] == "Jalisco"
            assert future["age_cutoff"] is None

            result = await sources._finance(
                session,
                str(project),
                2026,
                tuple(map(str, (report, wrong_year, wrong_project))),
            )
            rows = {row["id"]: row for row in result["rows"]}
            assert set(rows) == {str(paid), str(report)}
            assert rows[str(paid)]["status"] == "payment_confirmed"
            assert rows[str(paid)]["effective_payment_date"] == "2027-01-12"
            assert rows[str(paid)]["categories"] == ["Juvenil"]
            visit = rows[str(report)]
            assert visit["edition_year"] is None
            assert visit["amount_paid"] is None
            assert visit["expense_dates"] == ["2026-12-04", "2027-01-02"]
            assert visit["expenses_by_currency"] == [
                {"amount": "55.25", "currency": "MXN", "expenses": 1},
                {"amount": "10", "currency": "USD", "expenses": 1},
            ]
    finally:
        await engine.dispose()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
