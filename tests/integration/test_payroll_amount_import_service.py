"""Acceptance tests against an explicitly selected, isolated PostgreSQL instance."""

import asyncio
import copy
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos.models import (
    Base,
    CuentaContable,
    Empleado,
    PayrollAmountImport,
    PayrollAmountImportLine,
    PayrollEmployee,
    PayrollEmployer,
    PayrollEmployerRegistration,
    PayrollPeriod,
)
from devnous.gastos.services.payroll_amount_import_service import (
    build_payroll_amount_preview,
    confirm_payroll_amount_import,
)
from devnous.gastos.services.payroll_amount_workbook_parser import ParsedPayrollAmounts

pytestmark = pytest.mark.integration


@pytest.fixture
async def database():
    url = os.getenv("PAYROLL_AMOUNT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PAYROLL_AMOUNT_TEST_DATABASE_URL must select an isolated Postgres")
    schema = "payroll_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Empleado.__table__,
                    CuentaContable.__table__,
                    PayrollEmployer.__table__,
                    PayrollEmployerRegistration.__table__,
                    PayrollPeriod.__table__,
                    PayrollEmployee.__table__,
                ],
            )
        )
    migration = Path(
        "database/migrations/20261028_payroll_amount_imports.sql"
    ).read_text()
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        # Exercise the actual owner-run SQL twice, rather than only ORM table creation.
        await raw.driver_connection.execute(migration)
        await raw.driver_connection.execute(migration)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor, employer, registration, period, payroll_employee = [
        uuid4() for _ in range(5)
    ]
    async with factory() as session:
        await session.execute(
            Empleado.__table__.insert().values(
                id=actor, nombre="Persona prueba", rol="finanzas", activo=True
            )
        )
        await session.execute(
            PayrollEmployer.__table__.insert().values(
                id=employer, employer_key="test", legal_name="Prueba", active=True
            )
        )
        await session.execute(
            PayrollEmployerRegistration.__table__.insert().values(
                id=registration,
                payroll_employer_id=employer,
                registration_code="TEST",
                active=True,
            )
        )
        await session.execute(
            PayrollPeriod.__table__.insert().values(
                id=period,
                period_type="biweekly",
                fiscal_year=2026,
                period_no=19,
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 15),
                status="open",
            )
        )
        await session.execute(
            PayrollEmployee.__table__.insert().values(
                id=payroll_employee,
                empleado_id=actor,
                employee_number="001",
                employer_registration_id=registration,
                active=True,
            )
        )
        for code in ["5200-001-002", "1170-001-008", "1120-001-001"]:
            await session.execute(
                CuentaContable.__table__.insert().values(
                    id=uuid4(),
                    codigo=code,
                    nombre="Cuenta prueba",
                    tipo="gasto",
                    activo=True,
                )
            )
        await session.commit()
    try:
        yield factory, {
            "actor_id": actor,
            "employer_id": employer,
            "period_id": period,
        }, engine
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def source(*, amount="100.00", file_hash="a" * 64, bonus=False):
    gross = Decimal(amount) + (Decimal("5") if bonus else Decimal("0"))
    net = str(gross - Decimal("10"))
    components = [
        {
            "concept_key": "salary",
            "label": "SUELDO",
            "side": "perception",
            "amount": amount,
            "account_code": "5200-001-002",
            "has_formula": False,
        },
        {
            "concept_key": "personal_loan",
            "label": "PRESTAMO PERSONAL",
            "side": "deduction",
            "amount": "10.00",
            "account_code": "1170-001-008",
            "has_formula": False,
        },
        {
            "concept_key": "net_pay",
            "label": "NETO A PAGAR",
            "side": "net",
            "amount": net,
            "account_code": "1120-001-001",
            "has_formula": False,
        },
    ]
    if bonus:
        components.append(
            {
                "concept_key": "bonus",
                "label": "BONO",
                "side": "perception",
                "amount": "5.00",
                "account_code": None,
                "has_formula": False,
            }
        )
    return ParsedPayrollAmounts(
        file_hash,
        rows=[
            {
                "source_row": 7,
                "employee_code": "001",
                "source_name": "Persona prueba",
                "days_paid": "15.0000",
                "daily_salary": None,
                "gross_total": str(gross),
                "deductions_total": "10.00",
                "net_total": net,
                "components": components,
            }
        ],
    )


async def preview(factory, ids, parsed):
    async with factory() as session:
        return await build_payroll_amount_preview(
            session, parsed, employer_id=ids["employer_id"], period_id=ids["period_id"]
        )


async def confirm(factory, ids, parsed, plan):
    async with factory() as session:
        return await confirm_payroll_amount_import(
            session, parsed, **ids, expected_plan_sha256=plan["plan_sha256"]
        )


async def test_preview_no_writes_confirm_exact_accounts_and_retry(database):
    factory, ids, _ = database
    parsed = source()
    plan = await preview(factory, ids, parsed)
    assert not plan["errors"]
    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PayrollAmountImport))
            == 0
        )
    batch_id = await confirm(factory, ids, parsed, plan)
    assert await confirm(factory, ids, parsed, plan) == batch_id
    async with factory() as session:
        batch = await session.get(PayrollAmountImport, batch_id)
        assert batch.created_by_empleado_id == ids["actor_id"]
        assert batch.status == "validated"
        lines = (await session.execute(select(PayrollAmountImportLine))).scalars().all()
        assert len(lines) == 1
        assert [c["account_code"] for c in lines[0].source_json["components"]] == [
            "5200-001-002",
            "1170-001-008",
            "1120-001-001",
        ]
        assert all(c["account_id"] for c in lines[0].source_json["components"])
        assert lines[0].daily_salary is None
        tables = (
            (
                await session.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = current_schema()"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "payroll_runs" not in tables and "accounting_polizas" not in tables


async def test_pending_mapping_preserves_import_without_posting(database):
    factory, ids, _ = database
    parsed = source(bonus=True)
    plan = await preview(factory, ids, parsed)
    assert not plan["errors"]
    assert plan["pending_count"] == 1
    batch_id = await confirm(factory, ids, parsed, plan)
    async with factory() as session:
        assert (
            await session.get(PayrollAmountImport, batch_id)
        ).status == "pending_mapping"


async def test_accounts_remain_distinct_for_each_employee(database):
    factory, ids, _ = database
    second_person, second_payroll = uuid4(), uuid4()
    async with factory() as session:
        registration_id = await session.scalar(select(PayrollEmployerRegistration.id))
        await session.execute(
            Empleado.__table__.insert().values(
                id=second_person, nombre="Otra persona", activo=True
            )
        )
        await session.execute(
            PayrollEmployee.__table__.insert().values(
                id=second_payroll,
                empleado_id=second_person,
                employee_number="002",
                employer_registration_id=registration_id,
                active=True,
            )
        )
        for code in ["5300-001-002", "1170-001-016"]:
            await session.execute(
                CuentaContable.__table__.insert().values(
                    id=uuid4(),
                    codigo=code,
                    nombre="Otra cuenta",
                    tipo="gasto",
                    activo=True,
                )
            )
        await session.commit()
    parsed = source()
    second = copy.deepcopy(parsed.rows[0])
    second.update(source_row=8, employee_code="002", source_name="Otra persona")
    second["components"][0]["account_code"] = "5300-001-002"
    second["components"][1]["account_code"] = "1170-001-016"
    parsed.rows.append(second)
    batch_id = await confirm(factory, ids, parsed, await preview(factory, ids, parsed))
    async with factory() as session:
        lines = (
            (
                await session.execute(
                    select(PayrollAmountImportLine).order_by(
                        PayrollAmountImportLine.source_row
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [
            line.source_json["components"][0]["account_code"] for line in lines
        ] == ["5200-001-002", "5300-001-002"]
        assert [
            line.source_json["components"][1]["account_code"] for line in lines
        ] == ["1170-001-008", "1170-001-016"]
        assert (
            await session.get(PayrollAmountImport, batch_id)
        ).gross_total == Decimal("200.00")


@pytest.mark.parametrize(
    "change",
    [
        "unknown_employee",
        "other_employer",
        "closed",
        "inactive_account",
        "inactive_employee",
        "missing_account",
        "missing_period",
        "inactive_employer",
        "source_error",
    ],
)
async def test_invalid_resolution_blocks_entire_import(database, change):
    factory, ids, _ = database
    parsed = source()
    async with factory() as session:
        if change == "unknown_employee":
            parsed.rows[0]["employee_code"] = "missing"
        elif change == "other_employer":
            ids["employer_id"] = uuid4()
        elif change == "closed":
            await session.execute(
                PayrollPeriod.__table__.update().values(status="closed")
            )
        elif change == "inactive_account":
            await session.execute(
                CuentaContable.__table__.update().values(activo=False)
            )
        elif change == "inactive_employee":
            await session.execute(Empleado.__table__.update().values(activo=False))
        elif change == "missing_account":
            parsed.rows[0]["components"][0]["account_code"] = None
        elif change == "missing_period":
            ids["period_id"] = uuid4()
        elif change == "inactive_employer":
            await session.execute(
                PayrollEmployer.__table__.update().values(active=False)
            )
        elif change == "source_error":
            parsed.errors.append("Fila 7: diferencia en percepciones.")
        await session.commit()
    plan = await preview(factory, ids, parsed)
    assert plan["errors"]
    with pytest.raises(ValueError, match="errores"):
        await confirm(factory, ids, parsed, plan)
    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PayrollAmountImport))
            == 0
        )


async def test_changed_resolution_and_source_require_new_preview(database):
    factory, ids, _ = database
    parsed = source()
    plan = await preview(factory, ids, parsed)
    changed = source(amount="200.00", file_hash="b" * 64)
    with pytest.raises(ValueError, match="cambiaron"):
        await confirm(factory, ids, changed, plan)
    async with factory() as session:
        await session.execute(
            CuentaContable.__table__.update()
            .where(CuentaContable.codigo == "5200-001-002")
            .values(activo=False)
        )
        await session.commit()
    with pytest.raises(ValueError, match="errores"):
        await confirm(factory, ids, parsed, plan)


async def test_conflicting_replacement_is_rejected(database):
    factory, ids, _ = database
    parsed = source()
    await confirm(factory, ids, parsed, await preview(factory, ids, parsed))
    changed = source(amount="200.00", file_hash="b" * 64)
    with pytest.raises(ValueError, match="Ya existe"):
        await confirm(factory, ids, changed, await preview(factory, ids, changed))


async def test_concurrent_retries_only_create_one_batch(database):
    factory, ids, _ = database
    parsed = source()
    plan = await preview(factory, ids, parsed)
    receipts = await asyncio.gather(
        confirm(factory, ids, parsed, plan), confirm(factory, ids, parsed, plan)
    )
    assert receipts[0] == receipts[1]
    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PayrollAmountImport))
            == 1
        )


async def test_concurrent_conflicts_are_serialized(database):
    factory, ids, _ = database
    first, second = source(), source(amount="200.00", file_hash="b" * 64)
    plans = await asyncio.gather(
        preview(factory, ids, first), preview(factory, ids, second)
    )
    results = await asyncio.gather(
        confirm(factory, ids, first, plans[0]),
        confirm(factory, ids, second, plans[1]),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1


async def test_line_failure_rolls_back_the_batch(database):
    factory, ids, engine = database
    parsed = source()
    plan = await preview(factory, ids, parsed)

    def fail_lines(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO payroll_amount_import_lines"):
            raise RuntimeError("injected line failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_lines)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await confirm(factory, ids, parsed, plan)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_lines)
    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PayrollAmountImport))
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(PayrollAmountImportLine)
            )
            == 0
        )
