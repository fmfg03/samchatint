"""Real PostgreSQL transaction/race checks, never a production connection.

The isolated schema retains new recognition foreign keys, unique and check
constraints. Legacy foreign keys are omitted to avoid unrelated domain seeds.
Fiscal preview account resolution is mocked; journal/recognition writes and
transaction locks execute against PostgreSQL.
"""

import asyncio
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import (
    ForeignKeyConstraint,
    MetaData,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.services import amex_accounting_cut_service as cuts
from devnous.gastos.services import amex_accounting_posting_service as service
from devnous.gastos.services import amex_recognition_service as recognition


@pytest_asyncio.fixture
async def pg(monkeypatch):
    raw = os.environ.get("TEST_AMEX_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_AMEX_DATABASE_URL to an isolated local PostgreSQL")
    url = make_url(raw)
    if url.host not in {"127.0.0.1", "localhost"} or url.port != 55471:
        pytest.fail("AMEX acceptance database must be isolated localhost:55471")
    schema = "amex_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    copied = MetaData()
    for table in models.Base.metadata.tables.values():
        clone = table.to_metadata(copied)
        if not table.name.startswith("amex_"):
            for constraint in list(clone.constraints):
                if isinstance(constraint, ForeignKeyConstraint):
                    clone.constraints.remove(constraint)
                    for element in constraint.elements:
                        clone.foreign_keys.discard(element)
                        element.parent.foreign_keys.discard(element)
    async with engine.begin() as connection:
        await connection.run_sync(copied.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = uuid4()
    async with factory.begin() as session:
        session.add(models.Empleado(id=actor, nombre="Acceptance", rol="finanzas"))
        session.add(
            models.AmexRecognitionActivation(
                id=1, activated_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
            )
        )
    monkeypatch.setattr(
        service,
        "build_expense_accounting_preview",
        AsyncMock(
            return_value={"taxes": {"base_gasto": 116, "neto_contrapartida": 116}}
        ),
    )
    try:
        yield SimpleNamespace(factory=factory, actor=actor)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def _seed(pg, *, count=1, code="2120-002-062", invoice=True):
    account = models.CuentaContable(
        id=uuid4(), codigo="5300-010-001", nombre="Gasto", tipo="gasto"
    )
    liability = models.CuentaContable(
        id=uuid4(), codigo=code, nombre="AMEX", tipo="pasivo"
    )
    bank = models.CuentaContable(
        id=uuid4(), codigo="1120-001-001", nombre="Santander", tipo="banco"
    )
    partner = models.CuentaContable(
        id=uuid4(), codigo="1170-002-004", nombre="Socio", tipo="deudor"
    )
    card = models.AmexCardAccount(
        id=uuid4(),
        card_label="Acceptance",
        cardholder_key="empresa",
        last4="5007",
        liability_cuenta_contable_id=liability.id,
        active=True,
    )
    account_id = uuid4()
    cg = models.CuentaDeGastos(
        id=account_id,
        empleado_id=pg.actor,
        referencia_base="ACCEPTANCE",
        estado="cerrada",
    )
    informe = models.Documento(
        id=uuid4(),
        tipo="INFORME",
        estado="aprobado",
        cuenta_gastos_id=account_id,
        empleado_id=pg.actor,
        numero_referencia="I-TEST",
        referencia_operaciones="OP-TEST",
        proyecto_otro="Proyecto AMEX congelado",
        aprobado_en=datetime(2026, 10, 3, tzinfo=timezone.utc),
    )
    imports, reports, invoices = [], [], []
    for _ in range(count):
        cfdi = (
            models.CFDIReport(
                id=uuid4(), total=116, subtotal=100, moneda="MXN", xml_parsed=True
            )
            if invoice
            else None
        )
        common = dict(
            proyecto="Acceptance",
            concepto="Gasto",
            gasto_cantidad=116,
            currency="MXN",
            fecha=datetime(2026, 10, 2),
            created_at=datetime(2026, 10, 2),
            estado_gasto="activo",
            pagado_con_amex_empresa=True,
            ultimos_4_digitos=card.last4,
            cuenta_contable_id=account.id,
            cfdi_report_id=cfdi.id if cfdi else None,
        )
        imports.append(models.ExpenseReport(id=uuid4(), origen="amex_batch", **common))
        reports.append(
            models.ExpenseReport(
                id=uuid4(),
                origen="manual",
                cuenta_gastos_id=account_id,
                informe_documento_id=informe.id,
                **common,
            )
        )
        if cfdi:
            invoices.append(cfdi)
    async with pg.factory.begin() as session:
        session.add_all(
            [
                account,
                liability,
                bank,
                partner,
                card,
                cg,
                informe,
                *invoices,
                *imports,
                *reports,
            ]
        )
    return SimpleNamespace(
        card=card,
        imports=imports,
        reports=reports,
        liability=liability,
        partner=partner,
        account=account,
        informe=informe,
    )


async def _bind(pg, data):
    async with pg.factory.begin() as session:
        for imported, report in zip(data.imports, data.reports):
            result = await recognition.bind_amex_consumption(
                session,
                imported_expense_id=imported.id,
                report_expense_ids=[report.id],
                actor_id=pg.actor,
            )
            assert result.status in {
                "created",
                "exists",
                "linked",
                "bound",
            }, result.reason


async def _review(pg, data, treatments=None):
    versions = {}
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        for expense, treatment in zip(
            data.reports, treatments or ["expense"] * len(data.reports)
        ):
            result = await cuts.review_amex_partida(
                session,
                informe_id=data.informe.id,
                expense_id=expense.id,
                treatment=treatment,
                debtor_account_id=(
                    data.partner.id if treatment == "partner_receivable" else None
                ),
                actor=actor,
                reason="Decision de Finanzas",
            )
            assert result.status == "reviewed", result.reason
            versions[str(expense.id)] = result.review.version
    return versions


async def _cut(
    pg,
    data,
    versions,
    *,
    adjustment=False,
    actor_id=None,
    accounting_date=date(2026, 10, 2),
):
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, actor_id or pg.actor)
        return await cuts.create_amex_accounting_cut(
            session,
            informe_id=data.informe.id,
            actor=actor,
            expected_versions=versions,
            accounting_date=accounting_date,
            adjustment=adjustment,
            reason="Corte autorizado",
        )


async def _lines(pg, poliza_id=None):
    async with pg.factory() as session:
        query = select(models.AccountingPolizaLine).order_by(
            models.AccountingPolizaLine.line_no
        )
        if poliza_id:
            query = query.where(models.AccountingPolizaLine.poliza_id == poliza_id)
        return list((await session.scalars(query)).all())


async def _journal_count(pg):
    async with pg.factory() as session:
        return await session.scalar(
            select(func.count()).select_from(models.AccountingPoliza)
        )


@pytest.mark.asyncio
async def test_multiple_partidas_mixed_treatments_create_one_whole_report_cut(pg):
    data = await _seed(pg, count=3)
    await _bind(pg, data)
    versions = await _review(pg, data, ["expense", "partner_receivable", "expense"])
    result = await _cut(pg, data, versions)
    assert result.status == "created", result.reason
    assert result.cut.snapshot_json["coi_metadata"] == {
        "document_id": str(data.informe.id),
        "operation_reference": "OP-TEST",
        "party_name": "Acceptance",
        "context_description": "Proyecto AMEX congelado",
    }
    assert await _journal_count(pg) == 1
    rows = await _lines(pg, result.poliza.id)
    assert sum(Decimal(str(row.debe or 0)) for row in rows) == Decimal("348")
    assert sum(Decimal(str(row.haber or 0)) for row in rows) == Decimal("348")
    assert (
        sum(row.debe or 0 for row in rows if row.cuenta_codigo == data.partner.codigo)
        == 116
    )
    assert (
        sum(row.debe or 0 for row in rows if row.cuenta_codigo == data.account.codigo)
        == 232
    )
    assert {row.cuenta_codigo for row in rows if row.haber} == {data.liability.codigo}


@pytest.mark.asyncio
@pytest.mark.parametrize("invoice", [True, False])
async def test_partner_whole_amount_ignores_auto_classification_and_invoice(
    pg, invoice, monkeypatch
):
    data = await _seed(pg, invoice=invoice)
    await _bind(pg, data)
    versions = await _review(pg, data, ["partner_receivable"])
    preview = AsyncMock(
        side_effect=AssertionError("Partner receivable must not resolve fiscal charges")
    )
    monkeypatch.setattr(service, "build_expense_accounting_preview", preview)
    result = await _cut(pg, data, versions)
    assert result.status == "created", result.reason
    rows = await _lines(pg, result.poliza.id)
    assert [(row.cuenta_codigo, row.debe or 0, row.haber or 0) for row in rows] == [
        (data.partner.codigo, 116, 0),
        (data.liability.codigo, 0, 116),
    ]
    preview.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["report", "reconciliation"])
async def test_operational_approval_and_conciliation_prepare_without_posting_then_cut(
    pg, first
):
    data = await _seed(pg)
    await _bind(pg, data)
    for lane in [first, "reconciliation" if first == "report" else "report"]:
        async with pg.factory.begin() as session:
            if lane == "report":
                result = await service.ensure_amex_report_approval_posting(
                    session, informe_documento=data.informe
                )
            else:
                card = await session.get(models.AmexCardAccount, data.card.id)
                result = await service.ensure_amex_reconciliation_posting(
                    session, year=2026, month=10, card_account=card
                )
            assert result.status in {"prepared", "skipped", "exists"}, result.reason
        assert await _journal_count(pg) == 0
    versions = await _review(pg, data)
    result = await _cut(pg, data, versions)
    assert result.status == "created", result.reason
    retry = await _cut(pg, data, versions)
    assert retry.status == "exists"
    assert result.poliza.id == retry.poliza.id
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_two_concurrent_initial_cuts_and_retry_return_same_poliza(pg):
    data = await _seed(pg, count=2)
    await _bind(pg, data)
    versions = await _review(pg, data)
    results = await asyncio.gather(_cut(pg, data, versions), _cut(pg, data, versions))
    assert sorted(result.status for result in results) == ["created", "exists"]
    assert results[0].poliza.id == results[1].poliza.id
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_cut_rollback_removes_all_headers_lines_and_receipt_then_retry(pg):
    data = await _seed(pg, count=2)
    await _bind(pg, data)
    versions = await _review(pg, data)
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        result = await cuts.create_amex_accounting_cut(
            session,
            informe_id=data.informe.id,
            actor=actor,
            expected_versions=versions,
            accounting_date=date(2026, 10, 2),
        )
        assert result.status == "created", result.reason
        await session.rollback()
    assert await _journal_count(pg) == 0
    assert not await _lines(pg)
    retry = await _cut(pg, data, versions)
    assert retry.status == "created", retry.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_review", [True, False])
async def test_complete_review_and_exact_versions_are_required(pg, missing_review):
    data = await _seed(pg, count=2)
    await _bind(pg, data)
    versions = await _review(pg, data)
    if missing_review:
        versions.pop(str(data.reports[0].id))
    else:
        versions[str(data.reports[0].id)] -= 1
    result = await _cut(pg, data, versions)
    assert result.status == "pending"
    assert result.reason
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(service.ALLOWED_AMEX_LIABILITY_CODES))
async def test_each_allowed_card_liability_is_frozen_in_cut(pg, code):
    data = await _seed(pg, code=code)
    await _bind(pg, data)
    versions = await _review(pg, data)
    result = await _cut(pg, data, versions)
    assert result.status == "created", result.reason
    rows = await _lines(pg, result.poliza.id)
    assert [row.cuenta_codigo for row in rows if row.haber] == [code]


@pytest.mark.asyncio
async def test_closed_economic_period_blocks_entire_cut(pg):
    data = await _seed(pg, count=2)
    await _bind(pg, data)
    versions = await _review(pg, data)
    async with pg.factory.begin() as session:
        session.add(
            models.AccountingClosePeriod(
                id=uuid4(), fiscal_year=2026, fiscal_month=10, status="closed"
            )
        )
    result = await _cut(pg, data, versions)
    assert result.status == "pending" and "closed" in result.reason
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
async def test_non_finance_actor_cannot_confirm_cut(pg):
    data = await _seed(pg)
    await _bind(pg, data)
    versions = await _review(pg, data)
    outsider = uuid4()
    async with pg.factory.begin() as session:
        session.add(
            models.Empleado(
                id=outsider, nombre="Unauthorized", rol="empleado", activo=True
            )
        )
    result = await _cut(pg, data, versions, actor_id=outsider)
    assert result.status == "pending"
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
async def test_grouped_adjustment_reverses_frozen_taxes_and_preserves_liability(
    pg, monkeypatch
):
    data = await _seed(pg, count=2)
    tax = models.CuentaContable(
        id=uuid4(), codigo="1180-001-001", nombre="IVA", tipo="iva"
    )
    retention = models.CuentaContable(
        id=uuid4(), codigo="2130-001-001", nombre="Retencion", tipo="retencion"
    )
    async with pg.factory.begin() as session:
        session.add_all([tax, retention])
    monkeypatch.setattr(
        service,
        "build_expense_accounting_preview",
        AsyncMock(
            return_value={
                "taxes": {
                    "base_gasto": 110,
                    "neto_contrapartida": 116,
                    "iva_trasladado": 16,
                    "iva_account": {"codigo": tax.codigo, "cuenta_contable_id": tax.id},
                    "retenciones": [
                        {
                            "importe": 10,
                            "account": {
                                "codigo": retention.codigo,
                                "cuenta_contable_id": retention.id,
                            },
                        }
                    ],
                }
            }
        ),
    )
    await _bind(pg, data)
    initial_versions = await _review(pg, data)
    initial = await _cut(pg, data, initial_versions)
    assert initial.status == "created", initial.reason
    original_rows = await _lines(pg, initial.poliza.id)
    frozen = [
        (row.id, row.cuenta_codigo, row.debe, row.haber, row.raw_row_json)
        for row in original_rows
    ]
    versions = await _review(pg, data, ["partner_receivable"] * 2)
    monkeypatch.setattr(
        service,
        "build_expense_accounting_preview",
        AsyncMock(side_effect=AssertionError("Must reverse original frozen lines")),
    )
    adjustment = await _cut(
        pg, data, versions, adjustment=True, accounting_date=date(2026, 10, 4)
    )
    assert adjustment.status == "created", adjustment.reason
    assert adjustment.poliza.id != initial.poliza.id
    assert await _journal_count(pg) == 2

    rows = await _lines(pg, adjustment.poliza.id)
    assert not any(row.cuenta_codigo == data.liability.codigo for row in rows)
    assert (
        sum(row.debe or 0 for row in rows if row.cuenta_codigo == data.partner.codigo)
        == 232
    )
    assert (
        sum(row.haber or 0 for row in rows if row.cuenta_codigo == data.account.codigo)
        == 220
    )
    assert sum(row.haber or 0 for row in rows if row.cuenta_codigo == tax.codigo) == 32
    assert (
        sum(row.debe or 0 for row in rows if row.cuenta_codigo == retention.codigo)
        == 20
    )
    assert [
        (row.id, row.cuenta_codigo, row.debe, row.haber, row.raw_row_json)
        for row in await _lines(pg, initial.poliza.id)
    ] == frozen
    retry = await _cut(
        pg, data, versions, adjustment=True, accounting_date=date(2026, 10, 4)
    )
    assert retry.status == "exists" and retry.poliza.id == adjustment.poliza.id
    assert await _journal_count(pg) == 2


@pytest.mark.asyncio
async def test_actual_unreviewed_item_and_cross_informe_version_selection_block(pg):
    data = await _seed(pg, count=2)
    await _bind(pg, data)
    versions = await _review(pg, data)
    async with pg.factory.begin() as session:
        await session.execute(
            delete(models.AmexAccountingReview).where(
                models.AmexAccountingReview.expense_id == data.reports[0].id
            )
        )
    result = await _cut(pg, data, versions)
    assert result.status == "pending" and "unreviewed" in result.reason
    assert await _journal_count(pg) == 0
    async with pg.factory.begin() as session:
        outsider = models.Documento(
            id=uuid4(),
            empleado_id=pg.actor,
            tipo="INFORME",
            estado="aprobado",
            numero_referencia="I-OTHER",
            cuenta_gastos_id=uuid4(),
        )
        session.add(outsider)
        await session.flush()
        actor = await session.get(models.Empleado, pg.actor)
        result = await cuts.review_amex_partida(
            session,
            informe_id=outsider.id,
            expense_id=data.reports[0].id,
            treatment="expense",
            debtor_account_id=None,
            actor=actor,
        )
        assert result.status == "pending"
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "missing_identity",
        "source_amount",
        "report_amount",
        "missing_invoice",
        "inactive_card",
        "inactive_liability",
        "changed_mapping",
        "mixed_period",
        "non_amex_item",
        "missing_activation",
        "historical_source",
    ],
)
async def test_invalid_source_blocks_complete_cut_without_partial_journal(pg, failure):
    data = await _seed(pg, count=2)
    if failure != "missing_identity":
        await _bind(pg, data)
    versions = await _review(pg, data)
    async with pg.factory.begin() as session:
        if failure == "source_amount":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.imports[0].id)
                .values(gasto_cantidad=100)
            )
        elif failure == "report_amount":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(gasto_cantidad=100)
            )
        elif failure == "missing_invoice":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(cfdi_report_id=None)
            )
        elif failure == "inactive_card":
            await session.execute(
                update(models.AmexCardAccount)
                .where(models.AmexCardAccount.id == data.card.id)
                .values(active=False)
            )
        elif failure == "inactive_liability":
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == data.liability.id)
                .values(activo=False)
            )
        elif failure == "changed_mapping":
            account = models.CuentaContable(
                id=uuid4(), codigo="2120-002-063", nombre="Changed", tipo="pasivo"
            )
            session.add(account)
            await session.flush()
            await session.execute(
                update(models.AmexCardAccount)
                .where(models.AmexCardAccount.id == data.card.id)
                .values(liability_cuenta_contable_id=account.id)
            )
        elif failure == "mixed_period":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(fecha=datetime(2026, 11, 2))
            )
        elif failure == "non_amex_item":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(pagado_con_amex_empresa=False)
            )
        elif failure == "missing_activation":
            await session.execute(delete(models.AmexRecognitionActivation))
        elif failure == "historical_source":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.imports[0].id)
                .values(created_at=datetime(2026, 9, 30))
            )
    result = await _cut(pg, data, versions)
    assert result.status == "pending", (failure, result.reason)
    assert result.reason
    assert await _journal_count(pg) == 0
    assert not await _lines(pg)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account_kind",
    ["missing", "inactive", "employee", "legacy_1700", "malformed", "no_reason"],
)
async def test_partner_review_requires_explicit_active_partner_account_and_reason(
    pg, account_kind
):
    data = await _seed(pg)
    account_id = data.partner.id
    reason = "Decision autorizada"
    async with pg.factory.begin() as session:
        if account_kind == "missing":
            account_id = uuid4()
        elif account_kind == "inactive":
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == account_id)
                .values(activo=False)
            )
        elif account_kind == "no_reason":
            reason = " "
        else:
            code = {
                "employee": "1170-001-004",
                "legacy_1700": "1700-002-004",
                "malformed": "1170-002-not-a-code",
            }[account_kind]
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == account_id)
                .values(codigo=code)
            )
        actor = await session.get(models.Empleado, pg.actor)
        result = await cuts.review_amex_partida(
            session,
            informe_id=data.informe.id,
            expense_id=data.reports[0].id,
            treatment="partner_receivable",
            debtor_account_id=account_id,
            actor=actor,
            reason=reason,
        )
        assert result.status == "pending"
    async with pg.factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexAccountingReview)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_automatic_reconciliation_preserves_manual_partner_choice(pg):
    data = await _seed(pg)
    await _bind(pg, data)
    versions = await _review(pg, data, ["partner_receivable"])
    async with pg.factory.begin() as session:
        card = await session.get(models.AmexCardAccount, data.card.id)
        result = await service.ensure_amex_reconciliation_posting(
            session, year=2026, month=10, card_account=card
        )
        assert result.status == "prepared", result.reason
    async with pg.factory() as session:
        review = await session.get(models.AmexAccountingReview, data.reports[0].id)
        assert review.treatment == "partner_receivable"
        assert review.debtor_account_id == data.partner.id
        assert review.version == versions[str(data.reports[0].id)]


@pytest.mark.asyncio
async def test_competing_explicit_links_cannot_own_same_report_representation(pg):
    data = await _seed(pg, count=2)

    async def bind(imported):
        async with pg.factory.begin() as session:
            return await recognition.bind_amex_consumption(
                session,
                imported_expense_id=imported.id,
                report_expense_ids=[data.reports[0].id],
                actor_id=pg.actor,
            )

    results = await asyncio.gather(bind(data.imports[0]), bind(data.imports[1]))
    assert sorted(result.status for result in results) == ["bound", "pending"]
    assert "already_bound" in next(
        result.reason for result in results if result.status == "pending"
    )
    async with pg.factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexRecognitionConsumption)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexRecognitionRepresentation)
            )
            == 2
        )


@pytest.mark.asyncio
async def test_pg_constraints_reject_duplicate_representation_and_initial_cut(
    pg,
):
    data = await _seed(pg)
    await _bind(pg, data)
    async with pg.factory() as session:
        existing = await session.scalar(
            select(models.AmexRecognitionRepresentation).where(
                models.AmexRecognitionRepresentation.expense_id == data.reports[0].id
            )
        )
        session.add(
            models.AmexRecognitionRepresentation(
                id=uuid4(),
                expense_id=existing.expense_id,
                consumption_id=existing.consumption_id,
                role="report",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    versions = await _review(pg, data)
    initial = await _cut(pg, data, versions)
    async with pg.factory() as session:
        original = await session.get(models.AmexAccountingCut, initial.cut.id)
        poliza = await service._create_poliza(
            session,
            origen="test_duplicate",
            numero_poliza=str(uuid4()),
            fecha=datetime(2026, 10, 2),
            beneficiario_nombre="Acceptance",
            concepto="Duplicate prohibited",
            lines=[],
        )
        session.add(
            models.AmexAccountingCut(
                id=uuid4(),
                informe_id=original.informe_id,
                kind="initial",
                state_key=uuid4().hex,
                accounting_date=original.accounting_date,
                accounting_poliza_id=poliza.id,
                actor_id=pg.actor,
                reason="Duplicate",
                snapshot_json={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_actual_fiscal_preview_shared_invoice_coverage_not_multiplied(
    pg, monkeypatch
):
    from devnous.gastos.services.expense_accounting_service import (
        build_expense_accounting_preview,
    )

    data = await _seed(pg, count=2)
    iva = models.CuentaContable(
        id=uuid4(), codigo="1200-001-001", nombre="IVA acreditable", tipo="iva"
    )
    async with pg.factory.begin() as session:
        session.add(iva)
        cfdi = await session.get(models.CFDIReport, data.reports[0].cfdi_report_id)
        cfdi.total = 232
        cfdi.subtotal = 200
        cfdi.total_impuestos_trasladados = 32
        for expense in [*data.imports, *data.reports]:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == expense.id)
                .values(
                    cfdi_report_id=cfdi.id,
                    cfdi_compartido_confirmado=True,
                    cuenta_iva_id=iva.id,
                )
            )
    monkeypatch.setattr(
        service, "build_expense_accounting_preview", build_expense_accounting_preview
    )
    await _bind(pg, data)
    versions = await _review(pg, data)
    result = await _cut(pg, data, versions)
    assert result.status == "created", result.reason
    rows = await _lines(pg, result.poliza.id)
    assert (
        sum(row.debe or 0 for row in rows if row.cuenta_codigo == data.account.codigo)
        == 200
    )
    assert sum(row.debe or 0 for row in rows if row.cuenta_codigo == iva.codigo) == 32
    assert (
        sum(
            row.haber or 0 for row in rows if row.cuenta_codigo == data.liability.codigo
        )
        == 232
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [False, True])
async def test_payment_liability_santander_and_mapping_drift(pg, drift):
    data = await _seed(pg)
    await _bind(pg, data)
    versions = await _review(pg, data, ["partner_receivable"])
    initial = await _cut(pg, data, versions)
    documento = SimpleNamespace(
        id=uuid4(),
        metodo_pago="AMEX",
        notas=service.amex_payment_card_marker(
            data.card.id, liability_account_id=data.liability.id
        ),
        monto_solicitado=116,
        monto_total=116,
        numero_referencia="S-PAY",
    )
    async with pg.factory.begin() as session:
        if drift:
            account = models.CuentaContable(
                id=uuid4(), codigo="2120-002-063", nombre="Drift", tipo="pasivo"
            )
            session.add(account)
            await session.flush()
            await session.execute(
                update(models.AmexCardAccount)
                .where(models.AmexCardAccount.id == data.card.id)
                .values(liability_cuenta_contable_id=account.id)
            )
        result = await service.ensure_amex_payment_posting(
            session, documento=documento, payment_date=date(2026, 10, 3)
        )
    if drift:
        assert result.status == "pending" and "mapping_changed" in result.reason
        assert await _journal_count(pg) == 1
    else:
        assert result.status == "created", result.reason
        rows = await _lines(pg, result.poliza.id)
        assert [(row.cuenta_codigo, row.debe or 0, row.haber or 0) for row in rows] == [
            (data.liability.codigo, 116, 0),
            ("1120-001-001", 0, 116),
        ]
        assert await _journal_count(pg) == 2
    assert initial.poliza.id


@pytest.mark.asyncio
async def test_concurrent_grouped_adjustment_is_unique_and_different_target_is_blocked(
    pg,
):
    data = await _seed(pg, count=2)
    await _bind(pg, data)
    initial_versions = await _review(pg, data)
    initial = await _cut(pg, data, initial_versions)
    assert initial.status == "created"
    versions = await _review(pg, data, ["partner_receivable"] * 2)
    results = await asyncio.gather(
        _cut(pg, data, versions, adjustment=True),
        _cut(pg, data, versions, adjustment=True),
    )
    assert sorted(result.status for result in results) == ["created", "exists"]
    assert results[0].poliza.id == results[1].poliza.id
    assert await _journal_count(pg) == 2
    async with pg.factory.begin() as session:
        other = models.CuentaContable(
            id=uuid4(), codigo="1170-002-005", nombre="Otro socio", tipo="deudor"
        )
        session.add(other)
        await session.flush()
        actor = await session.get(models.Empleado, pg.actor)
        changed = await cuts.review_amex_partida(
            session,
            informe_id=data.informe.id,
            expense_id=data.reports[0].id,
            treatment="partner_receivable",
            debtor_account_id=other.id,
            actor=actor,
            reason="Nueva propuesta",
        )
        assert changed.status == "reviewed"
        versions[str(data.reports[0].id)] = changed.review.version
    blocked = await _cut(pg, data, versions, adjustment=True)
    assert blocked.status == "pending"
    assert "partner_account_change" in blocked.reason
    assert await _journal_count(pg) == 2


@pytest.mark.asyncio
async def test_partitioned_charge_keeps_each_item_whole_with_partner_without_iva(
    pg, monkeypatch
):
    data = await _seed(pg, count=2)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id == data.imports[0].id)
            .values(gasto_cantidad=232)
        )
        await session.execute(
            delete(models.ExpenseReport).where(
                models.ExpenseReport.id == data.imports[1].id
            )
        )
        invoice = await session.get(models.CFDIReport, data.imports[0].cfdi_report_id)
        invoice.total = 232
        invoice.subtotal = 200
        for report in data.reports:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == report.id)
                .values(cfdi_report_id=invoice.id, cfdi_compartido_confirmado=True)
            )
        imported = await session.get(models.ExpenseReport, data.imports[0].id)
        imported.cfdi_compartido_confirmado = True
        result = await recognition.bind_amex_consumption(
            session,
            imported_expense_id=imported.id,
            report_expense_ids=[report.id for report in data.reports],
            actor_id=pg.actor,
        )
        assert result.status == "bound", result.reason
    iva = models.CuentaContable(
        id=uuid4(), codigo="1200-001-001", nombre="IVA", tipo="iva"
    )
    async with pg.factory.begin() as session:
        session.add(iva)
    monkeypatch.setattr(
        service,
        "build_expense_accounting_preview",
        AsyncMock(
            return_value={
                "taxes": {
                    "base_gasto": 100,
                    "neto_contrapartida": 116,
                    "iva_trasladado": 16,
                    "iva_account": {"codigo": iva.codigo, "cuenta_contable_id": iva.id},
                }
            }
        ),
    )
    versions = await _review(pg, data, ["expense", "partner_receivable"])
    result = await _cut(pg, data, versions)
    assert result.status == "created", result.reason
    rows = await _lines(pg, result.poliza.id)
    assert sum(row.debe or 0 for row in rows if row.cuenta_codigo == iva.codigo) == 16
    assert (
        sum(row.debe or 0 for row in rows if row.cuenta_codigo == data.partner.codigo)
        == 116
    )
    assert (
        sum(
            row.haber or 0 for row in rows if row.cuenta_codigo == data.liability.codigo
        )
        == 232
    )
    assert len(result.cut.snapshot_json["consumption_ids"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "zero",
        "negative",
        "foreign_currency",
        "unknown_card",
        "historical",
        "report_mismatch",
        "cancelled",
        "duplicate_selection",
    ],
)
async def test_explicit_identity_binding_invalid_inputs_leave_no_receipt(pg, failure):
    data = await _seed(pg)
    async with pg.factory.begin() as session:
        if failure in {"zero", "negative"}:
            amount = 0 if failure == "zero" else -1
            await session.execute(
                update(models.ExpenseReport)
                .where(
                    models.ExpenseReport.id.in_(
                        [data.imports[0].id, data.reports[0].id]
                    )
                )
                .values(gasto_cantidad=amount)
            )
        elif failure == "foreign_currency":
            await session.execute(
                update(models.ExpenseReport)
                .where(
                    models.ExpenseReport.id.in_(
                        [data.imports[0].id, data.reports[0].id]
                    )
                )
                .values(currency="USD")
            )
        elif failure == "unknown_card":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.imports[0].id)
                .values(ultimos_4_digitos="9999")
            )
        elif failure == "historical":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.imports[0].id)
                .values(created_at=datetime(2026, 9, 30))
            )
        elif failure == "report_mismatch":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(ultimos_4_digitos="9999")
            )
        elif failure == "cancelled":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(estado_gasto="cancelado")
            )
        selected = [data.reports[0].id] * (2 if failure == "duplicate_selection" else 1)
        result = await recognition.bind_amex_consumption(
            session,
            imported_expense_id=data.imports[0].id,
            report_expense_ids=selected,
            actor_id=pg.actor,
        )
        assert result.status == "pending" and result.reason
    async with pg.factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexRecognitionConsumption)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexRecognitionRepresentation)
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["amount", "classification", "cfdi"])
async def test_review_check_rejects_source_changed_after_finance_review(pg, change):
    data = await _seed(pg)
    await _bind(pg, data)
    versions = await _review(pg, data)
    async with pg.factory.begin() as session:
        if change == "amount":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(gasto_cantidad=120)
            )
        elif change == "classification":
            alternate = models.CuentaContable(
                id=uuid4(),
                codigo="5300-010-002",
                nombre="Nueva clasificacion",
                tipo="gasto",
            )
            session.add(alternate)
            await session.flush()
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(cuenta_contable_id=alternate.id)
            )
        else:
            await session.execute(
                update(models.CFDIReport)
                .where(models.CFDIReport.id == data.reports[0].cfdi_report_id)
                .values(subtotal=99)
            )
    blocked = await _cut(pg, data, versions)
    assert blocked.status == "pending"
    assert blocked.reason == "stale_amex_review_source"
    assert await _journal_count(pg) == 0
    assert not await _lines(pg)
    if change == "amount":
        # A checked receipt is immutable: restore its amount before rechecking.
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(gasto_cantidad=116)
            )
    fresh_versions = await _review(pg, data)
    if change != "amount":
        assert (
            fresh_versions[str(data.reports[0].id)] > versions[str(data.reports[0].id)]
        )
    fresh = await _cut(pg, data, fresh_versions)
    assert fresh.status == "created", fresh.reason
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_owner_run_sql_migration_replay_preserves_cutoff_and_constraints():
    """Execute the actual SQL, rather than assuming ORM create_all proves it."""
    from pathlib import Path

    import asyncpg

    raw = os.environ.get("TEST_AMEX_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_AMEX_DATABASE_URL to isolated localhost PostgreSQL")
    url = make_url(raw)
    if url.host not in {"localhost", "127.0.0.1"} or url.port != 55471:
        pytest.fail("Migration acceptance must use isolated localhost:55471")
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    schema = "amex_migration_" + uuid4().hex
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        for name in (
            "expense_reports",
            "amex_card_accounts",
            "cuentas_contables",
            "accounting_polizas",
            "empleados",
            "documentos",
        ):
            await conn.execute(f"CREATE TABLE {name} (id UUID PRIMARY KEY)")
        sql = (
            Path(__file__).resolve().parents[2]
            / "database/migrations/20261001_amex_shared_recognition.sql"
        ).read_text()
        await conn.execute(sql)
        cutoff = await conn.fetchval(
            "SELECT activated_at FROM amex_recognition_activation WHERE id=1"
        )
        await conn.execute(sql)
        assert (
            await conn.fetchval(
                "SELECT activated_at FROM amex_recognition_activation WHERE id=1"
            )
            == cutoff
        )
        assert (
            await conn.fetchval("SELECT count(*) FROM amex_recognition_activation") == 1
        )
        expense, report, card, liability, actor, informe, journal, other_journal = [
            uuid4() for _ in range(8)
        ]
        for name, ids in {
            "expense_reports": [expense, report],
            "amex_card_accounts": [card],
            "cuentas_contables": [liability],
            "empleados": [actor],
            "documentos": [informe],
            "accounting_polizas": [journal, other_journal],
        }.items():
            for value in ids:
                await conn.execute(f"INSERT INTO {name}(id) VALUES($1)", value)
        consumption = uuid4()
        await conn.execute(
            "INSERT INTO amex_recognition_consumptions (id, "
            "imported_expense_id, card_account_id, amount, currency, "
            "economic_date, liability_account_id, liability_code, "
            "actor_id) VALUES "
            "($1,$2,$3,116,'MXN','2026-10-02',$4,'2120-002-062',$5)",
            consumption,
            expense,
            card,
            liability,
            actor,
        )
        await conn.execute(
            "INSERT INTO amex_recognition_representations(id, "
            "consumption_id, expense_id, role) "
            "VALUES($1,$2,$3,'report')",
            uuid4(),
            consumption,
            report,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO amex_recognition_representations(id, "
                "consumption_id, expense_id, role) "
                "VALUES($1,$2,$3,'report')",
                uuid4(),
                consumption,
                report,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO "
                "amex_accounting_reviews(expense_id,informe_id,treatment,"
                "actor_id,source_key) "
                "VALUES($1,$2,'expense',$3,$4)",
                uuid4(),
                informe,
                actor,
                "a" * 64,
            )
        insert_cut = (
            "INSERT INTO "
            "amex_accounting_cuts(id,informe_id,kind,state_key,accounting_date,"
            "accounting_poliza_id,actor_id,snapshot_json) "
            "VALUES($1,$2,'initial',$3,'2026-10-02',$4,$5,'{}'::jsonb)"
        )
        await conn.execute(insert_cut, uuid4(), informe, "b" * 64, journal, actor)
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                insert_cut, uuid4(), informe, "c" * 64, other_journal, actor
            )
        assert await conn.fetchval("SELECT count(*) FROM amex_accounting_cuts") == 1
    finally:
        await conn.execute("SET search_path TO public")
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["code", "inactive"])
async def test_review_detects_related_expense_account_drift_with_stable_uuid(
    pg, change
):
    data = await _seed(pg)
    await _bind(pg, data)
    versions = await _review(pg, data)
    async with pg.factory.begin() as session:
        values = {"codigo": "5300-010-099"} if change == "code" else {"activo": False}
        await session.execute(
            update(models.CuentaContable)
            .where(models.CuentaContable.id == data.account.id)
            .values(**values)
        )
    blocked = await _cut(pg, data, versions)
    assert blocked.status == "pending"
    assert blocked.reason == "stale_amex_review_source"
    assert await _journal_count(pg) == 0
    assert not await _lines(pg)
    if change == "inactive":
        async with pg.factory.begin() as session:
            actor = await session.get(models.Empleado, pg.actor)
            invalid = await cuts.review_amex_partida(
                session,
                informe_id=data.informe.id,
                expense_id=data.reports[0].id,
                treatment="expense",
                debtor_account_id=None,
                actor=actor,
                reason="Decision de Finanzas",
            )
            assert invalid.status in {"pending", "reviewed"}
            if invalid.status == "reviewed":
                versions[str(data.reports[0].id)] = invalid.review.version
        still_blocked = await _cut(pg, data, versions)
        assert still_blocked.status == "pending"
        assert await _journal_count(pg) == 0
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == data.account.id)
                .values(activo=True)
            )
    fresh_versions = await _review(pg, data)
    if change == "code":
        assert (
            fresh_versions[str(data.reports[0].id)] > versions[str(data.reports[0].id)]
        )
    fresh = await _cut(pg, data, fresh_versions)
    assert fresh.status == "created", fresh.reason
    if change == "code":
        assert "5300-010-099" in {
            line.cuenta_codigo
            for line in await _lines(pg, fresh.poliza.id)
            if line.debe
        }


@pytest.mark.asyncio
async def test_review_detects_selected_partner_account_code_drift_with_stable_uuid(pg):
    data = await _seed(pg)
    await _bind(pg, data)
    versions = await _review(pg, data, ["partner_receivable"])
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.CuentaContable)
            .where(models.CuentaContable.id == data.partner.id)
            .values(codigo="1170-002-099")
        )
    blocked = await _cut(pg, data, versions)
    assert blocked.status == "pending"
    assert blocked.reason == "stale_amex_review_source"
    assert await _journal_count(pg) == 0
    fresh_versions = await _review(pg, data, ["partner_receivable"])
    assert fresh_versions[str(data.reports[0].id)] > versions[str(data.reports[0].id)]
    fresh = await _cut(pg, data, fresh_versions)
    assert fresh.status == "created", fresh.reason
    assert "1170-002-099" in {
        line.cuenta_codigo for line in await _lines(pg, fresh.poliza.id) if line.debe
    }
