"""Invoice capture under an existing supplier advance, using session authority."""

from html import escape
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Documento, Empleado
from ..services.cfdi_ingestion_service import CFDIIngestionError
from ..services.documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
)
from ..services.supplier_advance_service import (
    advance_balances,
    submit_supplier_invoice,
)
from .dependencies import get_current_empleado, get_db_session

router = APIRouter()


async def _owned_paid_advance(session, document_id, actor):
    advance = await session.get(Documento, document_id)
    if advance is None or not advance.is_supplier_advance:
        raise HTTPException(404, "Anticipo no encontrado")
    if advance.empleado_id != actor.id:
        raise HTTPException(403, "Solo el solicitante puede comprobar el anticipo")
    if advance.estado != "pagado" or not advance.pagado_en:
        raise HTTPException(409, "Primero debe confirmarse el pago del anticipo")
    return advance


async def render_supplier_advance_controls(session, documento, actor) -> str:
    """Called after the canonical document-detail route has authorized the read."""
    if documento.supplier_advance_id:
        return (
            '<section class="surface"><h3>Comprobación de anticipo a proveedor</h3>'
            f"<p>Total de factura: {documento.supplier_invoice_total:.2f} · "
            f"Anticipo aplicado/reservado: {documento.supplier_advance_applied:.2f} · "
            f'Cantidad a pagar: {documento.monto_total:.2f} {escape(documento.currency or "MXN")}</p>'
            f'<a href="/documentos/{documento.supplier_advance_id}">Ver anticipo original</a></section>'
        )
    balance = await advance_balances(session, documento)
    status = (
        "Pendiente de comprobar"
        if balance["pending"] > 0
        else "Comprobado" if balance["paid"] > 0 else "Pendiente de pago"
    )
    children = list(
        (
            await session.execute(
                select(Documento)
                .where(Documento.supplier_advance_id == documento.id)
                .order_by(Documento.creado_en)
            )
        )
        .scalars()
        .all()
    )
    history = "".join(
        f'<li><a href="/documentos/{c.id}">{escape(c.numero_referencia)}</a> · '
        f"{escape(c.estado)} · Factura {c.supplier_invoice_total:.2f} · "
        f"Anticipo {c.supplier_advance_applied:.2f} · A pagar {c.monto_total:.2f}</li>"
        for c in children
    )
    action = (
        f'<a class="button primary" href="/documentos/{documento.id}/comprobar-anticipo-proveedor">Comprobar anticipo</a>'
        if documento.empleado_id == actor.id and balance["available"] > 0
        else ""
    )
    return (
        f'<section class="surface"><h3>Anticipo a proveedor · {status}</h3>'
        f'<p>Pagado: {balance["paid"]:.2f} · Aplicado: {balance["applied"]:.2f} · '
        f'Pendiente de comprobar: {balance["pending"]:.2f} · '
        f'Disponible para nuevas facturas: {balance["available"]:.2f} {escape(documento.currency or "MXN")}</p>'
        f'<p>Fecha esperada de comprobación: {escape(str(documento.supplier_advance_due_date or "Sin fecha"))}</p>'
        f"{action}<ul>{history}</ul></section>"
    )


@router.get(
    "/documentos/{documento_id}/comprobar-anticipo-proveedor",
    response_class=HTMLResponse,
)
async def supplier_invoice_form(
    documento_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    advance = await _owned_paid_advance(session, documento_id, actor)
    balances = await advance_balances(session, advance)
    if balances["available"] <= 0:
        raise HTTPException(409, "El anticipo ya está aplicado o reservado")
    return (
        '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Comprobar anticipo a proveedor</title><body style="font-family:system-ui;max-width:760px;margin:40px auto;padding:20px">'
        f"<h1>Comprobar anticipo {escape(advance.numero_referencia)}</h1>"
        f'<p>Anticipo disponible: {balances["available"]:.2f} {escape(advance.currency or "MXN")}</p>'
        "<p>El XML determina el total de la factura. Se reservará el anticipo disponible y se calculará la diferencia a pagar. "
        "La factura completa pasa a Control Presupuestal y Aprobación. Si no hay diferencia, se cierra sin transferencia.</p>"
        f'<form method="post" enctype="multipart/form-data" action="/documentos/{documento_id}/comprobar-anticipo-proveedor">'
        f'<input type="hidden" name="submission_id" value="{uuid4()}">'
        '<label>Factura XML <input type="file" name="archivo_xml" accept=".xml" required></label><br><br>'
        '<label>Factura PDF <input type="file" name="archivo_pdf" accept=".pdf"></label><br><br>'
        '<button type="submit">Registrar factura y enviar a Control Presupuestal</button></form>'
        f'<p><a href="/documentos/{documento_id}">Volver al anticipo</a></p></body></html>'
    )


@router.post("/documentos/{documento_id}/comprobar-anticipo-proveedor")
async def supplier_invoice_submit(
    documento_id: UUID,
    submission_id: UUID = Form(...),
    archivo_xml: UploadFile = File(...),
    archivo_pdf: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    await _owned_paid_advance(session, documento_id, actor)
    try:
        xml = SolicitudTercerosAttachment(
            raw_bytes=await archivo_xml.read(15 * 1024 * 1024 + 1),
            filename=archivo_xml.filename or "factura.xml",
            mime_type="application/xml",
            categoria="cfdi_xml",
        )
        pdf = None
        if archivo_pdf and archivo_pdf.filename:
            pdf = SolicitudTercerosAttachment(
                raw_bytes=await archivo_pdf.read(15 * 1024 * 1024 + 1),
                filename=archivo_pdf.filename,
                mime_type="application/pdf",
                categoria="cfdi_pdf",
            )
        child = await submit_supplier_invoice(
            session,
            advance_id=documento_id,
            actor=actor,
            submission_id=submission_id,
            xml=xml,
            pdf=pdf,
        )
        await session.commit()
        return RedirectResponse(f"/documentos/{child.id}", status_code=303)
    except (SolicitudValidationError, CFDIIngestionError) as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
