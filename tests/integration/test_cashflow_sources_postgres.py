"""Cashflow acceptance using only an explicit isolated PostgreSQL target."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, MetaData, Table, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import registry

from samchat.cashflow.service import load_cashflow_sources


@pytest_asyncio.fixture
async def source_database():
    dsn = os.environ.get("CASHFLOW_TEST_DSN")
    if not dsn:
        pytest.skip("CASHFLOW_TEST_DSN: isolated PostgreSQL not selected")
    parsed = make_url(dsn)
    if (
        parsed.host != "127.0.0.1"
        or parsed.port in (None, 5432)
        or parsed.database != "cashflow001"
    ):
        raise RuntimeError("Require isolated loopback cashflow001 database")
    engine = create_async_engine(dsn)
    schema = "cashflow_test_" + uuid4().hex
    async with engine.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
        await connection.execute(text(f"SET search_path TO {schema}"))
        await connection.execute(text("""
            CREATE TABLE bank_movements (
                id uuid PRIMARY KEY, company_id uuid, currency text,
                bank_account_id uuid, import_run_id uuid, fecha timestamp,
                signo text, importe numeric, cuenta_bancaria text,
                nombre_ordenante text, nombre_beneficiario text,
                concepto_banco text, descripcion text, referencia_bancaria text,
                conciliacion_estado text, related_poliza_id uuid,
                matched_aux_entry_id uuid, matched_expense_id uuid)
        """))
        await connection.execute(text("""
            CREATE TABLE documentos (
                id uuid PRIMARY KEY, company_id uuid, currency text,
                bank_account_id uuid, tipo text, estado text,
                numero_referencia text, referencia_operaciones text,
                monto_total numeric, monto_solicitado numeric,
                concepto_pago text, fecha_pago date, pagado_en timestamp,
                torneo_id uuid, proyecto_otro text, cuenta_gastos_id uuid,
                gasto_generado_id uuid, budget_concept_id uuid,
                cfdi_report_id uuid, empleado_id uuid, proveedor_cliente_id uuid,
                beneficiario_empleado_id uuid,
                beneficiario_proveedor_cliente_id uuid)
        """))
        await connection.execute(
            text("CREATE TABLE pending_caller_work (id integer PRIMARY KEY)")
        )
        await connection.execute(
            text(
                "CREATE TABLE cuentas_de_gastos "
                "(id uuid PRIMARY KEY, company_id uuid, torneo_id uuid)"
            )
        )
        await connection.execute(
            text("CREATE TABLE tournaments (id uuid PRIMARY KEY, name text)")
        )
    try:
        yield engine, schema
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await engine.dispose()


async def load(session, **kwargs):
    return await load_cashflow_sources(
        session,
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        forecast_end=date(2026, 3, 1),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_complete_unassigned_bank_universe_and_unknown_currency(source_database):
    engine, schema = source_database
    async with engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {schema}"))
        await connection.execute(text("""
            INSERT INTO bank_movements (id, fecha, signo, importe, currency)
            SELECT md5('bank-' || value::text)::uuid, '2026-01-05', '+', 0.01, 'MXN'
            FROM generate_series(1,5001) AS value
        """))
        await connection.execute(
            text("""
            INSERT INTO bank_movements (id, fecha, signo, importe)
            VALUES (:id, '2026-01-06', '-', 2)
        """),
            {"id": uuid4()},
        )
        await connection.execute(
            text("""
            INSERT INTO bank_movements (id, fecha, signo, importe, company_id, currency)
            VALUES (:id, '2026-01-07', '+', 999999, :company, 'USD')
        """),
            {"id": uuid4(), "company": uuid4()},
        )
    async with AsyncSession(engine) as session:
        await session.execute(text(f"SET search_path TO {schema}"))
        await session.execute(text("SET TRANSACTION READ ONLY"))
        result = await load(session)
        assert len(result["bank_rows"]) == 5002
        assert result["source_status"]["bank"]["complete"]
        inflows = (row["importe"] for row in result["bank_rows"] if row["signo"] == "+")
        assert sum(inflows) == Decimal("50.01")
        assert len([row for row in result["bank_rows"] if row["currency"] is None]) == 1
        assert all(row["company_id"] is None for row in result["bank_rows"])
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        await session.rollback()


@pytest.mark.asyncio
async def test_missing_source_keeps_caller_pending_unit_of_work(source_database):
    engine, schema = source_database
    mapper = registry(metadata=MetaData())
    pending_table = Table(
        "pending_caller_work",
        mapper.metadata,
        Column("id", Integer, primary_key=True),
        schema=schema,
    )

    class Pending:
        pass

    mapper.map_imperatively(Pending, pending_table)
    async with engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {schema}"))
        await connection.execute(text("DROP TABLE bank_movements"))
        await connection.execute(
            text("""
            INSERT INTO documentos (id,tipo,estado,monto_total,fecha_pago,currency)
            VALUES (:id,'SOLICITUD','en_proceso_pago',123.45,'2026-02-01','MXN')
        """),
            {"id": uuid4()},
        )
    async with AsyncSession(engine) as session:
        await session.execute(text(f"SET search_path TO {schema}"))
        await session.execute(text("SET TRANSACTION READ ONLY"))
        pending = Pending()
        pending.id = 17
        session.add(pending)
        result = await load(session)
        assert pending in session.new
        assert result["source_status"]["bank"]["available"] is False
        assert result["source_status"]["obligations"]["complete"] is True
        assert result["documents"][0]["payable_amount"] == Decimal("123.45")
        with session.no_autoflush:
            count = await session.scalar(
                text("SELECT count(*) FROM pending_caller_work")
            )
            assert count == 0
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        await session.rollback()
    mapper.dispose()


@pytest.mark.asyncio
async def test_inherited_tournament_keeps_obligation_queues(source_database):
    engine, schema = source_database
    tid, account = uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {schema}"))
        await connection.execute(
            text("INSERT INTO tournaments VALUES (:id,:name)"),
            {"id": tid, "name": "Exact tournament"},
        )
        await connection.execute(
            text("INSERT INTO cuentas_de_gastos VALUES (:id,NULL,:tid)"),
            {"id": account, "tid": tid},
        )
        requests = [
            ("aprobado", date(2025, 12, 15)),
            ("en_proceso_pago", None),
            ("enviado", date(2026, 2, 10)),
        ]
        for state, payment_date in requests:
            await connection.execute(
                text("""
                INSERT INTO documentos
                    (id,tipo,estado,monto_total,fecha_pago,currency,cuenta_gastos_id)
                VALUES (:id,'SOLICITUD',:state,11,:day,'MXN',:account)
            """),
                {
                    "id": uuid4(),
                    "state": state,
                    "day": payment_date,
                    "account": account,
                },
            )
        await connection.execute(
            text("""
            INSERT INTO documentos (id,tipo,estado,monto_total,currency,torneo_id)
            VALUES (:id,'SOLICITUD','aprobado',999,'MXN',:foreign)
        """),
            {"id": uuid4(), "foreign": uuid4()},
        )
    async with AsyncSession(engine) as session:
        await session.execute(text(f"SET search_path TO {schema}"))
        await session.execute(text("SET TRANSACTION READ ONLY"))
        result = await load(session, tournament_ids=[str(tid)])
        assert not result["bank_rows"]
        assert not result["source_status"]["bank"]["available"]
        assert len(result["documents"]) == 3
        assert {row["estado"] for row in result["documents"]} == {
            "aprobado",
            "en_proceso_pago",
            "enviado",
        }
        assert all(row["project_id"] == str(tid) for row in result["documents"])
        assert all(
            row["project_name"] == "Exact tournament" for row in result["documents"]
        )
        undated = [row for row in result["documents"] if row["fecha_pago"] is None]
        assert len(undated) == 1
        await session.rollback()


@pytest.mark.asyncio
async def test_company_consumer_uses_real_upstream_guards(source_database):
    """The overlay is test-only; no candidate owners are copied into serving."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    engine, schema = source_database
    root = Path(__file__).resolve().parents[2]
    upstream = root.parent / "multiempresa-002-20261027" / "src"
    if not (upstream / "devnous/gastos/services/company_scope_service.py").exists():
        pytest.skip("Explicit multiempresa upstream candidate not present")
    actor, company, foreign, account, gl = (uuid4() for _ in range(5))
    async with engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {schema}"))
        for statement in (
            "CREATE TABLE empleados (id uuid PRIMARY KEY, activo boolean)",
            "CREATE TABLE companies (id uuid PRIMARY KEY, active boolean)",
            "CREATE TABLE company_memberships "
            "(id uuid PRIMARY KEY, company_id uuid, empleado_id uuid, "
            "active boolean)",
            "CREATE TABLE company_bank_accounts "
            "(id uuid PRIMARY KEY, company_id uuid, currency text, "
            "accounting_account_id uuid)",
            "CREATE TABLE cuentas_contables (id uuid PRIMARY KEY, company_id uuid)",
        ):
            await connection.execute(text(statement))
        await connection.execute(
            text("INSERT INTO empleados VALUES (:id,true)"), {"id": actor}
        )
        await connection.execute(
            text("INSERT INTO companies VALUES (:id,true)"), {"id": company}
        )
        await connection.execute(
            text(
                "INSERT INTO company_memberships " "VALUES (:id,:company,:actor,true)"
            ),
            {"id": uuid4(), "company": company, "actor": actor},
        )
        await connection.execute(
            text("INSERT INTO cuentas_contables VALUES (:id,:company)"),
            {"id": gl, "company": company},
        )
        await connection.execute(
            text(
                "INSERT INTO company_bank_accounts " "VALUES (:id,:company,'MXN',:gl)"
            ),
            {"id": account, "company": company, "gl": gl},
        )
        await connection.execute(
            text("""
            INSERT INTO bank_movements
                (id, company_id, currency, bank_account_id, fecha, signo, importe)
            VALUES (:id,:company,'MXN',:account,'2026-01-05','+',12.34)
        """),
            {"id": uuid4(), "company": company, "account": account},
        )
        await connection.execute(
            text("""
            INSERT INTO bank_movements (id, company_id, currency, fecha, signo, importe)
            VALUES (:id,:company,'USD','2026-01-05','+',99999)
        """),
            {"id": uuid4(), "company": foreign},
        )
    script = r"""
import asyncio, hashlib, json, os, sys
from datetime import date
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import devnous.gastos
import samchat
import samchat.finance_platform.service as finance
import samchat.ar.service as ar
import samchat.budgets.service as budgets
from devnous.gastos.services.company_scope_service import (
    CompanyContext, CompanySummary, CompanyScopeError
)
samchat.__path__.insert(0, os.environ['CASHFLOW_LOCAL_SRC'] + '/samchat')
import samchat.cashflow.service as cashflow
assert '/cashflow-001-20261028/' in cashflow.__file__
for owner in (finance, ar, budgets):
    assert '/multiempresa-002-20261027/' in owner.__file__, owner.__file__
async def main():
    engine=create_async_engine(
        os.environ['CASHFLOW_TEST_DSN'],
        connect_args={'server_settings': {
            'search_path':os.environ['CASHFLOW_TEST_SCHEMA']
        }}
    )
    cid=UUID(os.environ['CASHFLOW_TEST_COMPANY'])
    actor=UUID(os.environ['CASHFLOW_TEST_ACTOR'])
    summary=CompanySummary(cid,'sports','Synthetic company','Synthetic company')
    context=CompanyContext(actor,cid,frozenset([cid]),frozenset(),(summary,),frozenset())
    kwargs=dict(start=date(2026,1,1),end=date(2026,2,1),
                forecast_end=date(2026,3,1),company_context=context)
    async with AsyncSession(engine) as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        loaded=await cashflow.load_cashflow_sources(session,**kwargs)
        assert len(loaded['bank_rows'])==1, loaded
        assert str(loaded['bank_rows'][0]['importe'])=='12.34'
        assert loaded['company_id']==str(cid)
        assert await session.scalar(text('SHOW transaction_read_only'))=='on'
        await session.rollback()
    async with engine.begin() as conn:
        await conn.execute(text('UPDATE company_memberships SET active=false'))
    async with AsyncSession(engine) as session:
        try: await cashflow.load_cashflow_sources(session,**kwargs)
        except CompanyScopeError as exc: assert 'company_selection_denied' in str(exc)
        else: raise AssertionError('Revoked membership was accepted')
        await session.rollback()
    async with engine.begin() as conn:
        await conn.execute(text('UPDATE company_memberships SET active=true'))
        await conn.execute(
            text('UPDATE company_bank_accounts SET company_id=:foreign'),
            {'foreign':os.environ['CASHFLOW_TEST_FOREIGN']}
        )
    async with AsyncSession(engine) as session:
        try: await cashflow.load_cashflow_sources(session,**kwargs)
        except CompanyScopeError as exc:
            assert 'finance_source_company_mismatch' in str(exc)
        else: raise AssertionError('Foreign bank parent was accepted')
        await session.rollback()
    await engine.dispose()
    hashes={owner.__file__:hashlib.sha256(
        open(owner.__file__,'rb').read()).hexdigest()
        for owner in (finance,ar,budgets)}
    print(json.dumps({
        'local_cashflow':cashflow.__file__, 'upstream_hashes':hashes,
        'membership_revocation':'denied', 'foreign_parent':'denied',
        'owned_rows':1
    }))
asyncio.run(main())
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(upstream),
        CASHFLOW_LOCAL_SRC=str(root / "src"),
        CASHFLOW_TEST_SCHEMA=schema,
        CASHFLOW_TEST_COMPANY=str(company),
        CASHFLOW_TEST_ACTOR=str(actor),
        CASHFLOW_TEST_FOREIGN=str(foreign),
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(completed.stdout.strip().splitlines()[-1])
    assert receipt["membership_revocation"] == "denied"
    assert receipt["foreign_parent"] == "denied"
    assert receipt["owned_rows"] == 1
