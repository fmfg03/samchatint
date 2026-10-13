"""Actual transactions for approved-report finance source and later bank bridge."""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from test_amex_shared_recognition_postgres import _seed, _review, _journal_count
from test_amex_shared_recognition_postgres import pg as _pg_fixture
from devnous.gastos import models
from devnous.gastos.services import amex_accounting_cut_service as cuts
from devnous.gastos.services import amex_recognition_service as recognition

legacy_pg = _pg_fixture


@pytest_asyncio.fixture
async def pg(legacy_pg):
    """Run the owner DDL as well as ORM services in this disposable schema."""
    import re
    from pathlib import Path

    ddl = (
        Path(__file__).resolve().parents[2]
        / "database/migrations/20261013_amex_approved_informe_recognition.sql"
    ).read_text()
    ddl = re.sub(r"(?m)^(BEGIN|COMMIT);\s*$", "", ddl)
    engine = legacy_pg.factory.kw["bind"]
    async with engine.begin() as connection:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.execute(ddl)
    return legacy_pg


async def setup_report(pg, count=1):
    data = await _seed(pg, count=count)
    async with pg.factory.begin() as session:
        # Bank import is independent; no identity inferred from shared invoice.
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id.in_([e.id for e in data.imports]))
            .values(cfdi_report_id=None)
        )
    return data


async def prepare(pg, data):
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        return await cuts.preview_amex_approved_informe(
            session, informe_id=data.informe.id, actor=actor
        )


def payload(data, preview, decisions=None):
    return dict(
        informe_id=data.informe.id,
        expected_source_key=preview.source_key,
        expected_versions=preview.current_versions,
        expected_approval_id=(
            preview.approval_event.id if preview.approval_event else None
        ),
        expected_approval_version=preview.approval_version,
        decisions=(
            decisions
            if decisions is not None
            else [
                dict(
                    expense_ids=[str(e.id) for e in data.reports],
                    treatment="expense",
                    reason="",
                )
            ]
        ),
        accounting_date=date(2026, 10, 2),
        confirmed=True,
        reason="Atestación actual de informe histórico",
    )


async def confirm(pg, values):
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        return await cuts.confirm_amex_approved_informe_cut(
            session, actor=actor, **values
        )


@pytest.mark.asyncio
async def test_report_first_full_cut_replay_original_payload_and_late_bank(pg):
    data = await setup_report(pg)
    preview = await prepare(pg, data)
    assert preview.status == "ready" and preview.current_versions == {
        str(data.reports[0].id): 0
    }
    assert await _journal_count(pg) == 0
    values = payload(data, preview)
    result = await confirm(pg, values)
    assert result.status == "created", result.reason
    same = await confirm(pg, values)
    assert same.status == "exists" and same.cut.id == result.cut.id
    async with pg.factory() as session:
        consumption = (
            await session.scalars(select(models.AmexRecognitionConsumption))
        ).one()
        evidence = (
            await session.scalars(select(models.AmexReportSourceEvidence))
        ).one()
        review = await session.get(models.AmexAccountingReview, data.reports[0].id)
        assert (
            consumption.source_kind == "report"
            and consumption.imported_expense_id is None
        )
        assert consumption.source_report_expense_id == data.reports[0].id
        assert review.source_evidence_id == evidence.id
        assert str(evidence.id) == result.cut.snapshot_json["source_evidence_id"]
        target_id = consumption.id
    async with pg.factory.begin() as session:
        bound = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=[target_id],
            actor_id=pg.actor,
        )
        assert bound.status == "bound", bound.reason
        assert bound.consumption.accounting_poliza_id is None
    async with pg.factory.begin() as session:
        repeated = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=[target_id],
            actor_id=pg.actor,
        )
        assert repeated.status == "exists", repeated.reason
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_fresh_reviews_adopt_actor_version_and_null_evidence(pg):
    data = await setup_report(pg)
    await _review(pg, data)
    preview = await prepare(pg, data)
    assert data.reports[0].id in preview.fresh_review_ids
    before = preview.reviews[data.reports[0].id]
    result = await confirm(pg, payload(data, preview, decisions=[]))
    assert result.status == "created", result.reason
    async with pg.factory() as session:
        review = await session.get(models.AmexAccountingReview, data.reports[0].id)
        evidence = (
            await session.scalars(select(models.AmexReportSourceEvidence))
        ).one()
        assert review.actor_id == before.actor_id and review.version == before.version
        assert (
            review.reviewed_at == before.reviewed_at
            and review.source_evidence_id is None
        )
        assert (
            evidence.decisions_json["adopted_reviews"][0]["source_key"]
            == before.source_key
        )


@pytest.mark.asyncio
async def test_stale_projection_preserved_and_incrementing_replay_not_stale(pg):
    data = await setup_report(pg)
    await _review(pg, data)
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id == data.reports[0].id)
            .values(concepto="Actualizado")
        )
    preview = await prepare(pg, data)
    assert data.reports[0].id not in preview.fresh_review_ids
    values = payload(data, preview)
    result = await confirm(pg, values)
    assert result.status == "created", result.reason
    async with pg.factory() as session:
        review = await session.get(models.AmexAccountingReview, data.reports[0].id)
        evidence = (
            await session.scalars(select(models.AmexReportSourceEvidence))
        ).one()
        assert review.version == 2
        prior = evidence.decisions_json["prior_review_projection"][0]
        assert prior["version"] == 1 and prior["source_key"] != review.source_key
    assert (await confirm(pg, values)).status == "exists"
    changed = {
        **values,
        "decisions": [
            dict(
                expense_ids=[str(data.reports[0].id)],
                treatment="partner_receivable",
                debtor_account_id=str(data.partner.id),
                reason="Cambio",
            )
        ],
    }
    assert (await confirm(pg, changed)).status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "missing_card",
        "missing_decision",
        "overlap",
        "source_changed",
        "historical",
        "no_reason",
    ],
)
async def test_invalid_consolidated_input_rolls_back_all_reviews_and_evidence(
    pg, failure
):
    data = await setup_report(pg)
    if failure == "missing_card":
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(ultimos_4_digitos=None)
            )
    preview = await prepare(pg, data)
    values = payload(data, preview)
    if failure == "missing_decision":
        values["decisions"] = []
    elif failure == "overlap":
        values["decisions"] *= 2
    elif failure == "source_changed":
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(concepto="Cambio después preview")
            )
    elif failure == "historical":
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(created_at=date(2026, 9, 2))
            )
        values = payload(data, await prepare(pg, data))
    elif failure == "no_reason":
        values["reason"] = ""
    result = await confirm(pg, values)
    assert result.status == "pending", result.reason
    async with pg.factory() as session:
        for model in [
            models.AmexAccountingReview,
            models.AmexReportSourceEvidence,
            models.AmexRecognitionConsumption,
            models.AccountingPoliza,
        ]:
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_late_bridge_one_bank_to_multiple_reports_exact_no_double_journal(pg):
    data = await setup_report(pg, count=2)
    result = await confirm(pg, payload(data, await prepare(pg, data)))
    assert result.status == "created", result.reason
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id == data.imports[0].id)
            .values(gasto_cantidad=232)
        )
        targets = list(
            (await session.scalars(select(models.AmexRecognitionConsumption.id))).all()
        )
        bound = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=targets,
            actor_id=pg.actor,
        )
        assert bound.status == "bound", bound.reason
    async with pg.factory.begin() as session:
        conflict = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[1].id,
            report_consumption_ids=[targets[0]],
            actor_id=pg.actor,
        )
        assert conflict.status == "pending"
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "partner_no_account",
        "partner_no_reason",
        "unexpected_account",
        "missing_invoice",
        "closed_period",
        "period_mismatch",
        "currency",
        "unknown_review_ids",
    ],
)
async def test_financial_and_fiscal_exceptions_leave_no_partial_state(pg, bad):
    data = await setup_report(pg)
    if bad in {"missing_invoice", "currency"}:
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(
                    **(
                        {"cfdi_report_id": None}
                        if bad == "missing_invoice"
                        else {"currency": "USD"}
                    )
                )
            )
    preview = await prepare(pg, data)
    values = payload(data, preview)
    group = values["decisions"][0]
    if bad.startswith("partner"):
        group["treatment"] = "partner_receivable"
        group["reason"] = "Cargo confirmado" if bad == "partner_no_account" else ""
        if bad == "partner_no_reason":
            group["debtor_account_id"] = str(data.partner.id)
    elif bad == "unexpected_account":
        group["debtor_account_id"] = str(data.partner.id)
    elif bad == "closed_period":
        async with pg.factory.begin() as session:
            session.add(
                models.AccountingClosePeriod(
                    fiscal_year=2026, fiscal_month=10, status="closed"
                )
            )
    elif bad == "period_mismatch":
        values["accounting_date"] = date(2026, 11, 2)
    elif bad == "unknown_review_ids":
        values["expected_versions"] = {}
    result = await confirm(pg, values)
    assert result.status == "pending", result.reason
    async with pg.factory() as session:
        for model in [
            models.AmexAccountingReview,
            models.AmexReportSourceEvidence,
            models.AmexRecognitionConsumption,
            models.AccountingPoliza,
        ]:
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_posting_failure_after_reviews_and_source_flush_rolls_back(
    pg, monkeypatch
):
    from unittest.mock import AsyncMock

    data = await setup_report(pg)
    values = payload(data, await prepare(pg, data))
    monkeypatch.setattr(
        cuts,
        "_create_poliza",
        AsyncMock(side_effect=RuntimeError("Injected posting failure")),
    )
    with pytest.raises(RuntimeError, match="Injected posting"):
        await confirm(pg, values)
    async with pg.factory() as session:
        for model in [
            models.AmexAccountingReview,
            models.AmexReportSourceEvidence,
            models.AmexRecognitionConsumption,
            models.AmexRecognitionRepresentation,
            models.AccountingPoliza,
        ]:
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_operational_nonfinance_evidence_preserved_no_initial_expense_reason(
    pg,
):
    from uuid import uuid4
    from devnous.gastos.services.amex_report_evidence_service import (
        record_amex_final_report_approval,
    )

    data = await setup_report(pg)
    operator = uuid4()
    async with pg.factory.begin() as session:
        session.add(
            models.Empleado(id=operator, nombre="Aprobador operativo", rol="pm")
        )
        await session.flush()
        informe = await session.get(models.Documento, data.informe.id)
        approval = models.Aprobacion(
            id=uuid4(),
            tipo_entidad="documento",
            entidad_id=informe.id,
            aprobador_id=operator,
            accion="aprobar",
            fecha=informe.aprobado_en,
        )
        session.add(approval)
        await session.flush()
        operational = await record_amex_final_report_approval(
            session, informe=informe, approval=approval
        )
        assert operational.actor_id == operator
    preview = await prepare(pg, data)
    assert preview.operational_evidence.id == operational.id
    values = payload(data, preview)
    values["reason"] = ""
    result = await confirm(pg, values)
    assert result.status == "created", result.reason
    async with pg.factory() as session:
        evidence = (
            await session.scalars(
                select(models.AmexReportSourceEvidence).where(
                    models.AmexReportSourceEvidence.kind == "finance_confirmation"
                )
            )
        ).one()
        assert (
            evidence.actor_id == pg.actor
            and evidence.operational_evidence_id == operational.id
        )
        assert evidence.decisions_json["resulting_reviews"][0]["version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("same_payload", [True, False])
async def test_concurrent_finance_confirmations_block_until_commit_and_have_one_cut(
    pg, same_payload
):
    import asyncio
    from sqlalchemy import text

    data = await setup_report(pg)
    values = payload(data, await prepare(pg, data))
    release = asyncio.Event()
    created = asyncio.Event()
    waiting_pid = asyncio.Future()

    async def first():
        async with pg.factory.begin() as session:
            actor = await session.get(models.Empleado, pg.actor)
            result = await cuts.confirm_amex_approved_informe_cut(
                session, actor=actor, **values
            )
            assert result.status == "created", result.reason
            created.set()
            await release.wait()
            return result

    async def second():
        second_values = dict(values)
        if not same_payload:
            second_values["decisions"] = [
                dict(
                    expense_ids=[str(data.reports[0].id)],
                    treatment="partner_receivable",
                    debtor_account_id=str(data.partner.id),
                    reason="Otra decisión",
                )
            ]
        async with pg.factory.begin() as session:
            waiting_pid.set_result(
                await session.scalar(text("SELECT pg_backend_pid()"))
            )
            actor = await session.get(models.Empleado, pg.actor)
            return await cuts.confirm_amex_approved_informe_cut(
                session, actor=actor, **second_values
            )

    first_task = asyncio.create_task(first())
    second_task = None
    try:
        await asyncio.wait_for(created.wait(), 5)
        second_task = asyncio.create_task(second())
        pid = await asyncio.wait_for(waiting_pid, 5)

        async def observe_lock():
            async with pg.factory() as session:
                while True:
                    blocked = await session.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' "
                            "FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": pid},
                    )
                    if blocked:
                        return
                    await asyncio.sleep(0)

        await asyncio.wait_for(observe_lock(), 5)
        assert not second_task.done()
        release.set()
        first_result, second_result = await asyncio.wait_for(
            asyncio.gather(first_task, second_task), 5
        )
        assert second_result.status == ("exists" if same_payload else "pending")
        if same_payload:
            assert second_result.cut.id == first_result.cut.id
    finally:
        release.set()
        await first_task
        if second_task:
            await second_task
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_bridge_fiscal_constructor_refuses_header_even_without_children(pg):
    from uuid import uuid4
    from devnous.gastos.services import amex_accounting_posting_service as posting

    data = await setup_report(pg)
    async with pg.factory.begin() as session:
        session.add(
            models.AmexRecognitionConsumption(
                id=uuid4(),
                source_kind="statement",
                binding_mode="report_bridge",
                imported_expense_id=data.imports[0].id,
                card_account_id=data.card.id,
                amount=116,
                currency="MXN",
                economic_date=date(2026, 10, 2),
                liability_account_id=data.liability.id,
                liability_code=data.liability.codigo,
                actor_id=pg.actor,
                classification_json={},
            )
        )
    async with pg.factory() as session:
        source = await session.get(models.ExpenseReport, data.imports[0].id)
        rows, amount, reason = await posting._single_expense_fiscal_lines(
            session, source, meta={}
        )
        assert rows is None and reason == "amex_bridge_header_not_postable"
        group_rows, _, group_reason = await posting._pase_group_fiscal_lines(
            session, [source, source], meta={}
        )
        assert group_rows is None and group_reason == "amex_bridge_header_not_postable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "card_mapping_after_preview",
        "partner_mapping_after_preview",
        "no_confirm",
        "bad_date",
        "duplicate_ids",
        "unknown_treatment",
        "invalid_uuid",
        "stale_approval",
    ],
)
async def test_preview_catalog_and_explicit_payload_guards(pg, bad):
    from datetime import datetime
    from uuid import uuid4

    data = await setup_report(pg)
    preview = await prepare(pg, data)
    values = payload(data, preview)
    if bad == "card_mapping_after_preview":
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == data.account.id)
                .values(codigo="5300-010-999")
            )
    elif bad == "partner_mapping_after_preview":
        async with pg.factory.begin() as session:
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == data.partner.id)
                .values(codigo="1170-002-005")
            )
    elif bad == "no_confirm":
        values["confirmed"] = False
    elif bad == "bad_date":
        values["accounting_date"] = datetime(2026, 10, 2)
    elif bad == "duplicate_ids":
        values["decisions"][0]["expense_ids"] *= 2
    elif bad == "unknown_treatment":
        values["decisions"][0]["treatment"] = "inventado"
    elif bad == "invalid_uuid":
        values["decisions"][0]["expense_ids"] = ["no-uuid"]
    else:
        values["expected_approval_id"] = uuid4()
    result = await confirm(pg, values)
    assert result.status == "pending", result.reason
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "unknown_target",
        "duplicate_targets",
        "unposted_target",
        "source_cancelled",
        "source_coi",
        "source_currency",
        "source_amount",
        "report_changed",
        "target_reversal",
        "card_inactive",
        "liability_inactive",
    ],
)
async def test_bank_bridge_requires_exact_unchanged_posted_targets(pg, bad):
    from uuid import uuid4

    data = await setup_report(pg)
    result = await confirm(pg, payload(data, await prepare(pg, data)))
    assert result.status == "created", result.reason
    async with pg.factory.begin() as session:
        targets = list(
            (await session.scalars(select(models.AmexRecognitionConsumption.id))).all()
        )
        if bad == "unknown_target":
            targets = [uuid4()]
        elif bad == "duplicate_targets":
            targets *= 2
        elif bad == "unposted_target":
            await session.execute(
                update(models.AmexRecognitionConsumption)
                .where(models.AmexRecognitionConsumption.id == targets[0])
                .values(accounting_poliza_id=None)
            )
        elif bad in {
            "source_cancelled",
            "source_coi",
            "source_currency",
            "source_amount",
        }:
            values = {
                "source_cancelled": {"estado_gasto": "cancelado"},
                "source_coi": {"coi_estado": "contabilizado"},
                "source_currency": {"currency": "USD"},
                "source_amount": {"gasto_cantidad": 117},
            }[bad]
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.imports[0].id)
                .values(**values)
            )
        elif bad in {"report_changed", "target_reversal"}:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(
                    **(
                        {"concepto": "Cambiado después corte"}
                        if bad == "report_changed"
                        else {"coi_estado": "reversar"}
                    )
                )
            )
        elif bad == "card_inactive":
            await session.execute(
                update(models.AmexCardAccount)
                .where(models.AmexCardAccount.id == data.card.id)
                .values(active=False)
            )
        else:
            await session.execute(
                update(models.CuentaContable)
                .where(models.CuentaContable.id == data.liability.id)
                .values(activo=False)
            )
        bound = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=targets,
            actor_id=pg.actor,
        )
        assert bound.status == "pending", bound.reason
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AmexReportBankAssociation)
            )
            == 0
        )
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_explicit_bridge_shared_cfdi_reuses_single_application(pg):
    data = await setup_report(pg)
    result = await confirm(pg, payload(data, await prepare(pg, data)))
    assert result.status == "created", result.reason
    async with pg.factory.begin() as session:
        source = await session.get(models.ExpenseReport, data.reports[0].id)
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id == data.imports[0].id)
            .values(cfdi_report_id=source.cfdi_report_id)
        )
        targets = list(
            (await session.scalars(select(models.AmexRecognitionConsumption.id))).all()
        )
        bound = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=targets,
            actor_id=pg.actor,
        )
        assert bound.status == "bound", bound.reason
    async with pg.factory() as session:
        reports = await recognition.load_expenses(session, [data.reports[0].id])
        lines, net, reason = await recognition.fiscal_lines(session, reports)
        assert reason is None and net == 116 and lines
    assert await _journal_count(pg) == 1


@pytest.mark.asyncio
async def test_preview_never_flushes_pending_work(pg):
    from uuid import uuid4

    data = await setup_report(pg)
    async with pg.factory() as session:
        actor = await session.get(models.Empleado, pg.actor)
        pending = models.ExpenseReport(
            id=uuid4(), proyecto="Sin guardar", concepto="Pendiente", gasto_cantidad=1
        )
        session.add(pending)
        preview = await cuts.preview_amex_approved_informe(
            session, informe_id=data.informe.id, actor=actor
        )
        assert preview.status == "ready" and pending in session.new
        await session.rollback()
    async with pg.factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(models.ExpenseReport))
            == 2
        )


@pytest.mark.asyncio
async def test_changed_operational_source_needs_actual_current_attestation(pg):
    from uuid import uuid4
    from devnous.gastos.services.amex_report_evidence_service import (
        record_amex_final_report_approval,
    )

    data = await setup_report(pg)
    async with pg.factory.begin() as session:
        informe = await session.get(models.Documento, data.informe.id)
        approval = models.Aprobacion(
            id=uuid4(),
            tipo_entidad="documento",
            entidad_id=informe.id,
            aprobador_id=pg.actor,
            accion="aprobar",
            fecha=informe.aprobado_en,
        )
        session.add(approval)
        await session.flush()
        evidence = await record_amex_final_report_approval(
            session, informe=informe, approval=approval
        )
        old_source = evidence.source_key
        await session.execute(
            update(models.ExpenseReport)
            .where(models.ExpenseReport.id == data.reports[0].id)
            .values(concepto="Cambio material posterior a aprobación")
        )
    preview = await prepare(pg, data)
    assert preview.exceptions["source"] == "stale_amex_operational_source"
    values = payload(data, preview)
    values["reason"] = ""
    assert (
        await confirm(pg, values)
    ).reason == "amex_current_attestation_requires_reason"
    values["reason"] = "Finanzas atesta fuente actual cambiada"
    result = await confirm(pg, values)
    assert result.status == "created", result.reason
    async with pg.factory() as session:
        unchanged = await session.get(models.AmexReportSourceEvidence, evidence.id)
        assert unchanged.source_key == old_source
        financial = (
            await session.scalars(
                select(models.AmexReportSourceEvidence).where(
                    models.AmexReportSourceEvidence.kind == "finance_confirmation"
                )
            )
        ).one()
        assert (
            financial.source_key != old_source
            and financial.operational_evidence_id == unchanged.id
        )


@pytest.mark.asyncio
async def test_statement_only_requires_explicit_financial_exception_and_records_it_once(
    pg,
):
    data = await setup_report(pg)
    async with pg.factory.begin() as session:
        denied = await recognition.bind_amex_consumption(
            session,
            imported_expense_id=data.imports[0].id,
            report_expense_ids=[],
            actor_id=pg.actor,
        )
        assert denied.reason == "amex_statement_only_requires_exception"
        result = await recognition.bind_amex_consumption(
            session,
            imported_expense_id=data.imports[0].id,
            report_expense_ids=[],
            actor_id=pg.actor,
            statement_only_reason="Cargo ajeno a informe, confirmado",
        )
        assert result.status == "bound", result.reason
    async with pg.factory.begin() as session:
        repeat = await recognition.bind_amex_consumption(
            session,
            imported_expense_id=data.imports[0].id,
            report_expense_ids=[],
            actor_id=pg.actor,
            statement_only_reason="Cargo ajeno a informe, confirmado",
        )
        assert repeat.status == "bound", repeat.reason
        audits = list(
            (
                await session.scalars(
                    select(models.Aprobacion).where(
                        models.Aprobacion.entidad_id == data.imports[0].id
                    )
                )
            ).all()
        )
        assert len(audits) == 1 and "Cargo ajeno a informe" in audits[0].comentario
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("unauthorized", ["inactive", "owner"])
async def test_source_preview_confirmation_and_bank_binding_revalidate_finance(
    pg, unauthorized
):
    data = await setup_report(pg)
    values = payload(data, await prepare(pg, data))
    async with pg.factory.begin() as session:
        await session.execute(
            update(models.Empleado)
            .where(models.Empleado.id == pg.actor)
            .values(
                **(
                    {"activo": False}
                    if unauthorized == "inactive"
                    else {"rol": "empleado"}
                )
            )
        )
        actor = await session.get(models.Empleado, pg.actor)
        preview = await cuts.preview_amex_approved_informe(
            session, informe_id=data.informe.id, actor=actor
        )
        confirmed = await cuts.confirm_amex_approved_informe_cut(
            session, actor=actor, **values
        )
        bridge = await recognition.bind_amex_report_bank_consumptions(
            session,
            imported_expense_id=data.imports[0].id,
            report_consumption_ids=[],
            actor_id=pg.actor,
        )
        assert (
            preview.reason
            == confirmed.reason
            == bridge.reason
            == "unauthorized_amex_accounting_actor"
        )
        assert not preview.expenses and preview.informe is None
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["unapproved", "empty", "mixed"])
async def test_ineligible_report_context_is_pending_with_known_report_visible(
    pg, context
):
    from sqlalchemy import delete

    data = await setup_report(pg)
    async with pg.factory.begin() as session:
        if context == "unapproved":
            await session.execute(
                update(models.Documento)
                .where(models.Documento.id == data.informe.id)
                .values(estado="enviado")
            )
        elif context == "empty":
            await session.execute(
                delete(models.ExpenseReport).where(
                    models.ExpenseReport.id == data.reports[0].id
                )
            )
        else:
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(pagado_con_amex_empresa=False)
            )
    preview = await prepare(pg, data)
    assert preview.status == "pending"
    if context != "unapproved":
        assert preview.informe.id == data.informe.id
    assert await _journal_count(pg) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["treatment", "reason", "source", "account"])
async def test_legacy_review_cannot_clear_new_financial_provenance(pg, change):
    from uuid import uuid4

    data = await setup_report(pg)
    values = payload(data, await prepare(pg, data))
    if change == "account":
        values["decisions"] = [
            dict(
                expense_ids=[str(data.reports[0].id)],
                treatment="partner_receivable",
                debtor_account_id=str(data.partner.id),
                reason="Cargo confirmado",
            )
        ]
    result = await confirm(pg, values)
    assert result.status == "created", result.reason
    async with pg.factory.begin() as session:
        actor = await session.get(models.Empleado, pg.actor)
        before = await session.get(models.AmexAccountingReview, data.reports[0].id)
        observed = (
            before.actor_id,
            before.reviewed_at,
            before.version,
            before.source_evidence_id,
        )
        args = dict(
            informe_id=data.informe.id,
            expense_id=data.reports[0].id,
            treatment=before.treatment,
            debtor_account_id=before.debtor_account_id,
            actor=actor,
            reason=before.reason,
        )
        unchanged = await cuts.review_amex_partida(session, **args)
        assert unchanged.status == "reviewed" and unchanged.review is before
        if change == "treatment":
            args.update(
                treatment="partner_receivable",
                debtor_account_id=data.partner.id,
                reason="Cargo nuevo",
            )
        elif change == "reason":
            args["reason"] = "Motivo cambiado"
        elif change == "source":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id == data.reports[0].id)
                .values(concepto="Fuente cambiada")
            )
        else:
            another = models.CuentaContable(
                id=uuid4(),
                codigo="1170-002-006",
                nombre="Otro socio",
                tipo="deudor",
                activo=True,
            )
            session.add(another)
            await session.flush()
            args["debtor_account_id"] = another.id
        changed = await cuts.review_amex_partida(session, **args)
        assert changed.reason == "amex_review_update_requires_confirmation"
        assert (
            before.actor_id,
            before.reviewed_at,
            before.version,
            before.source_evidence_id,
        ) == observed
