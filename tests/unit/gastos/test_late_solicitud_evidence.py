"""Late invoice evidence is authorized, append-only and has no financial effects."""

from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from test_supplier_advances import case, xml_attachment  # noqa: F401
from devnous.gastos.models import (
    AccountingPoliza,
    Adjunto,
    Aprobacion,
    CFDIReport,
    Documento,
    ExpenseReport,
)
from devnous.gastos.routes import user_routes as routes
from devnous.gastos.services import documento_service as service
from devnous.gastos.services.cfdi_ingestion_service import (
    CFDIDuplicateLinkError,
    ingest_cfdi_from_upload,
)
from devnous.gastos.utils.receipt_bytes import _label_for_documento_meta

STATES = [
    "borrador",
    "control_presupuestal",
    "enviado",
    "aprobado",
    "en_proceso_pago",
    "pagado",
    "cerrado",
    "reembolsado",
    "aplicado",
    "liquidado",
]


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize(
    "role", ["empleado", "finanzas", "admin", "superadmin", "coordinador"]
)
def test_append_permission_preserves_document_identity(state, role):
    owner = uuid4()
    doc = SimpleNamespace(
        tipo="SOLICITUD", proveedor_cliente_id=uuid4(), empleado_id=owner, estado=state
    )
    actor = SimpleNamespace(id=owner, rol=role)
    assert routes._can_add_solicitud_adjuntos(doc, actor)
    actor.id = uuid4()
    assert routes._can_add_solicitud_adjuntos(doc, actor) == (
        role in {"finanzas", "admin", "superadmin"}
    )
    assert not routes._can_add_solicitud_adjuntos(doc, actor, solicitud_cancelada=True)
    doc.estado = "rechazado"
    assert not routes._can_add_solicitud_adjuntos(doc, actor)
    doc.estado = "cancelado"
    assert not routes._can_add_solicitud_adjuntos(doc, actor)


@pytest.mark.parametrize("state", STATES[2:])
def test_advanced_append_never_grants_edit_or_delete(state):
    actor = SimpleNamespace(id=uuid4(), rol="empleado")
    doc = SimpleNamespace(
        tipo="SOLICITUD",
        proveedor_cliente_id=uuid4(),
        empleado_id=actor.id,
        estado=state,
    )
    assert not routes._can_edit_solicitud_terceros(doc, actor)
    for category in ["supporting", "cfdi_xml", "cfdi_pdf", "cfdi_xml_evidence"]:
        assert not routes._can_remove_solicitud_adjunto(doc, actor, category)


async def regular(case, state="pagado"):
    doc = case.advance
    doc.is_supplier_advance = False
    doc.estado = state
    doc.pagado_en = datetime(2026, 10, 8) if state == "pagado" else None
    await case.session.commit()
    return doc


def upload(attachment):
    return SimpleNamespace(
        filename=attachment.filename,
        content_type=attachment.mime_type,
        read=AsyncMock(return_value=attachment.raw_bytes),
    )


async def rows(case, model):
    return (await case.session.execute(select(model))).scalars().all()


async def test_paid_invoice_is_evidence_only_and_retry_preserves_files(case):
    doc = await regular(case)
    invoice = xml_attachment()
    support = service.SolicitudTercerosAttachment(
        b"materialidad", "entrega.txt", "text/plain", "supporting"
    )
    before = (
        doc.estado,
        doc.pagado_en,
        doc.monto_solicitado,
        doc.proveedor_cliente_id,
        doc.cfdi_report_id,
        doc.gasto_generado_id,
    )
    response = await routes.agregar_documento_adjuntos(
        doc.id, case.session, case.owner, "cfdi_xml", [upload(invoice)]
    )
    assert response.status_code == 303
    assert "success_msg" in response.headers["location"]
    first = (await rows(case, Adjunto))[0]
    assert first.categoria == "cfdi_xml_evidence"
    assert (
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[invoice], actor_id=case.owner.id
        )
        == 0
    )
    assert (
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[support, support],
            actor_id=case.owner.id,
        )
        == 1
    )
    assert len(await rows(case, Adjunto)) == 2
    assert (await rows(case, Adjunto))[0].ruta_archivo == first.ruta_archivo
    assert before == (
        doc.estado,
        doc.pagado_en,
        doc.monto_solicitado,
        doc.proveedor_cliente_id,
        doc.cfdi_report_id,
        doc.gasto_generado_id,
    )
    assert not await rows(case, CFDIReport)
    assert not await rows(case, ExpenseReport)
    assert not await rows(case, AccountingPoliza)
    audits = await rows(case, Aprobacion)
    assert len([a for a in audits if a.accion == "adjuntar_factura"]) == 1
    assert all(a.aprobador_id == case.owner.id for a in audits)


@pytest.mark.parametrize(
    "state", ["control_presupuestal", "enviado", "en_proceso_pago", "cerrado"]
)
async def test_legitimate_route_upload_in_each_missing_and_closed_state(case, state):
    doc = await regular(case, state)
    response = await routes.agregar_documento_adjuntos(
        doc.id,
        case.session,
        case.owner,
        "supporting",
        [
            upload(
                service.SolicitudTercerosAttachment(
                    b"ok", "soporte.txt", "text/plain", "supporting"
                )
            )
        ],
    )
    assert "success_msg" in response.headers["location"]
    assert len(await rows(case, Adjunto)) == 1
    assert doc.estado == state


@pytest.mark.parametrize("denial", ["outsider", "rejected", "cancelled", "bank-proof"])
async def test_denied_upload_has_no_effect(case, denial):
    doc = await regular(case)
    actor = case.outsider if denial == "outsider" else case.owner
    category = "comprobante_pago" if denial == "bank-proof" else "supporting"
    if denial == "rejected":
        doc.estado = "rechazado"
    if denial == "cancelled":
        case.session.add(
            Aprobacion(
                tipo_entidad="documento",
                entidad_id=doc.id,
                aprobador_id=case.owner.id,
                accion="cancelar",
            )
        )
    await case.session.commit()
    file = upload(
        service.SolicitudTercerosAttachment(
            b"ok", "soporte.txt", "text/plain", "supporting"
        )
    )
    response = await routes.agregar_documento_adjuntos(
        doc.id, case.session, actor, category, [file]
    )
    assert "error_msg" in response.headers["location"]
    file.read.assert_not_awaited()
    assert not await rows(case, Adjunto)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"issuer": "CCC010101CCC"}, "issuer_mismatch"),
        ({"receiver": "CCC010101CCC"}, "receiver_mismatch"),
        ({"currency": "USD"}, "currency_mismatch"),
        ({"total": "999"}, "amount_mismatch"),
        ({"kind": "P"}, "invalid_invoice"),
        ({"uuid": "not-a-uuid"}, "invalid_cfdi"),
    ],
)
async def test_invoice_identity_validation_is_atomic(case, kwargs, code):
    doc = await regular(case)
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[xml_attachment(**kwargs)],
            actor_id=case.owner.id,
        )
    assert exc.value.code == code
    await case.session.rollback()
    assert not await rows(case, Adjunto)
    assert not await rows(case, CFDIReport)


async def test_late_invoice_cannot_be_replaced_or_used_on_another_request(case):
    doc = await regular(case)
    fiscal_uuid = str(uuid4()).upper()
    invoice = xml_attachment(uuid=fiscal_uuid)
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=doc, attachments=[invoice], actor_id=case.owner.id
    )
    with pytest.raises(service.SolicitudValidationError, match="sustituir"):
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[xml_attachment()],
            actor_id=case.owner.id,
        )
    other = Documento(
        id=uuid4(),
        empleado_id=case.outsider.id,
        tipo="SOLICITUD",
        numero_referencia="S-OTHER",
        proveedor_cliente_id=case.provider.id,
        estado="aprobado",
        monto_solicitado=1000,
        currency="MXN",
    )
    case.session.add(other)
    await case.session.commit()
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=other,
            attachments=[invoice],
            actor_id=case.outsider.id,
        )
    assert exc.value.code == "duplicate_cfdi"
    with pytest.raises(CFDIDuplicateLinkError):
        await ingest_cfdi_from_upload(
            case.session,
            xml_bytes=invoice.raw_bytes,
            source="user_upload",
            entity=other,
            require_shared_confirmation=True,
        )
    assert len(await rows(case, Adjunto)) == 1
    assert not await rows(case, CFDIReport)


async def test_materiality_does_not_reingest_prior_fiscal_evidence(case, monkeypatch):
    doc = await regular(case, "borrador")
    ingest = AsyncMock(side_effect=AssertionError("must not ingest support"))
    monkeypatch.setattr(service, "_ingest_solicitud_cfdi_from_attachments", ingest)
    await service.add_solicitud_documento_adjuntos(
        case.session,
        documento=doc,
        attachments=[
            service.SolicitudTercerosAttachment(
                b"ok", "support.txt", "text/plain", "supporting"
            )
        ],
        actor_id=case.owner.id,
    )
    ingest.assert_not_awaited()


async def test_storage_failure_rolls_back_entire_upload(case, monkeypatch):
    doc = await regular(case)
    doc_id = doc.id
    original = service.create_adjunto_record
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic persistence failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "create_adjunto_record", fail_second)
    files = [
        upload(
            service.SolicitudTercerosAttachment(
                value, "support.txt", "text/plain", "supporting"
            )
        )
        for value in [b"one", b"two"]
    ]
    response = await routes.agregar_documento_adjuntos(
        doc_id, case.session, case.owner, "supporting", files
    )
    assert "unexpected_solicitud_attachment_add" in response.headers["location"]
    assert not await rows(case, Adjunto)
    assert not await rows(case, Aprobacion)


@pytest.mark.parametrize("category", ["cfdi_xml_evidence", "cfdi_pdf_evidence"])
def test_late_invoice_label_is_explicit(category):
    meta = SimpleNamespace(
        categoria=category,
        mime_type=None,
        tipo_archivo=None,
        nombre_archivo="invoice",
        activo=True,
    )
    assert "revisión contable pendiente" in _label_for_documento_meta(meta, 1)


def invoice_pdf(xml):
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.add_attachment("factura.xml", xml.raw_bytes)
    stream = BytesIO()
    writer.write(stream)
    return service.SolicitudTercerosAttachment(
        stream.getvalue(), "factura.pdf", "application/pdf", "cfdi_pdf"
    )


async def test_xml_then_pdf_remains_evidence_and_mismatch_is_rejected(case):
    doc = await regular(case)
    xml = xml_attachment()
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=doc, attachments=[xml], actor_id=case.owner.id
    )
    assert (
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[invoice_pdf(xml)],
            actor_id=case.owner.id,
        )
        == 1
    )
    assert {a.categoria for a in await rows(case, Adjunto)} == {
        "cfdi_xml_evidence",
        "cfdi_pdf_evidence",
    }
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[invoice_pdf(xml_attachment())],
            actor_id=case.owner.id,
        )
    assert exc.value.code == "invoice_immutable"
    assert not await rows(case, CFDIReport)


async def test_mixed_xml_pdf_batch_is_atomic(case):
    doc = await regular(case)
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[xml_attachment(), invoice_pdf(xml_attachment())],
            actor_id=case.owner.id,
        )
    assert exc.value.code == "invoice_pair_mismatch"
    assert not await rows(case, Adjunto)


async def test_linked_invoice_without_manual_uuid_cannot_be_replaced(case):
    doc = await regular(case, "borrador")
    xml = xml_attachment()
    await ingest_cfdi_from_upload(
        case.session,
        xml_bytes=xml.raw_bytes,
        source="user_upload",
        entity=doc,
        require_shared_confirmation=True,
    )
    doc.cfdi_uuid_manual = None
    doc.estado = "pagado"
    await case.session.commit()
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[xml_attachment()],
            actor_id=case.owner.id,
        )
    assert exc.value.code == "invoice_immutable"
    assert len(await rows(case, CFDIReport)) == 1
    assert not await rows(case, Adjunto)


async def test_shared_late_reservations_never_exceed_invoice_total(case):
    doc = await regular(case)
    doc.monto_solicitado = 400
    doc.cfdi_compartido_confirmado = True
    await case.session.commit()
    xml = xml_attachment()
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=doc, attachments=[xml], actor_id=case.owner.id
    )
    other = Documento(
        id=uuid4(),
        empleado_id=case.outsider.id,
        tipo="SOLICITUD",
        numero_referencia="S-SHARED",
        proveedor_cliente_id=case.provider.id,
        estado="aprobado",
        monto_solicitado=600,
        currency="MXN",
        cfdi_compartido_confirmado=True,
    )
    case.session.add(other)
    await case.session.commit()
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=other, attachments=[xml], actor_id=case.outsider.id
    )
    assert (
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[xml], actor_id=case.owner.id
        )
        == 0
    )
    third = Documento(
        id=uuid4(),
        empleado_id=case.owner.id,
        tipo="SOLICITUD",
        numero_referencia="S-EXCESS",
        proveedor_cliente_id=case.provider.id,
        estado="aprobado",
        monto_solicitado=1,
        currency="MXN",
        cfdi_compartido_confirmado=True,
    )
    case.session.add(third)
    await case.session.commit()
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=third, attachments=[xml], actor_id=case.owner.id
        )
    assert exc.value.code == "cfdi_fully_allocated"
    result = await ingest_cfdi_from_upload(
        case.session, xml_bytes=xml.raw_bytes, source="user_upload"
    )
    with pytest.raises(service.SolicitudValidationError):
        await service.validate_shared_cfdi_payment_amount(
            case.session, cfdi_report=result.cfdi_report, requested_amount=1
        )


async def test_fiscal_upload_requires_audited_actor_and_classified_doc_is_evidence(
    case,
):
    doc = await regular(case, "control_presupuestal")
    doc.budget_concept_id = case.concept.id
    await case.session.commit()
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[xml_attachment()]
        )
    assert exc.value.code == "evidence_actor_required"
    await service.add_solicitud_documento_adjuntos(
        case.session,
        documento=doc,
        attachments=[xml_attachment()],
        actor_id=case.owner.id,
    )
    assert not await rows(case, CFDIReport)


@pytest.mark.parametrize(
    "attachment,code",
    [
        (
            service.SolicitudTercerosAttachment(
                b"", "empty.txt", "text/plain", "supporting"
            ),
            "empty_attachment",
        ),
        (
            service.SolicitudTercerosAttachment(
                b"bad", "bad.exe", "application/x-msdownload", "supporting"
            ),
            "invalid_attachment_type",
        ),
        (
            service.SolicitudTercerosAttachment(
                b"bad", "bad.pdf", "application/pdf", "cfdi_pdf"
            ),
            "invalid_pdf",
        ),
        (
            service.SolicitudTercerosAttachment(
                b"<bad", "bad.xml", "application/xml", "cfdi_xml"
            ),
            "invalid_xml",
        ),
        (
            service.SolicitudTercerosAttachment(
                b"x" * (15 * 1024 * 1024 + 1), "big.txt", "text/plain", "supporting"
            ),
            "attachment_too_large",
        ),
    ],
)
async def test_existing_file_limits_and_types_apply_to_late_upload(
    case, attachment, code
):
    doc = await regular(case)
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[attachment],
            actor_id=case.owner.id,
        )
    assert exc.value.code == code
    assert not await rows(case, Adjunto)


async def test_same_uuid_with_different_fiscal_subtotal_is_rejected(case):
    doc = await regular(case)
    xml = xml_attachment()
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=doc, attachments=[xml], actor_id=case.owner.id
    )
    altered = service.SolicitudTercerosAttachment(
        xml.raw_bytes.replace(b'SubTotal="1000"', b'SubTotal="800"'),
        "altered.xml",
        "application/xml",
        "cfdi_xml",
    )
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[altered], actor_id=case.owner.id
        )
    assert exc.value.code == "invoice_immutable"
    assert len(await rows(case, Adjunto)) == 1


def test_document_detail_marks_late_invoice_pending():
    from devnous.gastos.utils.receipt_bytes import html_documento_archivos_detail

    meta = SimpleNamespace(
        id=uuid4(),
        categoria="cfdi_xml_evidence",
        mime_type="application/xml",
        tipo_archivo="application/xml",
        nombre_archivo="factura.xml",
        activo=True,
    )
    html = html_documento_archivos_detail(uuid4(), [meta])
    assert "revisión contable pendiente" in html
    assert "/eliminar" not in html


async def test_text_pdf_requires_xml_and_matches_receiver(case):
    from reportlab.pdfgen.canvas import Canvas

    doc = await regular(case)
    fiscal_uuid = str(uuid4()).upper()
    stream = BytesIO()
    canvas = Canvas(stream)
    for index, line in enumerate(
        [
            f"Folio fiscal: {fiscal_uuid}",
            "RFC Emisor: AAA010101AAA",
            "RFC Receptor: BBB010101BBB",
            "Fecha: 2026-10-08",
            "Moneda: MXN",
            "Subtotal 1000.00",
            "Total 1000.00",
        ]
    ):
        canvas.drawString(72, 750 - index * 20, line)
    canvas.save()
    pdf = service.SolicitudTercerosAttachment(
        stream.getvalue(), "factura.pdf", "application/pdf", "cfdi_pdf"
    )
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[pdf], actor_id=case.owner.id
        )
    assert exc.value.code == "invoice_xml_required"
    await service.add_solicitud_documento_adjuntos(
        case.session,
        documento=doc,
        attachments=[xml_attachment(uuid=fiscal_uuid)],
        actor_id=case.owner.id,
    )
    assert (
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[pdf], actor_id=case.owner.id
        )
        == 1
    )


def text_invoice_pdf(fiscal_uuid, fields):
    from reportlab.pdfgen.canvas import Canvas

    stream = BytesIO()
    canvas = Canvas(stream)
    lines = [f"Folio fiscal: {fiscal_uuid}"] + [
        f"{label}: {value}" for label, value in fields.items()
    ]
    for index, line in enumerate(lines):
        canvas.drawString(72, 750 - index * 20, line)
    canvas.save()
    return service.SolicitudTercerosAttachment(
        stream.getvalue(), "text.pdf", "application/pdf", "cfdi_pdf"
    )


@pytest.mark.parametrize(
    "missing", ["RFC Emisor", "RFC Receptor", "Subtotal", "Total", "Moneda", "all"]
)
@pytest.mark.parametrize("same_batch", [False, True])
async def test_text_pdf_missing_fields_use_authoritative_xml(case, missing, same_batch):
    doc = await regular(case)
    doc.currency = "USD"
    await case.session.commit()
    fiscal_uuid = str(uuid4()).upper()
    xml = xml_attachment(uuid=fiscal_uuid, currency="USD")
    fields = {
        "RFC Emisor": "AAA010101AAA",
        "RFC Receptor": "BBB010101BBB",
        "Moneda": "USD",
        "Subtotal": "1000.00",
        "Total": "1000.00",
    }
    fields = (
        {} if missing == "all" else {k: v for k, v in fields.items() if k != missing}
    )
    pdf = text_invoice_pdf(fiscal_uuid, fields)
    if not same_batch:
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[xml], actor_id=case.owner.id
        )
    assert await service.add_solicitud_documento_adjuntos(
        case.session,
        documento=doc,
        attachments=[xml, pdf] if same_batch else [pdf],
        actor_id=case.owner.id,
    ) == (2 if same_batch else 1)
    assert len(await rows(case, Adjunto)) == 2
    assert not await rows(case, CFDIReport)
    assert not await rows(case, AccountingPoliza)
    assert not await rows(case, ExpenseReport)
    assert (
        await service.add_solicitud_documento_adjuntos(
            case.session, documento=doc, attachments=[xml, pdf], actor_id=case.owner.id
        )
        == 0
    )


@pytest.mark.parametrize(
    "label,value",
    [
        ("RFC Emisor", "CCC010101CCC"),
        ("RFC Receptor", "CCC010101CCC"),
        ("Moneda", "USD"),
        ("Subtotal", "999.00"),
        ("Total", "999.00"),
    ],
)
async def test_text_pdf_explicit_contradictions_preserve_xml(case, label, value):
    doc = await regular(case)
    fiscal_uuid = str(uuid4()).upper()
    await service.add_solicitud_documento_adjuntos(
        case.session,
        documento=doc,
        attachments=[xml_attachment(uuid=fiscal_uuid)],
        actor_id=case.owner.id,
    )
    with pytest.raises(service.SolicitudValidationError) as exc:
        await service.add_solicitud_documento_adjuntos(
            case.session,
            documento=doc,
            attachments=[text_invoice_pdf(fiscal_uuid, {label: value})],
            actor_id=case.owner.id,
        )
    assert exc.value.code == "invoice_pair_mismatch"
    assert len(await rows(case, Adjunto)) == 1
    assert not await rows(case, CFDIReport)


@pytest.mark.parametrize("mode", ["late", "canonical"])
async def test_unconfirmed_caller_cannot_reuse_shared_late_reservation(case, mode):
    doc = await regular(case)
    doc.monto_solicitado = 400
    doc.cfdi_compartido_confirmado = True
    await case.session.commit()
    xml = xml_attachment()
    await service.add_solicitud_documento_adjuntos(
        case.session, documento=doc, attachments=[xml], actor_id=case.owner.id
    )
    other = Documento(
        id=uuid4(),
        empleado_id=case.outsider.id,
        tipo="SOLICITUD",
        numero_referencia="S-NOT-CONFIRMED",
        proveedor_cliente_id=case.provider.id,
        estado="aprobado",
        monto_solicitado=1000,
        currency="MXN",
        cfdi_compartido_confirmado=False,
    )
    case.session.add(other)
    await case.session.commit()
    if mode == "late":
        with pytest.raises(service.SolicitudValidationError) as exc:
            await service.add_solicitud_documento_adjuntos(
                case.session,
                documento=other,
                attachments=[xml],
                actor_id=case.outsider.id,
            )
        assert exc.value.code == "duplicate_cfdi"
    else:
        with pytest.raises(CFDIDuplicateLinkError):
            await ingest_cfdi_from_upload(
                case.session,
                xml_bytes=xml.raw_bytes,
                source="user_upload",
                entity=other,
                require_shared_confirmation=True,
            )
    assert len(await rows(case, Adjunto)) == 1
    assert not await rows(case, CFDIReport)
    assert not await rows(case, ExpenseReport)
    assert not await rows(case, AccountingPoliza)
