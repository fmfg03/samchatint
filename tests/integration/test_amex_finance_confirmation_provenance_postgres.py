"""Regression for independent review findings using actual owner migration."""

from datetime import date

import pytest
from sqlalchemy import func, select, update
from test_amex_approved_informe_service_postgres import (
    confirm,
)
from test_amex_approved_informe_service_postgres import legacy_pg as _legacy_pg_fixture
from test_amex_approved_informe_service_postgres import (
    payload,
)
from test_amex_approved_informe_service_postgres import pg as _migrated_pg_fixture
from test_amex_approved_informe_service_postgres import (
    prepare,
    setup_report,
)
from test_amex_shared_recognition_postgres import _review

from devnous.gastos import models
from devnous.gastos.services import amex_accounting_cut_service as cuts

legacy_pg = _legacy_pg_fixture
pg = _migrated_pg_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["treatment", "reason", "source", "account"])
async def test_adopted_null_fk_review_cannot_bypass_finance_confirmation(pg, change):
    data = await setup_report(pg)
    await _review(pg, data)
    preview = await prepare(pg, data)
    result = await confirm(pg, payload(data, preview, decisions=[]))
    assert result.status == "created", result.reason
    expense_id = data.reports[0].id
    async with pg.factory.begin() as session:
        review = await session.get(models.AmexAccountingReview, expense_id)
        assert review.source_evidence_id is None
        original = (
            review.actor_id,
            review.reviewed_at,
            review.version,
            review.source_key,
        )
        actor = await session.get(models.Empleado, pg.actor)
        unchanged = await cuts.review_amex_partida(
            session,
            informe_id=data.informe.id,
            expense_id=expense_id,
            actor=actor,
            treatment=review.treatment,
            debtor_account_id=review.debtor_account_id,
            reason=review.reason,
        )
        assert unchanged.status == "reviewed"
        assert (
            review.actor_id,
            review.reviewed_at,
            review.version,
            review.source_key,
        ) == original
        treatment, account, reason = (
            review.treatment,
            review.debtor_account_id,
            review.reason,
        )
        if change in {"treatment", "account"}:
            treatment, account, reason = (
                "partner_receivable",
                data.partner.id,
                "Cargo explícito",
            )
        elif change == "reason":
            reason += " cambiado"
        else:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == expense_id)
                .values(concepto="Fuente material cambiada")
            )
        changed = await cuts.review_amex_partida(
            session,
            informe_id=data.informe.id,
            expense_id=expense_id,
            actor=actor,
            treatment=treatment,
            debtor_account_id=account,
            reason=reason,
        )
        assert (
            changed.status == "pending"
            and changed.reason == "amex_review_update_requires_confirmation"
        )
        assert (
            review.actor_id,
            review.reviewed_at,
            review.version,
            review.source_key,
        ) == original


@pytest.mark.asyncio
async def test_adopted_legacy_projection_can_change_only_with_new_finance_receipt(pg):
    data = await setup_report(pg)
    await _review(pg, data)
    before = await prepare(pg, data)
    initial = await confirm(pg, payload(data, before, decisions=[]))
    assert initial.status == "created"
    preview = await prepare(pg, data)
    values = payload(
        data,
        preview,
        [
            dict(
                expense_ids=[str(data.reports[0].id)],
                treatment="partner_receivable",
                debtor_account_id=str(data.partner.id),
                reason="Cargo al socio declarado",
            )
        ],
    )
    values.update(
        adjustment=True,
        accounting_date=date(2026, 10, 4),
        reason="Reclasificación consolidada",
    )
    adjusted = await confirm(pg, values)
    assert adjusted.status == "created", adjusted.reason
    async with pg.factory() as session:
        evidence = await session.get(
            models.AmexReportSourceEvidence,
            adjusted.cut.snapshot_json["source_evidence_id"],
        )
        previous = evidence.decisions_json["prior_review_projection"][0]
        assert previous["source_evidence_id"] is None
        assert previous["version"] == before.current_versions[str(data.reports[0].id)]
        assert previous["actor_id"] == str(before.reviews[data.reports[0].id].actor_id)
        review = await session.get(models.AmexAccountingReview, data.reports[0].id)
        assert (
            str(review.source_evidence_id) == str(evidence.id)
            and review.version == previous["version"] + 1
        )


@pytest.mark.asyncio
async def test_changed_adjustment_payload_cannot_reuse_other_confirmation_cut(pg):
    data = await setup_report(pg)
    initial = await confirm(pg, payload(data, await prepare(pg, data)))
    assert initial.status == "created"
    decisions = [
        dict(
            expense_ids=[str(data.reports[0].id)],
            treatment="partner_receivable",
            debtor_account_id=str(data.partner.id),
            reason="Cargo al socio declarado",
        )
    ]
    values = payload(data, await prepare(pg, data), decisions)
    values.update(
        adjustment=True, accounting_date=date(2026, 10, 4), reason="Motivo original"
    )
    first = await confirm(pg, values)
    assert first.status == "created", first.reason
    identical = await confirm(pg, values)
    assert identical.status == "exists" and identical.cut.id == first.cut.id
    changed = payload(data, await prepare(pg, data), decisions)
    changed.update(
        adjustment=True, accounting_date=date(2026, 10, 4), reason="Motivo diferente"
    )
    rejected = await confirm(pg, changed)
    assert (
        rejected.status == "pending"
        and rejected.reason == "amex_finance_confirmation_payload_conflict"
    )
    async with pg.factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexReportSourceEvidence)
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexAccountingCut)
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AccountingPoliza)
            )
            == 2
        )
        original = await session.get(
            models.AmexReportSourceEvidence,
            first.cut.snapshot_json["source_evidence_id"],
        )
        assert original.reason == "Motivo original"


@pytest.mark.asyncio
async def test_explicit_amex_report_with_null_legacy_origin_is_real_fiscal_source(pg):
    data = await setup_report(pg)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id == data.reports[0].id)
            .values(origen=None)
        )
    result = await confirm(pg, payload(data, await prepare(pg, data)))
    assert result.status == "created", result.reason
    async with pg.factory() as session:
        consumption = (
            await session.scalars(select(models.AmexRecognitionConsumption))
        ).one()
        assert consumption.source_kind == "report"
        assert consumption.source_report_expense_id == data.reports[0].id
        assert consumption.imported_expense_id is None
