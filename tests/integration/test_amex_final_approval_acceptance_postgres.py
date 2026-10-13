"""Final workflow owns the evidence transaction; legacy auxiliaries are isolated."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text, update
from test_amex_approved_informe_service_postgres import (
    confirm,
)
from test_amex_approved_informe_service_postgres import legacy_pg as _legacy_pg
from test_amex_approved_informe_service_postgres import (
    payload,
)
from test_amex_approved_informe_service_postgres import pg as _approved_pg
from test_amex_approved_informe_service_postgres import (
    prepare,
    setup_report,
)

from devnous.gastos import models
from devnous.gastos.services import amex_report_evidence_service as evidence
from devnous.gastos.services import authorization_profile_service as authorization
from devnous.gastos.services import documento_workflow_service as workflow

legacy_pg = _legacy_pg
pg = _approved_pg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["amex", "evidence_failure", "non_amex", "approval_edit_race"]
)
async def test_final_workflow_snapshot_success_or_atomic_failure(
    pg, monkeypatch, scenario
):
    data = await setup_report(pg)
    async with pg.factory.begin() as session:
        await session.execute(
            text("CREATE TABLE documento_authorization_routes (documento_id uuid)")
        )
        await session.execute(
            update(models.Documento)
            .where(models.Documento.id == data.informe.id)
            .values(estado="enviado", aprobado_en=None, referencia_operaciones=None)
        )
        if scenario == "non_amex":
            await session.execute(
                update(models.ExpenseReport)
                .where(models.ExpenseReport.id.in_([e.id for e in data.reports]))
                .values(pagado_con_amex_empresa=False)
            )

    # Existing settlement, authorization strategy and outbound adapters are not
    # under this acceptance fixture. The real document/event/hook/commit remains.
    monkeypatch.setattr(
        workflow, "documento_financial_terminal_reason", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        workflow, "validate_informe_surplus_before_submission", AsyncMock()
    )
    monkeypatch.setattr(
        authorization,
        "build_authorization_route_hard_block",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        authorization,
        "build_authorization_route_soft_warning",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        workflow,
        "ensure_debtor_comprobacion_posting_for_informe",
        AsyncMock(return_value=SimpleNamespace(status="skipped")),
    )
    audit = AsyncMock()
    monkeypatch.setattr(workflow, "record_customer_success_audit_event", audit)
    from devnous.gastos.services import documento_telegram

    monkeypatch.setattr(
        documento_telegram,
        "schedule_document_workflow_telegram_notifications",
        lambda **kwargs: None,
    )

    if scenario == "evidence_failure":
        real_capture = evidence.record_amex_final_report_approval

        async def fail_after_real_flush(session, **kwargs):
            result = await real_capture(session, **kwargs)
            assert result is not None
            raise RuntimeError("acceptance-injected-evidence-failure")

        monkeypatch.setattr(
            evidence, "record_amex_final_report_approval", fail_after_real_flush
        )

    if scenario == "approval_edit_race":
        capture_done, release = asyncio.Event(), asyncio.Event()
        waiting_pid = asyncio.Future()
        real_capture = evidence.record_amex_final_report_approval

        async def hold_after_capture(session, **kwargs):
            result = await real_capture(session, **kwargs)
            capture_done.set()
            await release.wait()
            return result

        monkeypatch.setattr(
            evidence, "record_amex_final_report_approval", hold_after_capture
        )

        async def approve():
            async with pg.factory() as session:
                return await workflow.transition_documento_workflow(
                    session,
                    documento_id=data.informe.id,
                    actor_id=pg.actor,
                    action="approve",
                )

        async def edit():
            async with pg.factory.begin() as session:
                waiting_pid.set_result(
                    await session.scalar(text("SELECT pg_backend_pid()"))
                )
                await session.execute(
                    update(models.ExpenseReport)
                    .where(models.ExpenseReport.id == data.reports[0].id)
                    .values(gasto_cantidad=115)
                )

        first = asyncio.create_task(approve())
        second = None
        try:
            await asyncio.wait_for(capture_done.wait(), 5)
            second = asyncio.create_task(edit())
            await observe_waiting_lock(pg, await asyncio.wait_for(waiting_pid, 5))
            assert not second.done()
        finally:
            release.set()
            await asyncio.wait_for(first, 5)
            if second:
                await asyncio.wait_for(second, 5)
        preview = await prepare(pg, data)
        assert preview.exceptions["source"] == "stale_amex_operational_source"
        values = payload(data, preview)
        values["reason"] = ""
        assert (await confirm(pg, values)).status == "pending"

    async with pg.factory() as session:
        if scenario == "evidence_failure":
            with pytest.raises(RuntimeError, match="acceptance-injected"):
                await workflow.transition_documento_workflow(
                    session,
                    documento_id=data.informe.id,
                    actor_id=pg.actor,
                    action="approve",
                )
        elif scenario != "approval_edit_race":
            result = await workflow.transition_documento_workflow(
                session,
                documento_id=data.informe.id,
                actor_id=pg.actor,
                action="approve",
            )
            assert result.aprobacion.accion == "aprobar"

    async with pg.factory() as session:
        doc = await session.get(models.Documento, data.informe.id)
        event_count = await session.scalar(
            select(func.count()).select_from(models.Aprobacion)
        )
        evidence_count = await session.scalar(
            select(func.count()).select_from(models.AmexReportSourceEvidence)
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AccountingPoliza)
            )
            == 0
        )
        if scenario == "evidence_failure":
            assert doc.estado == "enviado" and doc.aprobado_en is None
            assert event_count == evidence_count == 0
            audit.assert_not_awaited()
        else:
            assert doc.estado == "aprobado" and event_count == 1
            assert evidence_count == (0 if scenario == "non_amex" else 1)
            audit.assert_awaited_once()
            if scenario != "non_amex":
                captured = (
                    await session.scalars(select(models.AmexReportSourceEvidence))
                ).one()
                event = (await session.scalars(select(models.Aprobacion))).one()
                assert captured.approval_id == event.id
                assert captured.actor_id == event.aprobador_id == pg.actor
                assert captured.created_at == event.fecha == doc.aprobado_en
                if scenario == "approval_edit_race":
                    assert (
                        float(captured.snapshot_json["partidas"][0]["gasto_cantidad"])
                        == 116
                    )
                    current = await session.get(
                        models.ExpenseReport, data.reports[0].id
                    )
                    assert current.gasto_cantidad == 115


async def observe_waiting_lock(pg, pid):
    async def observe():
        async with pg.factory() as session:
            while not await session.scalar(
                text(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"
                ),
                {"pid": pid},
            ):
                await asyncio.sleep(0)

    await asyncio.wait_for(observe(), 5)


@pytest.mark.asyncio
async def test_late_binding_waits_for_finance_commit_reuses_receipt_and_export(pg):
    from copy import deepcopy

    from devnous.gastos.services import amex_accounting_cut_service as cuts
    from devnous.gastos.services import amex_recognition_service as recognition
    from devnous.gastos.services.amex_cut_export_service import cut_expense_cfdis
    from devnous.gastos.services.coi_poliza_exporter import generate_coi_poliza_csv

    data = await setup_report(pg)
    values = payload(data, await prepare(pg, data))
    created, release = asyncio.Event(), asyncio.Event()
    waiting_pid = asyncio.Future()
    receipt = {}

    async def financial():
        async with pg.factory.begin() as session:
            actor = await session.get(models.Empleado, pg.actor)
            result = await cuts.confirm_amex_approved_informe_cut(
                session, actor=actor, **values
            )
            assert result.status == "created", result.reason
            receipt["cut"] = result.cut.id
            receipt["snapshot"] = deepcopy(result.cut.snapshot_json)
            receipt["export"] = generate_coi_poliza_csv(cut_expense_cfdis(result.cut))
            receipt["target"] = (
                await session.scalars(select(models.AmexRecognitionConsumption.id))
            ).one()
            created.set()
            await release.wait()

    async def bank():
        async with pg.factory.begin() as session:
            waiting_pid.set_result(
                await session.scalar(text("SELECT pg_backend_pid()"))
            )
            return await recognition.bind_amex_report_bank_consumptions(
                session,
                imported_expense_id=data.imports[0].id,
                report_consumption_ids=[receipt["target"]],
                actor_id=pg.actor,
            )

    first = asyncio.create_task(financial())
    second = None
    try:
        await asyncio.wait_for(created.wait(), 5)
        second = asyncio.create_task(bank())
        await observe_waiting_lock(pg, await asyncio.wait_for(waiting_pid, 5))
        assert not second.done()
    finally:
        release.set()
        await asyncio.wait_for(first, 5)
    assert (await asyncio.wait_for(second, 5)).status == "bound"
    async with pg.factory() as session:
        cut = await session.get(models.AmexAccountingCut, receipt["cut"])
        assert cut.snapshot_json == receipt["snapshot"]
        assert generate_coi_poliza_csv(cut_expense_cfdis(cut)) == receipt["export"]
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AccountingPoliza)
            )
            == 1
        )
