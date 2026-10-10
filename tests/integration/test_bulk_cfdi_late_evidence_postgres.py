"""Bulk SAT matching must not promote late evidence or overallocate its UUID."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from test_late_solicitud_evidence_postgres import invoice, locked_document, pg
from devnous.gastos import models
from devnous.gastos.services import cfdi_expense_link_service as links
from devnous.gastos.services.cfdi_upload_resolver import resolve_cfdi_upload
from devnous.gastos.services.documento_service import add_solicitud_documento_adjuntos
from devnous.gastos.services.documento_service import SolicitudValidationError
from devnous.gastos.services.cfdi_ingestion_service import (
    CFDIDuplicateLinkError,
    ingest_cfdi_from_upload,
)
from devnous.gastos.services.cfdi_expense_link_service import (
    bulk_link_pending_documentos_to_cfdi_reports,
    bulk_link_pending_expenses_to_cfdi_reports,
)


@pytest.mark.parametrize("kind", ["documento", "expense"])
@pytest.mark.parametrize(
    "case", ["legacy", "own", "own_other_uuid", "foreign", "shared", "over"]
)
async def test_bulk_late_evidence_owner_balance_and_retry(pg, kind, case):
    fiscal_uuid, report_id, expense_id = str(uuid4()).upper(), uuid4(), uuid4()
    async with pg.factory.begin() as session:
        first = await session.get(models.Documento, pg.first)
        second = await session.get(models.Documento, pg.second)
        first.monto_solicitado = 600
        second.monto_solicitado = 401 if case == "over" else 400
        session.add(models.CFDIReport(id=report_id, cfdi_uuid=fiscal_uuid, total=1000))
        if case != "legacy":
            first.cfdi_uuid_manual = fiscal_uuid
            session.add(
                models.Aprobacion(
                    tipo_entidad="documento",
                    entidad_id=first.id,
                    aprobador_id=pg.actor,
                    accion="adjuntar_factura",
                    comentario=(
                        str(uuid4()).upper()
                        if case == "own_other_uuid"
                        else fiscal_uuid
                    ),
                )
            )
        if kind == "documento":
            target = first if case in {"own", "own_other_uuid"} else second
            target.cfdi_uuid_manual = " " + fiscal_uuid.lower() + " "
            target.cfdi_compartido_confirmado = case in {
                "own",
                "own_other_uuid",
                "shared",
                "over",
            }
        else:
            session.add(
                models.ExpenseReport(
                    id=expense_id,
                    proyecto="Test",
                    concepto="Test",
                    gasto_cantidad=421 if case == "over" else 420,
                    propina_no_deducible=20,
                    cfdi_uuid_manual=" " + fiscal_uuid.lower() + " ",
                    documento_id=(
                        first.id if case in {"own", "own_other_uuid"} else None
                    ),
                    cfdi_compartido_confirmado=case
                    in {"own", "own_other_uuid", "shared", "over"},
                )
            )
    method = (
        bulk_link_pending_documentos_to_cfdi_reports
        if kind == "documento"
        else bulk_link_pending_expenses_to_cfdi_reports
    )
    expected = int(case in {"legacy", "shared"})
    async with pg.factory.begin() as session:
        assert await method(session) == expected
        assert await method(session) == 0
    async with pg.factory() as session:
        target = await session.get(
            models.Documento,
            pg.first if case in {"own", "own_other_uuid"} else pg.second,
        )
        if kind == "expense":
            target = await session.get(models.ExpenseReport, expense_id)
        assert target.cfdi_report_id == (report_id if expected else None)
        first = await session.get(models.Documento, pg.first)
        assert first.cfdi_report_id is None
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AccountingPoliza)
            )
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(models.CFDIReport))
            == 1
        )
        assert await session.scalar(
            select(func.count()).select_from(models.Aprobacion)
        ) == (0 if case == "legacy" else 1)


@pytest.mark.parametrize("kind", ["documento", "expense"])
async def test_bulk_two_confirmed_candidates_cannot_consume_same_remaining_balance(
    pg, kind
):
    fiscal_uuid, report_id, extra_id = str(uuid4()).upper(), uuid4(), uuid4()
    async with pg.factory.begin() as session:
        first = await session.get(models.Documento, pg.first)
        first.monto_solicitado = 600
        session.add(models.CFDIReport(id=report_id, cfdi_uuid=fiscal_uuid, total=1000))
        session.add(
            models.Aprobacion(
                tipo_entidad="documento",
                entidad_id=pg.first,
                aprobador_id=pg.actor,
                accion="adjuntar_factura",
                comentario=fiscal_uuid,
            )
        )
        if kind == "documento":
            second = await session.get(models.Documento, pg.second)
            second.cfdi_uuid_manual = fiscal_uuid
            second.cfdi_compartido_confirmado = True
            second.monto_solicitado = 400
            session.add(
                models.Documento(
                    id=extra_id,
                    empleado_id=pg.actor,
                    numero_referencia="S-BULK-" + extra_id.hex,
                    tipo="SOLICITUD",
                    estado="pagado",
                    monto_solicitado=400,
                    cfdi_uuid_manual=fiscal_uuid,
                    cfdi_compartido_confirmado=True,
                )
            )
        else:
            for identifier in (extra_id, uuid4()):
                session.add(
                    models.ExpenseReport(
                        id=identifier,
                        proyecto="Test",
                        concepto="Test",
                        gasto_cantidad=400,
                        cfdi_uuid_manual=fiscal_uuid,
                        cfdi_compartido_confirmado=True,
                    )
                )
    method = (
        bulk_link_pending_documentos_to_cfdi_reports
        if kind == "documento"
        else bulk_link_pending_expenses_to_cfdi_reports
    )
    model = models.Documento if kind == "documento" else models.ExpenseReport
    async with pg.factory.begin() as session:
        assert await method(session) == 1
        assert await method(session) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.cfdi_report_id == report_id)
            )
            == 1
        )


@pytest.mark.parametrize("kind", ["documento", "expense"])
async def test_bulk_failure_rolls_back_prior_link_even_if_caller_commits(
    pg, monkeypatch, kind
):
    fiscal_uuid, report_id = str(uuid4()).upper(), uuid4()
    async with pg.factory.begin() as session:
        session.add(models.CFDIReport(id=report_id, cfdi_uuid=fiscal_uuid, total=1000))
        if kind == "documento":
            for identifier in (pg.first, pg.second):
                document = await session.get(models.Documento, identifier)
                document.cfdi_uuid_manual = fiscal_uuid
        else:
            for _ in range(2):
                session.add(
                    models.ExpenseReport(
                        proyecto="Test",
                        concepto="Test",
                        gasto_cantidad=400,
                        cfdi_uuid_manual=fiscal_uuid,
                    )
                )
    original = links.find_cfdi_report_by_fiscal_uuid
    calls = 0

    async def fail_second(session, uuid):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Injected second-candidate failure")
        return await original(session, uuid)

    monkeypatch.setattr(links, "find_cfdi_report_by_fiscal_uuid", fail_second)
    method = (
        bulk_link_pending_documentos_to_cfdi_reports
        if kind == "documento"
        else bulk_link_pending_expenses_to_cfdi_reports
    )
    async with pg.factory.begin() as session:
        with pytest.raises(RuntimeError, match="second-candidate"):
            await method(session)
        assert calls == 2
        # Deliberately let the caller's enclosing transaction commit.
    model = models.Documento if kind == "documento" else models.ExpenseReport
    async with pg.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.cfdi_report_id == report_id)
            )
            == 0
        )


@pytest.mark.parametrize("kind", ["documento", "expense"])
async def test_bulk_waits_for_real_late_evidence_uuid_lock_and_rechecks(pg, kind):
    attachment = invoice()
    resolved, error = resolve_cfdi_upload(xml_bytes=attachment.raw_bytes)
    assert not error
    fiscal_uuid = resolved.parsed["cfdi_uuid"]
    async with pg.factory.begin() as session:
        session.add(
            models.CFDIReport(
                cfdi_uuid=fiscal_uuid,
                total=1000,
                subtotal=1000,
                emisor_rfc="AAA010101AAA",
                receptor_rfc="BBB010101BBB",
                moneda="MXN",
            )
        )
        if kind == "documento":
            second = await session.get(models.Documento, pg.second)
            second.cfdi_uuid_manual = fiscal_uuid
        else:
            session.add(
                models.ExpenseReport(
                    proyecto="Test",
                    concepto="Test",
                    gasto_cantidad=1000,
                    cfdi_uuid_manual=fiscal_uuid,
                )
            )
    method = (
        bulk_link_pending_documentos_to_cfdi_reports
        if kind == "documento"
        else bulk_link_pending_expenses_to_cfdi_reports
    )
    started, worker = asyncio.Event(), {}

    async def bulk():
        async with pg.factory.begin() as session:
            worker["pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            return await method(session)

    async with pg.factory() as session:
        document = await locked_document(session, pg.first)
        await add_solicitud_documento_adjuntos(
            session,
            documento=document,
            attachments=[attachment],
            actor_id=pg.actor,
            commit=False,
        )
        pending = asyncio.create_task(bulk())
        try:
            await asyncio.wait_for(started.wait(), 5)

            async def contention():
                async with pg.factory() as observer:
                    while not await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid "
                            "AND locktype = 'advisory' AND NOT granted)"
                        ),
                        worker,
                    ):
                        if pending.done():
                            pytest.fail(
                                "Bulk completed without contending on evidence UUID"
                            )
                        await asyncio.sleep(0.02)

            await asyncio.wait_for(contention(), 10)
            await session.commit()
            assert await asyncio.wait_for(pending, 10) == 0
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("mode", ["sat", "direct_document", "direct_expense"])
@pytest.mark.parametrize(
    "scenario",
    [
        "owner",
        "owner_other_uuid",
        "unconfirmed",
        "spoof",
        "revoked",
        "shared",
        "over",
        "prelinked",
    ],
)
async def test_direct_sat_and_linkers_respect_persisted_late_evidence_contract(
    pg, mode, scenario
):
    attachment = invoice()
    resolved, error = resolve_cfdi_upload(xml_bytes=attachment.raw_bytes)
    assert not error
    fiscal_uuid = resolved.parsed["cfdi_uuid"]
    report_id, expense_id = uuid4(), uuid4()
    seed_report = mode != "sat" or scenario == "prelinked"
    async with pg.factory.begin() as session:
        first = await session.get(models.Documento, pg.first)
        first.monto_solicitado = 600
        session.add(
            models.Aprobacion(
                tipo_entidad="documento",
                entidad_id=pg.first,
                aprobador_id=pg.actor,
                accion="adjuntar_factura",
                comentario=(
                    str(uuid4()).upper()
                    if scenario == "owner_other_uuid"
                    else fiscal_uuid
                ),
            )
        )
        if seed_report:
            session.add(
                models.CFDIReport(
                    id=report_id,
                    cfdi_uuid=fiscal_uuid,
                    total=1000,
                    subtotal=1000,
                    emisor_rfc="AAA010101AAA",
                    receptor_rfc="BBB010101BBB",
                    moneda="MXN",
                )
            )
        if mode == "direct_document":
            target = (
                first
                if scenario in {"owner", "owner_other_uuid"}
                else await session.get(models.Documento, pg.second)
            )
            target_id = target.id
            target.monto_solicitado = 401 if scenario == "over" else 400
            target.cfdi_uuid_manual = fiscal_uuid.lower()
            target.cfdi_compartido_confirmado = scenario in {
                "owner",
                "shared",
                "over",
                "revoked",
            }
            if scenario == "prelinked":
                target.cfdi_report_id = report_id
        else:
            target_id = expense_id
            session.add(
                models.ExpenseReport(
                    id=expense_id,
                    proyecto="Test",
                    concepto="Test",
                    gasto_cantidad=421 if scenario == "over" else 420,
                    propina_no_deducible=20,
                    documento_id=(
                        pg.first
                        if scenario in {"owner", "owner_other_uuid", "prelinked"}
                        else None
                    ),
                    cfdi_uuid_manual=fiscal_uuid.lower(),
                    cfdi_compartido_confirmado=scenario
                    in {"owner", "shared", "over", "revoked"},
                    cfdi_report_id=report_id if scenario == "prelinked" else None,
                )
            )
    model = models.Documento if mode == "direct_document" else models.ExpenseReport
    async with pg.factory.begin() as session:
        target = await session.get(model, target_id)
        if scenario == "spoof":
            target.cfdi_compartido_confirmado = True
        if scenario == "revoked":
            target.cfdi_compartido_confirmado = False

        async def operation():
            if mode == "sat":
                return await ingest_cfdi_from_upload(
                    session,
                    xml_bytes=attachment.raw_bytes,
                    source="sat",
                    entity=target,
                    allow_shared=True,
                )
            if mode == "direct_document":
                return await links.link_documento_to_cfdi_if_manual_uuid_set(
                    session, target
                )
            return await links.link_expense_to_cfdi_if_manual_uuid_set(
                session,
                target,
                require_unique=True,
                allow_shared=True,
                shared_reason="Do not reapprove",
                actor_id=pg.actor,
            )

        if scenario in {"shared", "prelinked"}:
            await operation()
            assert target.cfdi_report_id is not None
        else:
            error_type = (
                SolicitudValidationError
                if scenario == "over"
                else CFDIDuplicateLinkError
            )
            with pytest.raises(error_type):
                await operation()
            assert target.cfdi_report_id is None
        # Commit after caught rejection explicitly verifies no partial report.
    async with pg.factory() as session:
        target = await session.get(model, target_id)
        assert (target.cfdi_report_id is not None) == (
            scenario in {"shared", "prelinked"}
        )
        assert await session.scalar(
            select(func.count()).select_from(models.CFDIReport)
        ) == (1 if seed_report or scenario == "shared" else 0)
        assert (
            await session.scalar(select(func.count()).select_from(models.Aprobacion))
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AccountingPoliza)
            )
            == 0
        )
        if scenario == "prelinked":
            assert target.cfdi_report_id == report_id
            assert target.cfdi_compartido_confirmado is False


async def test_pending_report_null_id_is_not_a_persisted_owner_link(pg):
    fiscal_uuid = str(uuid4()).upper()
    async with pg.factory.begin() as session:
        session.add(
            models.Aprobacion(
                tipo_entidad="documento",
                entidad_id=pg.first,
                aprobador_id=pg.actor,
                accion="adjuntar_factura",
                comentario=fiscal_uuid,
            )
        )
    async with pg.factory() as session:
        owner = await session.get(models.Documento, pg.first)
        owner.cfdi_uuid_manual = fiscal_uuid
        pending = models.CFDIReport(cfdi_uuid=fiscal_uuid, total=1000)
        session.add(pending)
        assert pending.id is None
        with pytest.raises(CFDIDuplicateLinkError):
            await links.link_documento_to_cfdi_if_manual_uuid_set(session, owner)
        assert owner.cfdi_report_id is None
        assert pending.id is None
        with session.no_autoflush:
            assert (
                await session.scalar(
                    select(func.count()).select_from(models.CFDIReport)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(models.Aprobacion)
                )
                == 1
            )
        await session.rollback()
