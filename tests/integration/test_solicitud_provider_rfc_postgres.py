"""RFC enrichment through real uploads/transactions on isolated PostgreSQL."""

import asyncio
import json
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select, text
from test_late_solicitud_evidence_postgres import (
    invoice,
    locked_document,
)
from test_late_solicitud_evidence_postgres import pg as pg_fixture

from devnous.gastos import models
from devnous.gastos.routes import user_routes as routes
from devnous.gastos.services import documento_service as service

pg = pg_fixture

RFC = "SOES940411MF1"
NOTICE = (
    "El proveedor no tenía RFC registrado. "
    "Se agregó automáticamente desde la factura."
)


def fiscal_file(*, issuer=RFC, name="SHEYLA FERNANDA SOTO EMBRIZ", **changes):
    attachment = invoice()
    raw = attachment.raw_bytes.decode().replace("AAA010101AAA", issuer)
    raw = raw.replace("Test provider", name)
    for old, new in changes.items():
        raw = raw.replace(old, new)
    return replace(attachment, raw_bytes=raw.encode())


async def prepare(pg, *, state="pagado", rfc=None, name=None):
    async with pg.factory.begin() as session:
        doc = await session.get(models.Documento, pg.first)
        doc.estado = state
        provider = await session.get(models.ProveedorCliente, doc.proveedor_cliente_id)
        provider.rfc = rfc
        provider.nombre = name or "SHEYLA FERNANDA SOTO EMBRIZ"
        return provider.id


async def upload(pg, attachment, *, actor=None):
    async with pg.factory() as session:
        employee = await session.get(models.Empleado, actor or pg.actor)
        file = SimpleNamespace(
            filename=attachment.filename,
            content_type=attachment.mime_type,
            read=AsyncMock(return_value=attachment.raw_bytes),
        )
        response = await routes.agregar_documento_adjuntos(
            pg.first, session, employee, attachment.categoria, [file]
        )
        return parse_qs(urlparse(response.headers["location"]).query)


async def verify_empty(pg, provider_id, expected=None):
    async with pg.factory() as session:
        provider = await session.get(models.ProveedorCliente, provider_id)
        assert provider.rfc == expected
        for model in (models.Adjunto, models.Aprobacion, models.CFDIReport):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("state", ["borrador", "aprobado", "pagado"])
@pytest.mark.parametrize("empty", [None, "", "   "])
async def test_route_fills_empty_rfc_and_audits_only_once(pg, state, empty):
    provider_id = await prepare(pg, state=state, rfc=empty)
    attachment = fiscal_file()
    result = await upload(pg, attachment)
    assert NOTICE in result["success_msg"][0]
    assert RFC not in str(result)  # No fiscal identifiers in redirect URLs.
    async with pg.factory() as session:
        provider = await session.get(models.ProveedorCliente, provider_id)
        assert provider.rfc == RFC
        doc = await session.get(models.Documento, pg.first)
        assert doc.estado == state
        assert doc.proveedor_cliente_id == provider_id
        assert doc.monto_solicitado == 1000
        audits = (await session.scalars(select(models.Aprobacion))).all()
        changes = [
            a
            for a in audits
            if "provider_rfc_added_from_invoice" in (a.comentario or "")
        ]
        assert len(changes) == 1
        assert changes[0].aprobador_id == pg.actor
        assert changes[0].accion == "adjuntar_soporte"
        payload = json.loads(changes[0].comentario)
        assert payload["previous_rfc"] == empty
        assert payload["rfc"] == RFC
        assert payload["provider_id"] == str(provider_id)
        assert payload["cfdi_uuid"] in attachment.raw_bytes.decode()
        if state != "borrador":
            assert doc.cfdi_report_id is None
            for model in (
                models.CFDIReport,
                models.ExpenseReport,
                models.AccountingPoliza,
            ):
                assert (
                    await session.scalar(select(func.count()).select_from(model)) == 0
                )
    result = await upload(pg, attachment)
    assert "success_msg" in result
    assert NOTICE not in result["success_msg"][0]
    async with pg.factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(models.Adjunto)) == 1
        )
        audits = (await session.scalars(select(models.Aprobacion))).all()
        assert (
            sum(
                "provider_rfc_added_from_invoice" in (a.comentario or "")
                for a in audits
            )
            == 1
        )


@pytest.mark.parametrize("state", ["borrador", "pagado"])
async def test_existing_different_rfc_is_not_overwritten(pg, state):
    provider_id = await prepare(pg, state=state, rfc="OTRO940411MF1")
    result = await upload(pg, fiscal_file())
    assert result["error"] == ["issuer_mismatch"]
    await verify_empty(pg, provider_id, "OTRO940411MF1")


async def test_existing_matching_rfc_needs_no_name_enrichment_or_notice(pg):
    provider_id = await prepare(pg, rfc=RFC, name="Nombre comercial distinto")
    result = await upload(pg, fiscal_file())
    assert "success_msg" in result
    assert NOTICE not in result["success_msg"][0]
    async with pg.factory() as session:
        assert (await session.get(models.ProveedorCliente, provider_id)).rfc == RFC


@pytest.mark.parametrize(
    "name", ["sheyla  fernanda soto embriz", "SHÉYLA FERNANDA SOTO EMBRIZ"]
)
async def test_name_normalizes_case_accents_and_spacing(pg, name):
    await prepare(pg, name=name)
    result = await upload(pg, fiscal_file())
    assert NOTICE in result["success_msg"][0]


@pytest.mark.parametrize(
    "attachment,code",
    [
        (fiscal_file(name="Otra persona"), "provider_identity_mismatch"),
        (fiscal_file(name=""), "provider_identity_mismatch"),
        (fiscal_file(issuer="OES940411MF"), "invalid_issuer_rfc"),
        (fiscal_file(issuer=""), "invalid_issuer_rfc"),
        (fiscal_file(BBB010101BBB="CCC010101CCC"), "receiver_mismatch"),
        (fiscal_file(MXN="USD"), "currency_mismatch"),
        (fiscal_file(**{'Total="1000"': 'Total="999"'}), "amount_mismatch"),
        (
            fiscal_file(**{'TipoDeComprobante="I"': 'TipoDeComprobante="P"'}),
            "invalid_invoice",
        ),
    ],
)
async def test_invalid_invoice_never_completes_catalog(pg, attachment, code):
    provider_id = await prepare(pg)
    result = await upload(pg, attachment)
    assert result["error"] == [code]
    await verify_empty(pg, provider_id)


async def test_another_catalog_entry_with_same_rfc_requires_review(pg):
    provider_id = await prepare(pg)
    async with pg.factory.begin() as session:
        session.add(
            models.ProveedorCliente(nombre="Duplicado", tipo="proveedor", rfc=RFC)
        )
    result = await upload(pg, fiscal_file())
    assert result["error"] == ["provider_rfc_ambiguous"]
    await verify_empty(pg, provider_id)


async def test_unauthorized_actor_cannot_enrich_catalog(pg):
    provider_id = await prepare(pg)
    outsider = uuid4()
    async with pg.factory.begin() as session:
        session.add(models.Empleado(id=outsider, nombre="Otro", rol="empleado"))
    result = await upload(pg, fiscal_file(), actor=outsider)
    assert "error_msg" in result
    await verify_empty(pg, provider_id)


async def test_attachment_storage_failure_rolls_back_catalog_and_audit(pg, monkeypatch):
    provider_id = await prepare(pg)
    monkeypatch.setattr(
        service,
        "create_adjunto_record",
        AsyncMock(side_effect=RuntimeError("storage failure")),
    )
    result = await upload(pg, fiscal_file())
    assert result["error"] == ["unexpected_solicitud_attachment_add"]
    await verify_empty(pg, provider_id)


async def test_inactive_provider_is_not_enriched(pg):
    provider_id = await prepare(pg)
    async with pg.factory.begin() as session:
        (await session.get(models.ProveedorCliente, provider_id)).activo = False
    result = await upload(pg, fiscal_file())
    assert result["error"] == ["inactive_provider"]
    await verify_empty(pg, provider_id)


async def test_invalid_uuid_does_not_enrich_catalog(pg):
    provider_id = await prepare(pg)
    attachment = fiscal_file()
    from devnous.gastos.services.cfdi_parser import parse_cfdi_xml

    parsed = parse_cfdi_xml(attachment.raw_bytes.decode())
    attachment = replace(
        attachment,
        raw_bytes=attachment.raw_bytes.replace(
            parsed["cfdi_uuid"].encode(), b"not-a-uuid"
        ),
    )
    result = await upload(pg, attachment)
    assert result["error"] == ["invalid_cfdi"]
    await verify_empty(pg, provider_id)


async def test_actor_is_required_to_enrich_catalog(pg):
    provider_id = await prepare(pg)
    async with pg.factory() as session:
        doc = await locked_document(session, pg.first)
        with pytest.raises(service.SolicitudValidationError) as exc:
            await service.add_solicitud_documento_adjuntos(
                session, documento=doc, attachments=[fiscal_file()]
            )
        assert exc.value.code == "evidence_actor_required"
        await session.rollback()
    await verify_empty(pg, provider_id)


async def test_reserved_uuid_never_completes_another_empty_provider(pg):
    provider_id = await prepare(pg)
    attachment = fiscal_file()
    async with pg.factory.begin() as session:
        other = models.ProveedorCliente(
            id=uuid4(), nombre="SHEYLA FERNANDA SOTO EMBRIZ", tipo="proveedor", rfc=RFC
        )
        session.add(other)
        doc = await session.get(models.Documento, pg.second)
        doc.proveedor_cliente_id = other.id
    async with pg.factory() as session:
        doc = await locked_document(session, pg.second)
        await service.add_solicitud_documento_adjuntos(
            session, documento=doc, attachments=[attachment], actor_id=pg.actor
        )
    # The donor entry is no longer a duplicate, but the UUID remains reserved.
    async with pg.factory.begin() as session:
        (await session.get(models.ProveedorCliente, other.id)).rfc = None
    result = await upload(pg, attachment)
    assert result["error"] == ["duplicate_cfdi"]
    async with pg.factory() as session:
        assert (await session.get(models.ProveedorCliente, provider_id)).rfc is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(models.Adjunto)
                .where(models.Adjunto.documento_id == pg.first)
            )
            == 0
        )


async def test_pdf_with_embedded_xml_can_complete_identity(pg):
    await prepare(pg)
    attachment = fiscal_file()
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.add_attachment("invoice.xml", attachment.raw_bytes)
    stream = BytesIO()
    writer.write(stream)
    pdf = service.SolicitudTercerosAttachment(
        stream.getvalue(), "invoice.pdf", "application/pdf", "cfdi_pdf"
    )
    result = await upload(pg, pdf)
    assert NOTICE in result["success_msg"][0]


async def test_text_pdf_without_xml_does_not_enrich_catalog(pg, monkeypatch):
    from devnous.gastos.services import cfdi_upload_resolver
    from devnous.gastos.services.cfdi_parser import parse_cfdi_xml

    provider_id = await prepare(pg)
    parsed = parse_cfdi_xml(fiscal_file().raw_bytes.decode())
    parsed["tipo_de_comprobante"] = ""
    monkeypatch.setattr(
        cfdi_upload_resolver, "extract_cfdi_xml_from_pdf", lambda _: None
    )
    monkeypatch.setattr(cfdi_upload_resolver, "parse_cfdi_pdf", lambda _: parsed.copy())
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    stream = BytesIO()
    writer.write(stream)
    pdf = service.SolicitudTercerosAttachment(
        stream.getvalue(), "invoice.pdf", "application/pdf", "cfdi_pdf"
    )
    result = await upload(pg, pdf)
    assert result["error"] == ["invoice_xml_required"]
    await verify_empty(pg, provider_id)


@pytest.mark.parametrize("mode", ["same", "different", "other_provider"])
async def test_concurrent_uploads_do_not_overwrite_or_duplicate_rfc(pg, mode):
    provider_id = await prepare(pg)
    if mode == "other_provider":
        async with pg.factory.begin() as session:
            other = models.ProveedorCliente(
                id=uuid4(),
                nombre="SHEYLA FERNANDA SOTO EMBRIZ",
                tipo="proveedor",
                rfc=None,
            )
            session.add(other)
            (await session.get(models.Documento, pg.second)).proveedor_cliente_id = (
                other.id
            )
    started = asyncio.Event()
    worker = {}
    second_updates = []

    async def second_upload():
        async with pg.factory() as session:
            worker["pid"] = await session.scalar(text("SELECT pg_backend_pid()"))
            doc = await locked_document(session, pg.second)
            # A cached NULL must be refreshed after the winning writer commits.
            await session.get(models.ProveedorCliente, doc.proveedor_cliente_id)
            started.set()
            try:
                await service.add_solicitud_documento_adjuntos(
                    session,
                    documento=doc,
                    attachments=[
                        fiscal_file(
                            issuer="SOES940411MF2" if mode == "different" else RFC
                        )
                    ],
                    actor_id=pg.actor,
                    rfc_updates=second_updates,
                )
                return "ok"
            except service.SolicitudValidationError as exc:
                await session.rollback()
                return exc.code

    async with pg.factory() as session:
        doc = await locked_document(session, pg.first)
        await service.add_solicitud_documento_adjuntos(
            session,
            documento=doc,
            attachments=[fiscal_file()],
            actor_id=pg.actor,
            commit=False,
        )
        await session.flush()
        pending = asyncio.create_task(second_upload())
        try:
            await asyncio.wait_for(started.wait(), 5)

            async def wait_for_contention():
                async with pg.factory() as observer:
                    while not await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE pid=:pid AND NOT granted)"
                        ),
                        {"pid": worker["pid"]},
                    ):
                        if pending.done():
                            pytest.fail("Concurrent writer did not wait on PostgreSQL")
                        await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_for_contention(), 10)
            await session.commit()
            expected = {
                "same": "ok",
                "different": "issuer_mismatch",
                "other_provider": "provider_rfc_ambiguous",
            }[mode]
            assert await asyncio.wait_for(pending, 10) == expected
            assert second_updates == []
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await session.rollback()
    async with pg.factory() as session:
        assert (await session.get(models.ProveedorCliente, provider_id)).rfc == RFC
        assert (
            await session.scalar(
                select(func.count())
                .select_from(models.ProveedorCliente)
                .where(models.ProveedorCliente.rfc == RFC)
            )
            == 1
        )
