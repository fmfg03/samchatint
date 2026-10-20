"""Manual UUID requests must honor the same late-evidence reservation."""

from uuid import uuid4

import pytest

from test_late_solicitud_evidence import case, regular, rows, xml_attachment
from devnous.gastos.models import Aprobacion, AccountingPoliza, Documento
from devnous.gastos.services import documento_service as service
from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_from_upload


@pytest.mark.parametrize("mode", ["create", "update"])
@pytest.mark.parametrize("scenario", ["unconfirmed", "shared", "over"])
async def test_manual_uuid_checks_late_reservation_before_assigning(
    case, mode, scenario
):
    owner = await regular(case)
    owner.monto_solicitado = 600
    owner.cfdi_compartido_confirmado = True
    await case.session.commit()
    fiscal_uuid = str(uuid4()).upper()
    xml = xml_attachment(uuid=fiscal_uuid)
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=owner, attachments=[xml], actor_id=case.owner.id
    )
    report = (
        await ingest_cfdi_from_upload(
            case.session, xml_bytes=xml.raw_bytes, source="sat", entity=None
        )
    ).cfdi_report
    await case.session.commit()
    target = None
    if mode == "update":
        target = Documento(
            id=uuid4(),
            numero_referencia="S-MANUAL-" + uuid4().hex,
            empleado_id=case.outsider.id,
            tipo="SOLICITUD",
            estado="borrador",
            proveedor_cliente_id=case.provider.id,
            monto_solicitado=400,
            proyecto_otro="Proyecto",
            currency="MXN",
        )
        case.session.add(target)
        await case.session.commit()
    count_before = len(await rows(case, Documento))
    audit_before = len(await rows(case, Aprobacion))
    payload = service.build_solicitud_terceros_payload(
        empleado_id=case.outsider.id,
        monto_solicitado="401" if scenario == "over" else "400",
        proveedor_cliente_id=str(case.provider.id),
        torneo_id="__otro__",
        proyecto_otro="Proyecto",
        concepto_pago="Servicio",
        currency="MXN",
        cfdi_uuid_manual=fiscal_uuid,
        cfdi_compartido_confirmado=scenario != "unconfirmed",
    )

    async def operation():
        if mode == "create":
            return await service.create_solicitud_terceros_document(
                case.session, payload
            )
        return await service.update_solicitud_terceros_document(
            case.session, documento=target, payload=payload
        )

    if scenario == "shared":
        result = await operation()
        await case.session.commit()
        assert result.cfdi_report_id == report.id
        assert result.cfdi_compartido_confirmado is True
    else:
        with pytest.raises(service.SolicitudValidationError) as error:
            await operation()
        assert error.value.code == (
            "duplicate_cfdi"
            if scenario == "unconfirmed"
            else "cfdi_amount_exceeds_remaining"
        )
        await case.session.commit()  # caught failure must not partially link
        assert len(await rows(case, Documento)) == count_before
        if target is not None:
            assert target.cfdi_report_id is None and target.monto_solicitado == 400
    assert owner.cfdi_report_id is None
    assert len(await rows(case, Aprobacion)) == audit_before
    assert not await rows(case, AccountingPoliza)


@pytest.mark.parametrize("same_uuid", [True, False])
async def test_editable_evidence_owner_is_not_promoted_by_manual_uuid(case, same_uuid):
    owner = await regular(case)
    invoice = xml_attachment(uuid=str(uuid4()).upper())
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=owner, attachments=[invoice], actor_id=case.owner.id
    )
    incoming = invoice if same_uuid else xml_attachment(uuid=str(uuid4()).upper())
    report = (
        await ingest_cfdi_from_upload(
            case.session, xml_bytes=incoming.raw_bytes, source="sat", entity=None
        )
    ).cfdi_report
    owner.estado = "rechazado"
    await case.session.commit()
    payload = service.build_solicitud_terceros_payload(
        empleado_id=case.owner.id,
        monto_solicitado="1000",
        proveedor_cliente_id=str(case.provider.id),
        torneo_id="__otro__",
        proyecto_otro="Proyecto",
        concepto_pago="Servicio",
        currency="MXN",
        cfdi_uuid_manual=report.cfdi_uuid,
        cfdi_compartido_confirmado=True,
    )
    with pytest.raises(service.SolicitudValidationError) as error:
        await service.update_solicitud_terceros_document(
            case.session, documento=owner, payload=payload
        )
    assert error.value.code == "duplicate_cfdi"
    await case.session.commit()
    assert owner.cfdi_report_id is None and owner.estado == "rechazado"
    assert (
        len([a for a in await rows(case, Aprobacion) if a.accion == "adjuntar_factura"])
        == 1
    )
    assert not await rows(case, AccountingPoliza)
