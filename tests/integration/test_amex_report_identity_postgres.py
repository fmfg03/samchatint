"""Canonical fiscal/budget readers against an isolated PostgreSQL schema."""

import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos.models import ExpenseReport
from devnous.gastos.services.amex_accounting_identity_service import (
    accounting_expense_condition,
    accounting_expense_sql,
)
from devnous.gastos.services.documento_service import (
    SolicitudValidationError,
    validate_shared_cfdi_payment_amount,
)
from devnous.gastos.services.expense_coi_export_service import (
    assess_expense_coi_cleanup_ready,
    build_expense_cfdi_for_export,
)
from samchat.budgets.service import _build_budget_scope_filters


@pytest_asyncio.fixture
async def identity_db():
    raw = os.environ.get("TEST_AMEX_DATABASE_URL")
    if not raw:
        pytest.skip("Requires isolated AMEX acceptance PostgreSQL")
    url = make_url(raw)
    assert url.host in {"localhost", "127.0.0.1"} and url.port == 55471
    schema = "amex_identity_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    # Reader fixture deliberately owns only columns read by the existing queries.
    # Migration/constraints are verified separately in the recognition suite.
    statements = [
        "CREATE TABLE documentos (id uuid PRIMARY KEY, cfdi_report_id uuid, estado text, monto_solicitado numeric)",
        "CREATE TABLE expense_reports (id uuid PRIMARY KEY, cfdi_report_id uuid, estado_gasto text, gasto_cantidad numeric, propina_no_deducible numeric, documento_id uuid, solicitud_documento_id uuid, informe_documento_id uuid, fecha timestamp)",
        "CREATE TABLE amex_recognition_consumptions (id uuid PRIMARY KEY, imported_expense_id uuid, source_kind text, binding_mode text)",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def seed(session, invoice, count=2):
    ids = [uuid4() for _ in range(count)]
    for expense in ids:
        await session.execute(
            text(
                "INSERT INTO expense_reports (id,cfdi_report_id,estado_gasto,gasto_cantidad,fecha) VALUES (:id,:invoice,'activo',116,'2026-10-03')"
            ),
            {"id": expense, "invoice": invoice},
        )
    return ids


async def mark(session, expense, mode):
    await session.execute(
        text(
            "INSERT INTO amex_recognition_consumptions VALUES (:id,:expense,'statement',:mode)"
        ),
        {"id": uuid4(), "expense": expense, "mode": mode},
    )


@pytest.mark.asyncio
async def test_explicit_bridge_reuses_cfdi_reservation(identity_db):
    invoice = SimpleNamespace(id=uuid4(), cfdi_uuid=None, total=348)
    async with identity_db.begin() as session:
        imported, report = await seed(session, invoice.id)
        with pytest.raises(SolicitudValidationError, match="excede"):
            await validate_shared_cfdi_payment_amount(
                session, cfdi_report=invoice, requested_amount=232
            )
        await mark(session, imported, "report_bridge")
        assert await validate_shared_cfdi_payment_amount(
            session, cfdi_report=invoice, requested_amount=232
        ) == Decimal("232.00")
        ready, issues = await assess_expense_coi_cleanup_ready(
            session, SimpleNamespace(id=imported)
        )
        assert not ready and "no genera otra póliza" in issues[0]
        with pytest.raises(ValueError, match="no genera otra póliza"):
            await build_expense_cfdi_for_export(
                session, SimpleNamespace(id=imported), require_cleanup_ready=False
            )
        # The real report reservation remains, so one extra cent still fails.
        with pytest.raises(SolicitudValidationError, match="excede"):
            await validate_shared_cfdi_payment_amount(
                session, cfdi_report=invoice, requested_amount="232.01"
            )


@pytest.mark.asyncio
async def test_sql_and_orm_budget_identity_agree_without_heuristics(identity_db):
    async with identity_db.begin() as session:
        imported, report, unrelated = await seed(session, uuid4(), count=3)
        await mark(session, unrelated, "legacy")
        before = set(
            (
                await session.scalars(
                    select(ExpenseReport.id).where(
                        accounting_expense_condition(ExpenseReport)
                    )
                )
            ).all()
        )
        assert before == {imported, report, unrelated}
        await mark(session, imported, "report_bridge")
        after = set(
            (
                await session.scalars(
                    select(ExpenseReport.id).where(
                        accounting_expense_condition(ExpenseReport)
                    )
                )
            ).all()
        )
        sql_ids = set(
            (
                await session.scalars(
                    text(
                        "SELECT e.id FROM expense_reports e WHERE "
                        + accounting_expense_sql("e")
                    )
                )
            ).all()
        )
        assert after == sql_ids == {report, unrelated}
        _, filters, params = _build_budget_scope_filters(
            edition_year=2026,
            tournament_id=None,
            tournament_name=None,
            tournament_code=None,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
        )
        amount = await session.scalar(
            text(
                "SELECT SUM(e.gasto_cantidad) FROM expense_reports e WHERE "
                + " AND ".join(filters)
            ),
            params,
        )
        assert amount == Decimal("232")


def test_sql_alias_is_not_user_interpolation():
    with pytest.raises(ValueError):
        accounting_expense_sql("e; DELETE FROM expense_reports")


@pytest.mark.asyncio
async def test_cfdi_reuse_ignores_only_the_explicit_header_not_other_expenses(
    identity_db,
):
    from devnous.gastos.services.cfdi_ingestion_service import _other_expense_links

    invoice = uuid4()
    async with identity_db.begin() as session:
        imported, report = await seed(session, invoice)
        entity = ExpenseReport(id=report)
        assert await _other_expense_links(session, invoice, entity) == 1
        await mark(session, imported, "report_bridge")
        assert await _other_expense_links(session, invoice, entity) == 0
        # Same UUID/amount alone never supplies an economic identity.
        await seed(session, invoice, count=1)
        assert await _other_expense_links(session, invoice, entity) == 1
