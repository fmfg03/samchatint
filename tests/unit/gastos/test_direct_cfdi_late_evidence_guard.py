"""SAT's permissive caller flag cannot promote a later evidence reservation."""

from uuid import uuid4

import pytest

from test_late_solicitud_evidence import case, regular, rows, xml_attachment
from devnous.gastos.models import (
    Aprobacion,
    AccountingPoliza,
    CFDIReport,
    Documento,
    ExpenseReport,
)
from devnous.gastos.services import cfdi_expense_link_service as links
from devnous.gastos.services.cfdi_ingestion_service import (
    CFDIDuplicateLinkError,
    ingest_cfdi_from_upload,
)
from devnous.gastos.services.documento_service import (
    SolicitudValidationError,
    add_solicitud_documento_adjuntos,
)


@pytest.mark.parametrize("mode", ["sat", "direct_document", "direct_expense"])
@pytest.mark.parametrize(
    "scenario", ["owner", "unconfirmed", "spoof", "revoked", "shared", "over"]
)
async def test_direct_link_requires_persisted_confirmation_and_preserves_owner(
    case, mode, scenario
):
    owner = await regular(case)
    owner.monto_solicitado = 600
    owner.cfdi_compartido_confirmado = True
    await case.session.commit()
    fiscal_uuid = str(uuid4()).upper()
    xml = xml_attachment(uuid=fiscal_uuid)
    await add_solicitud_documento_adjuntos(
        case.session, documento=owner, attachments=[xml], actor_id=case.owner.id
    )
    report = None
    if mode != "sat":
        report = (
            await ingest_cfdi_from_upload(
                case.session, xml_bytes=xml.raw_bytes, source="sat", entity=None
            )
        ).cfdi_report
    if mode == "direct_document":
        target = (
            owner
            if scenario == "owner"
            else Documento(
                id=uuid4(),
                empleado_id=case.outsider.id,
                tipo="SOLICITUD",
                estado="pagado",
                numero_referencia="S-DIRECT-" + uuid4().hex,
                proveedor_cliente_id=case.provider.id,
                monto_solicitado=401 if scenario == "over" else 400,
                currency="MXN",
                cfdi_compartido_confirmado=scenario in {"shared", "over", "revoked"},
            )
        )
    else:
        target = ExpenseReport(
            id=uuid4(),
            proyecto="Test",
            concepto="Test",
            gasto_cantidad=421 if scenario == "over" else 420,
            propina_no_deducible=20,
            documento_id=owner.id if scenario == "owner" else None,
            cfdi_compartido_confirmado=scenario in {"shared", "over", "revoked"},
        )
    target.cfdi_uuid_manual = fiscal_uuid.lower()
    case.session.add(target)
    await case.session.commit()
    audits_before = len(await rows(case, Aprobacion))
    if scenario == "spoof":
        target.cfdi_compartido_confirmado = True  # pending flag must not be trusted
    if scenario == "revoked":
        target.cfdi_compartido_confirmado = False

    async def operation():
        if mode == "sat":
            return await ingest_cfdi_from_upload(
                case.session,
                xml_bytes=xml.raw_bytes,
                source="sat",
                entity=target,
                allow_shared=True,
            )
        if mode == "direct_document":
            return await links.link_documento_to_cfdi_if_manual_uuid_set(
                case.session, target
            )
        return await links.link_expense_to_cfdi_if_manual_uuid_set(
            case.session,
            target,
            require_unique=True,
            allow_shared=True,
            shared_reason="Existing reason",
            actor_id=case.owner.id,
        )

    if scenario == "shared":
        assert await operation()
        assert target.cfdi_report_id is not None
        assert target.cfdi_compartido_confirmado is True
    else:
        error = (
            SolicitudValidationError if scenario == "over" else CFDIDuplicateLinkError
        )
        with pytest.raises(error):
            await operation()
        assert target.cfdi_report_id is None
        # A caller can commit after catching rejection: no partial report/link.
    await case.session.commit()
    assert len(await rows(case, CFDIReport)) == (
        1 if mode != "sat" or scenario == "shared" else 0
    )
    assert len(await rows(case, Aprobacion)) == audits_before
    assert not await rows(case, AccountingPoliza)
    assert owner.cfdi_report_id is None


@pytest.mark.parametrize(
    "mode", ["sat_document", "sat_expense", "direct_document", "direct_expense"]
)
async def test_preexisting_canonical_link_can_be_enriched_without_reapproval(
    case, mode
):
    owner = await regular(case)
    fiscal_uuid = str(uuid4()).upper()
    xml = xml_attachment(uuid=fiscal_uuid)
    target = owner
    if mode.endswith("expense"):
        target = ExpenseReport(
            id=uuid4(),
            proyecto="Test",
            concepto="Test",
            gasto_cantidad=1000,
            documento_id=owner.id,
            cfdi_compartido_confirmado=False,
        )
        case.session.add(target)
        await case.session.commit()
    result = await ingest_cfdi_from_upload(
        case.session, xml_bytes=xml.raw_bytes, source="sat", entity=target
    )
    await case.session.commit()
    await add_solicitud_documento_adjuntos(
        case.session, documento=owner, attachments=[xml], actor_id=case.owner.id
    )
    audits_before = len(await rows(case, Aprobacion))
    if mode.startswith("sat"):
        await ingest_cfdi_from_upload(
            case.session,
            xml_bytes=xml.raw_bytes,
            source="sat",
            entity=target,
            allow_shared=True,
        )
    elif mode.endswith("expense"):
        assert await links.link_expense_to_cfdi_if_manual_uuid_set(
            case.session,
            target,
            require_unique=True,
            allow_shared=True,
            shared_reason="Do not reapprove",
            actor_id=case.owner.id,
        )
    else:
        assert await links.link_documento_to_cfdi_if_manual_uuid_set(
            case.session, target
        )
    await case.session.commit()
    assert target.cfdi_report_id == result.cfdi_report.id
    assert target.cfdi_compartido_confirmado is False
    if mode.endswith("expense"):
        assert owner.cfdi_report_id is None
    assert len(await rows(case, Aprobacion)) == audits_before
    assert not await rows(case, AccountingPoliza)


@pytest.mark.parametrize(
    "mode", ["sat_document", "sat_expense", "direct_document", "direct_expense"]
)
async def test_uuid_without_late_reservations_keeps_existing_link_behavior(case, mode):
    owner = await regular(case)
    xml = xml_attachment(uuid=str(uuid4()).upper())
    target = owner
    if mode.endswith("expense"):
        target = ExpenseReport(
            id=uuid4(), proyecto="Test", concepto="Test", gasto_cantidad=1000
        )
        case.session.add(target)
        await case.session.commit()
    if mode.startswith("sat"):
        result = await ingest_cfdi_from_upload(
            case.session,
            xml_bytes=xml.raw_bytes,
            source="sat",
            entity=target,
            allow_shared=True,
        )
        assert result.linked
    else:
        report = (
            await ingest_cfdi_from_upload(
                case.session, xml_bytes=xml.raw_bytes, source="sat", entity=None
            )
        ).cfdi_report
        target.cfdi_uuid_manual = report.cfdi_uuid.lower()
        if mode.endswith("expense"):
            assert await links.link_expense_to_cfdi_if_manual_uuid_set(
                case.session, target
            )
        else:
            assert await links.link_documento_to_cfdi_if_manual_uuid_set(
                case.session, target
            )
    await case.session.commit()
    assert target.cfdi_report_id is not None
    assert not await rows(case, Aprobacion)
    assert not await rows(case, AccountingPoliza)


async def test_pending_report_without_id_does_not_make_null_owner_link_preexisting(
    case,
):
    owner = await regular(case)
    fiscal_uuid = str(uuid4()).upper()
    xml = xml_attachment(uuid=fiscal_uuid)
    await add_solicitud_documento_adjuntos(
        case.session, documento=owner, attachments=[xml], actor_id=case.owner.id
    )
    audits_before = len(await rows(case, Aprobacion))
    pending = CFDIReport(cfdi_uuid=fiscal_uuid, total=1000)
    case.session.add(pending)
    owner.cfdi_uuid_manual = fiscal_uuid
    assert pending.id is None
    with pytest.raises(CFDIDuplicateLinkError):
        await links.link_documento_to_cfdi_if_manual_uuid_set(case.session, owner)
    assert owner.cfdi_report_id is None
    assert pending.id is None
    assert pending in case.session.new
    with case.session.no_autoflush:
        assert len(await rows(case, Aprobacion)) == audits_before
        assert not await rows(case, CFDIReport)
    await case.session.rollback()


@pytest.mark.parametrize("mode", ["sat", "direct_document", "direct_expense"])
async def test_evidence_owner_cannot_be_promoted_using_another_invoice_uuid(case, mode):
    owner = await regular(case)
    original = xml_attachment(uuid=str(uuid4()).upper())
    await add_solicitud_documento_adjuntos(
        case.session, documento=owner, attachments=[original], actor_id=case.owner.id
    )
    incoming_uuid = str(uuid4()).upper()
    incoming = xml_attachment(uuid=incoming_uuid)
    if mode != "sat":
        await ingest_cfdi_from_upload(
            case.session, xml_bytes=incoming.raw_bytes, source="sat", entity=None
        )
    target = owner
    if mode == "direct_expense":
        target = ExpenseReport(
            id=uuid4(),
            proyecto="Test",
            concepto="Test",
            gasto_cantidad=1000,
            documento_id=owner.id,
            cfdi_compartido_confirmado=True,
        )
        case.session.add(target)
    target.cfdi_uuid_manual = incoming_uuid
    await case.session.commit()
    audits_before = len(await rows(case, Aprobacion))
    with pytest.raises(CFDIDuplicateLinkError):
        if mode == "sat":
            await ingest_cfdi_from_upload(
                case.session,
                xml_bytes=incoming.raw_bytes,
                source="sat",
                entity=target,
                allow_shared=True,
            )
        elif mode == "direct_expense":
            await links.link_expense_to_cfdi_if_manual_uuid_set(case.session, target)
        else:
            await links.link_documento_to_cfdi_if_manual_uuid_set(case.session, target)
    await case.session.commit()
    assert target.cfdi_report_id is None
    assert owner.cfdi_report_id is None
    assert len(await rows(case, Aprobacion)) == audits_before
    assert len(await rows(case, CFDIReport)) == (0 if mode == "sat" else 1)


@pytest.mark.parametrize("kind", ["document", "expense"])
async def test_pending_report_gets_identifier_after_legitimate_guard_acceptance(
    case, kind
):
    owner = await regular(case)
    target = owner
    if kind == "expense":
        target = ExpenseReport(
            id=uuid4(), proyecto="Test", concepto="Test", gasto_cantidad=1000
        )
        case.session.add(target)
        await case.session.commit()
    pending = CFDIReport(cfdi_uuid=str(uuid4()).upper(), total=1000)
    case.session.add(pending)
    target.cfdi_uuid_manual = pending.cfdi_uuid
    assert pending.id is None
    if kind == "expense":
        assert await links.link_expense_to_cfdi_if_manual_uuid_set(case.session, target)
    else:
        assert await links.link_documento_to_cfdi_if_manual_uuid_set(
            case.session, target
        )
    assert pending.id is not None
    assert target.cfdi_report_id == pending.id
    await case.session.commit()
    assert not await rows(case, Aprobacion)
    assert not await rows(case, AccountingPoliza)
