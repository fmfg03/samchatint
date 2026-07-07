"""
Webhook handler for Tocino AI CFDI status updates.

Handles incoming webhooks from Tocino when invoice status changes.
Updates invoice_reports table and syncs data back to expense_reports.
"""

import asyncio
import hmac
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from sqlalchemy import select, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ExpenseReport, InvoiceReport
from ..services.telegram_notify import send_telegram_message
from ..services.cuenta_contable_suggester import get_cuenta_suggestion
from ..services.tocino_client import get_tocino_client, TocinoAPIError
from ..utils.receipt_bytes import upsert_gasto_tocino_adjunto

logger = logging.getLogger(__name__)

router = APIRouter()

PRODUCTION_ENV_VALUES = frozenset({"production", "prod", "live"})
DEFAULT_TOCINO_WEBHOOK_MAX_BODY_BYTES = 1024 * 1024


def _samchat_runtime_env() -> str:
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        value = (os.getenv(name) or "").strip().lower()
        if value:
            return value
    return ""


def _is_production_runtime() -> bool:
    return _samchat_runtime_env() in PRODUCTION_ENV_VALUES


def _tocino_webhook_secret_for_runtime() -> str:
    secret = (os.getenv("TOCINO_WEBHOOK_SECRET") or "").strip()
    if secret:
        return secret
    if _is_production_runtime():
        logger.error("TOCINO_WEBHOOK_SECRET is missing in production mode")
        raise HTTPException(
            status_code=503,
            detail="Webhook signature verification is not configured",
        )
    return ""


def _tocino_webhook_max_body_bytes() -> int:
    raw = (os.getenv("TOCINO_WEBHOOK_MAX_BODY_BYTES") or "").strip()
    if not raw:
        return DEFAULT_TOCINO_WEBHOOK_MAX_BODY_BYTES
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TOCINO_WEBHOOK_MAX_BODY_BYTES


async def _read_limited_webhook_body(request: Request) -> bytes:
    max_bytes = _tocino_webhook_max_body_bytes()
    content_length = (request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="Webhook payload too large",
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")

    body = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload too large")
    return bytes(body)


async def _upsert_gasto_adjunto_tocino(
    session: AsyncSession,
    gasto_id: UUID,
    *,
    categoria: str,
    ruta_payload: str,
    mime_type: str,
    nombre_archivo: Optional[str],
) -> None:
    """Attach or update one gasto adjunto while tolerating old DB schemas."""
    await upsert_gasto_tocino_adjunto(
        session,
        gasto_id=gasto_id,
        categoria=categoria,
        ruta_archivo=ruta_payload,
        mime_type=mime_type,
        nombre_archivo=nombre_archivo,
    )

def verify_webhook_signature(body: bytes, signature: Optional[str], secret: Optional[str]) -> bool:
    """Verify webhook signature using HMAC-SHA256."""
    if not secret or not signature:
        logger.warning("Webhook secret or signature missing")
        return False

    try:
        # Compute expected signature
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        # Compare signatures (constant-time comparison to prevent timing attacks)
        return hmac.compare_digest(expected_signature, signature)
    except Exception as e:
        logger.error("Error verifying webhook signature", extra={"error_message": str(e)})
        return False


def map_tocino_status_to_estado(tocino_status: str) -> str:
    """Map Tocino API status (Spanish) to our database estado_factura."""
    normalized = str(tocino_status or "").strip().lower()
    normalized = normalized.replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if normalized == "no facurable":
        normalized = "no facturable"

    # Map Spanish statuses from Tocino platform
    if normalized in {"finalizado", "finalized"}:
        return "completada"
    elif normalized in {"no facturable", "not invoiceable", "error", "mantenimiento"}:
        return "error"
    elif normalized in {"enviando", "leyendo", "leído", "leido", "facturando", "editando", "facturada"}:
        return "en_proceso"
    # Legacy English statuses (for backward compatibility)
    elif normalized in {"waiting", "processing", "success", "invoicing", "invoiced"}:
        return "en_proceso"
    elif normalized in {"email_sent", "whatsapp_sent"}:
        return "en_proceso"
    else:
        # Unknown status, default to processing
        return "en_proceso"


def should_notify_status_change(tocino_status: str, previous_status: Optional[str]) -> bool:
    """Determine if a status change warrants a user notification."""
    normalized_status = str(tocino_status or "").strip().lower().replace("-", " ").replace("_", " ")
    normalized_status = " ".join(normalized_status.split())
    if normalized_status == "no facurable":
        normalized_status = "no facturable"
    normalized_previous = str(previous_status or "").strip().lower().replace("-", " ").replace("_", " ") if previous_status is not None else None
    if normalized_previous:
        normalized_previous = " ".join(normalized_previous.split())
        if normalized_previous == "no facurable":
            normalized_previous = "no facturable"

    milestone_statuses = [
        "finalizado", "finalized",  # Invoice ready
        "no facturable", "not invoiceable", "error",   # Error occurred
        "mantenimiento",            # Maintenance
        "leído", "leido",           # Data extracted
        "facturada", "invoiced",    # Invoice created
    ]

    # Only notify for milestone statuses
    if normalized_status not in milestone_statuses:
        return False

    # Notify if this is the first time or status changed
    if normalized_previous is None:
        return True
    elif normalized_previous not in milestone_statuses:
        return True
    elif normalized_previous != normalized_status:
        return True
    else:
        return False


def notify_user_status_change(
    telegram_user_id: Optional[int],
    tocino_status: str,
    numero_referencia: str,
    link_pdf: Optional[str] = None,
    link_xml: Optional[str] = None,
    mensaje_error: Optional[str] = None
) -> None:
    """Send Telegram notification to user about invoice status change (in Spanish)."""

    if not telegram_user_id:
        return

    normalized_status = str(tocino_status or "").strip().lower().replace("-", " ").replace("_", " ")
    normalized_status = " ".join(normalized_status.split())
    if normalized_status == "no facurable":
        normalized_status = "no facturable"

    # Build Spanish message based on Tocino status
    if normalized_status in {"finalizado", "finalized"}:
        message = f"""✅ **¡Tu factura está lista!**

**Referencia:** {numero_referencia}
**Estado:** Finalizado

El comercio ha enviado la factura y la hemos recibido correctamente. Ahora puedes encontrarla en la sección Mis facturas.

📄 **Enlaces:**
"""
        if link_pdf:
            message += f"• [Descargar PDF]({link_pdf})\n"
        if link_xml:
            message += f"• [Descargar XML]({link_xml})\n"

        message += "\n¡Tu CFDI está listo para usar!"

    elif normalized_status in {"no facturable", "not invoiceable", "error"}:
        message = f"""❌ **No se puede facturar tu ticket**

**Referencia:** {numero_referencia}
**Estado:** No facturable

Hay un problema con el ticket y no se podrá facturar. Esto puede deberse a restricciones del comercio o del SAT.

"""
        if mensaje_error:
            message += f"**Detalles:** {mensaje_error}\n\n"
        message += "Por favor, contacta al soporte si necesitas ayuda."

    elif normalized_status == "mantenimiento":
        message = f"""⚠️ **Sitio en mantenimiento**

**Referencia:** {numero_referencia}
**Estado:** Mantenimiento

El sitio de auto-facturación del comercio se encuentra en mantenimiento y no está emitiendo facturas en este momento.

Tu solicitud será procesada cuando el sitio vuelva a estar disponible. Te notificaremos cuando avance el proceso."""

    elif normalized_status in {"leído", "leido"}:
        message = f"""✅ **Datos extraídos exitosamente**

**Referencia:** {numero_referencia}
**Estado:** Leído

Nuestro bot ha extraído exitosamente la información del ticket. Tu factura está siendo procesada."""

    elif normalized_status in {"facturada", "invoiced"}:
        message = f"""📄 **Factura creada**

**Referencia:** {numero_referencia}
**Estado:** Facturada

El bot ha creado exitosamente tu factura. Estamos esperando que llegue a nuestro sistema para poder entregártela.

Te notificaremos cuando esté lista para descargar."""

    else:
        # Don't notify for other statuses
        return

    # Send asynchronously (fire and forget)
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, create a task
            asyncio.create_task(send_telegram_message(telegram_user_id, message))
        else:
            # If no loop, run it
            asyncio.run(send_telegram_message(telegram_user_id, message))
    except Exception as e:
        logger.error(f"Error scheduling Telegram notification: {e}")


# This will be set by the app that includes these routes
_db_session_maker = None

def set_db_session_maker(session_maker):
    """Set the database session maker for webhook routes."""
    global _db_session_maker
    _db_session_maker = session_maker

async def get_db_session() -> AsyncSession:
    """Dependency to get database session."""
    if _db_session_maker is None:
        raise RuntimeError("Database session maker not set. Call set_db_session_maker() first.")
    async with _db_session_maker() as session:
        yield session


def tocino_status_response_to_webhook_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build webhook-like payload from Tocino GET /api/external/tickets/:TICKET_ID/ response.

    Used by the fallback sync so we can reuse apply_tocino_payload_to_db.
    New API returns ticket_id; legacy used nova_request_id. We normalize to nova_request_id.
    """
    nova_request_id = data.get("ticket_id") or data.get("nova_request_id") or ""
    status = data.get("status", "")
    invoice = data.get("invoice") or {}
    # Normalize new webhook shape:
    # - invoice.pdf / invoice.xml
    # and legacy shape:
    # - invoice.pdf_attachment.file / invoice.xml_attachment.file
    pdf_link = None
    xml_link = None
    if isinstance(invoice, dict) and invoice:
        if isinstance(invoice.get("pdf"), str):
            pdf_link = invoice.get("pdf")
        if isinstance(invoice.get("xml"), str):
            xml_link = invoice.get("xml")

        pdf_attachment = invoice.get("pdf_attachment") or {}
        xml_attachment = invoice.get("xml_attachment") or {}
        if not pdf_link and isinstance(pdf_attachment, dict):
            pdf_link = pdf_attachment.get("file")
        if not xml_link and isinstance(xml_attachment, dict):
            xml_link = xml_attachment.get("file")

    invoice = {
        "pdf_attachment": {"file": pdf_link} if pdf_link else {},
        "xml_attachment": {"file": xml_link} if xml_link else {},
    }

    normalized_status = str(status or "").strip().lower().replace("-", " ").replace("_", " ")
    normalized_status = " ".join(normalized_status.split())
    if normalized_status == "no facurable":
        normalized_status = "no facturable"
    if normalized_status in {"no facturable", "not invoiceable"}:
        status = "No facturable"
    error_msg = (
        data.get("not_invoiceable_cause")
        or data.get("error_msg")
        or data.get("error_code")
        or data.get("exception_error")
        or data.get("error")
        or data.get("message")
        or data.get("detail")
    )
    return {
        "ticket_id": data.get("ticket_id"),
        "nova_request_id": nova_request_id,
        "status": status,
        "invoice": invoice,
        "error_msg": error_msg,
    }


async def apply_tocino_payload_to_db(session: AsyncSession, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply Tocino payload to DB: invoice_reports, expense_reports, CFDI processing, Telegram.

    Shared by webhook and fallback sync. Payload must have nova_request_id and status;
    optional invoice (pdf_attachment.file, xml_attachment.file) and error fields.
    """
    nova_request_id = payload.get("nova_request_id") or payload.get("ticket_id")
    tocino_status = payload.get("status", "")

    if not nova_request_id:
        raise ValueError("Payload missing nova_request_id")

    estado_factura = map_tocino_status_to_estado(tocino_status)

    invoice_data = payload.get("invoice", {})
    link_pdf = None
    link_xml = None
    if invoice_data:
        pdf_attachment = invoice_data.get("pdf_attachment", {})
        xml_attachment = invoice_data.get("xml_attachment", {})
        link_pdf = pdf_attachment.get("file") if isinstance(pdf_attachment, dict) else None
        link_xml = xml_attachment.get("file") if isinstance(xml_attachment, dict) else None

    mensaje_error = None
    normalized_status = str(tocino_status or "").strip().lower().replace("-", " ").replace("_", " ")
    normalized_status = " ".join(normalized_status.split())
    if normalized_status == "no facurable":
        normalized_status = "no facturable"
    if normalized_status in {"error", "no facturable", "not invoiceable", "mantenimiento"}:
        error_msg = (
            payload.get("not_invoiceable_cause")
            or payload.get("error_msg")
            or payload.get("error_code")
            or payload.get("exception_error")
            or payload.get("error")
            or payload.get("message")
            or payload.get("detail")
        )
        if error_msg:
            mensaje_error = f"Error: {error_msg}"
        elif normalized_status in {"no facturable", "not invoiceable", "error"}:
            mensaje_error = "No facturable: Restricciones del comercio o del SAT"
        elif normalized_status == "mantenimiento":
            mensaje_error = "Mantenimiento: Sitio del comercio no disponible"

    # Step 1: Upsert into invoice_reports
    result = await session.execute(
        select(InvoiceReport).where(InvoiceReport.nova_request_id == nova_request_id)
    )
    factura = result.scalar_one_or_none()

    previous_tocino_status = None
    if factura and factura.webhook_payload and isinstance(factura.webhook_payload, dict):
        previous_tocino_status = factura.webhook_payload.get("status")

    if factura:
        factura.estado_factura = estado_factura
        factura.link_pdf = link_pdf or factura.link_pdf
        factura.link_xml = link_xml or factura.link_xml
        factura.mensaje_error = mensaje_error
        factura.webhook_payload = payload
        factura.updated_at = datetime.utcnow()
        logger.info("Updated existing factura", extra={"nova_request_id": nova_request_id})
    else:
        factura = InvoiceReport(
            nova_request_id=nova_request_id,
            estado_factura=estado_factura,
            link_pdf=link_pdf,
            link_xml=link_xml,
            mensaje_error=mensaje_error,
            webhook_payload=payload,
        )
        session.add(factura)
        logger.info("Created new factura from payload", extra={"nova_request_id": nova_request_id})

    # Step 2: Match to expense_reports and sync
    expense_result = await session.execute(
        select(ExpenseReport).where(ExpenseReport.nova_request_id == nova_request_id)
    )
    gasto = expense_result.scalar_one_or_none()

    if gasto:
        if not factura.expense_id:
            factura.expense_id = gasto.id
            session.add(factura)

        gasto.estado_factura = estado_factura
        if link_pdf:
            gasto.link_pdf = link_pdf
        if link_xml:
            gasto.link_xml = link_xml
        if mensaje_error is not None:
            gasto.mensaje_error = mensaje_error
        gasto.updated_at = datetime.utcnow()
        session.add(gasto)

        if link_pdf:
            await _upsert_gasto_adjunto_tocino(
                session,
                gasto.id,
                categoria="cfdi_pdf",
                ruta_payload=link_pdf,
                mime_type="application/pdf",
                nombre_archivo="cfdi.pdf",
            )
        if link_xml:
            await _upsert_gasto_adjunto_tocino(
                session,
                gasto.id,
                categoria="cfdi_xml",
                ruta_payload=link_xml,
                mime_type="application/xml",
                nombre_archivo="cfdi.xml",
            )

        logger.info("Synced CFDI fields to expense_reports", extra={
            "nova_request_id": nova_request_id,
            "numero_referencia": gasto.numero_referencia,
        })

        # Commit invoice + expense updates atomically before downstream processing.
        await session.commit()

        # Step 3: Process CFDI XML if available
        if link_xml:
            try:
                from ..workers.cfdi_processor import process_cfdi_xml_async
                cfdi_result = await process_cfdi_xml_async(session, nova_request_id=nova_request_id)
                if cfdi_result.get("status") == "success":
                    logger.info("CFDI XML processed successfully", extra={
                        "nova_request_id": nova_request_id,
                        "cfdi_uuid": cfdi_result.get("cfdi_uuid"),
                    })
                elif cfdi_result.get("status") == "already_processed":
                    logger.debug("CFDI already processed", extra={"nova_request_id": nova_request_id})
                else:
                    logger.warning("CFDI processing failed", extra={
                        "nova_request_id": nova_request_id,
                        "error": cfdi_result.get("message"),
                    })
            except Exception as e:
                logger.error("Error processing CFDI XML", extra={
                    "nova_request_id": nova_request_id,
                    "error": str(e),
                }, exc_info=True)

        # Step 4: Notify user if status changed to a milestone
        if should_notify_status_change(tocino_status, previous_tocino_status):
            notify_user_status_change(
                telegram_user_id=gasto.telegram_user_id,
                tocino_status=tocino_status,
                numero_referencia=gasto.numero_referencia,
                link_pdf=link_pdf,
                link_xml=link_xml,
                mensaje_error=mensaje_error,
            )

        # Step 5: Best-effort auto accounting assignment for uncategorized expenses.
        auto_assign_enabled = os.getenv("ASSISTANT_AUTO_ASSIGN_CUENTA", "1").strip().lower() not in {"0", "false", "no"}
        if auto_assign_enabled and gasto.cuenta_contable_id is None and gasto.estado_gasto != "cancelado":
            try:
                min_confidence = float(os.getenv("ASSISTANT_AUTO_ASSIGN_MIN_CONFIDENCE", "0.80"))
                use_llm_for_suggester = os.getenv("ASSISTANT_CUENTA_SUGGESTER_USE_LLM", "0").strip().lower() in {"1", "true", "yes"}
                suggestion = await get_cuenta_suggestion(
                    session=session,
                    expense_id=gasto.id,
                    concepto=gasto.concepto or "",
                    metodo_pago=gasto.metodo_pago,
                    proyecto=gasto.proyecto,
                    gasto_cantidad=float(gasto.gasto_cantidad or 0),
                    use_llm=use_llm_for_suggester,
                )
                if suggestion and float(suggestion.confidence_score) >= min_confidence:
                    gasto.cuenta_contable_id = suggestion.cuenta_contable_id
                    gasto.updated_at = datetime.utcnow()
                    session.add(gasto)
                    await session.commit()
                    logger.info(
                        "Auto-assigned cuenta_contable after Tocino sync",
                        extra={
                            "expense_id": str(gasto.id),
                            "numero_referencia": gasto.numero_referencia,
                            "cuenta_contable_id": str(suggestion.cuenta_contable_id),
                            "cuenta_codigo": suggestion.cuenta_codigo,
                            "confidence": float(suggestion.confidence_score),
                            "tier": suggestion.tier,
                        },
                    )
            except Exception as e:
                logger.warning(
                    "Auto-assignment of cuenta_contable failed after Tocino sync",
                    extra={"expense_id": str(gasto.id), "error": str(e)},
                )
    else:
        logger.warning("No matching expense_reports for nova_request_id", extra={"nova_request_id": nova_request_id})
        # Commit invoice update before downstream processing.
        await session.commit()
        if link_xml:
            try:
                from ..workers.cfdi_processor import process_cfdi_xml_async
                cfdi_result = await process_cfdi_xml_async(session, nova_request_id=nova_request_id)
                if cfdi_result.get("status") == "success":
                    logger.info("CFDI XML processed (no matching expense)", extra={
                        "nova_request_id": nova_request_id,
                        "cfdi_uuid": cfdi_result.get("cfdi_uuid"),
                    })
            except Exception as e:
                logger.error("Error processing CFDI XML (no matching expense)", extra={
                    "nova_request_id": nova_request_id,
                    "error": str(e),
                }, exc_info=True)

    return {
        "status": "success",
        "nova_request_id": nova_request_id,
        "estado_factura": estado_factura,
        "synced_to_expenses": gasto is not None,
    }


@router.post("/tocino-webhook")
async def receive_tocino_webhook(
    request: Request,
    typeform_signature: Optional[str] = Header(None, alias="typeform-signature"),
    session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """
    Handle Tocino export webhook notifications.

    This endpoint receives real-time updates from Tocino when invoice status changes.
    It stores the full payload in invoice_reports and syncs key fields to expense_reports.
    """

    # Read request body as bytes for signature verification and JSON parsing.
    body_bytes = await _read_limited_webhook_body(request)
    body_length = len(body_bytes)

    signature_header_name = os.getenv("TOCINO_WEBHOOK_SIGNATURE_HEADER", "typeform-signature").lower()

    # Verify signature when configured; fail closed in explicit production mode.
    webhook_secret = _tocino_webhook_secret_for_runtime()
    if webhook_secret:
        configured_signature = request.headers.get(signature_header_name)
        signature_to_verify = configured_signature or typeform_signature
        if not verify_webhook_signature(body_bytes, signature_to_verify, webhook_secret):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Handle empty body (likely a test/verification request from Tocino)
    if body_length == 0:
        logger.info("Received empty webhook body - treating as test/verification request")
        return {
            "status": "ok",
            "message": "Webhook endpoint is active and ready to receive events",
            "test_request": True
        }

    # Parse JSON payload
    try:
        body_text = body_bytes.decode('utf-8')
        payload = json.loads(body_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.error(f"Error parsing webhook payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {str(e)}")

    # Normalize payload shape from Tocino webhook before processing
    normalized_payload = dict(payload)
    normalized_payload.update(tocino_status_response_to_webhook_payload(payload))

    # Extract key fields
    nova_request_id = normalized_payload.get("nova_request_id")
    tocino_status = normalized_payload.get("status", "")

    if not nova_request_id:
        logger.warning("Webhook payload missing ticket identifier (ticket_id/nova_request_id)")
        raise HTTPException(status_code=400, detail="Missing ticket_id/nova_request_id in payload")

    logger.info(
        "Tocino webhook received | event=webhook_received ticket_id=%s nova_request_id=%s status=%s signature_header_used=%s payload_size=%s",
        normalized_payload.get("ticket_id"),
        nova_request_id,
        tocino_status,
        signature_header_name,
        body_length,
        extra={
        "event": "webhook_received",
        "ticket_id": normalized_payload.get("ticket_id"),
        "nova_request_id": nova_request_id,
        "status": tocino_status,
        "signature_header_used": signature_header_name,
        "payload_size": body_length,
        },
    )

    try:
        result = await apply_tocino_payload_to_db(session, normalized_payload)
        logger.info(
            "Tocino webhook processed | event=webhook_processed ticket_id=%s nova_request_id=%s estado_factura=%s synced_to_expenses=%s error=%s",
            normalized_payload.get("ticket_id"),
            nova_request_id,
            result.get("estado_factura"),
            result.get("synced_to_expenses"),
            normalized_payload.get("error_msg") or normalized_payload.get("not_invoiceable_cause"),
            extra={
            "event": "webhook_processed",
            "ticket_id": normalized_payload.get("ticket_id"),
            "nova_request_id": nova_request_id,
            "estado_factura": result.get("estado_factura"),
            "synced_to_expenses": result.get("synced_to_expenses"),
            "error": normalized_payload.get("error_msg") or normalized_payload.get("not_invoiceable_cause"),
            },
        )
        return result
    except ValueError as e:
        logger.warning(str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception(
            "Error processing Tocino webhook",
            extra={
                "nova_request_id": nova_request_id,
                "ticket_id": normalized_payload.get("ticket_id"),
            },
        )
        raise HTTPException(status_code=500, detail="Unexpected processing error")


@router.get("/tocino-webhook")
async def verify_tocino_webhook() -> Dict[str, Any]:
    """Handle Tocino webhook verification (GET request).

    Many webhook systems send a GET request to verify the endpoint exists.
    This allows Tocino to verify the webhook URL is active.
    """
    logger.info("Received Tocino webhook verification request (GET)")
    return {"status": "ok", "message": "Webhook endpoint is active"}


# Fallback sync: advisory lock key (PostgreSQL bigint), cap per run, delay between API calls
TOCINO_SYNC_ADVISORY_LOCK_KEY = 987654321098765
MAX_OPEN_TICKETS_PER_RUN = 50
DELAY_BETWEEN_API_CALLS_SEC = 3
try:
    SKIP_TICKETS_UPDATED_WITHIN_HOURS = max(0.0, float(os.getenv("TOCINO_SYNC_SKIP_UPDATED_WITHIN_HOURS", "0")))
except ValueError:
    SKIP_TICKETS_UPDATED_WITHIN_HOURS = 0.0


@router.post("/tocino-cfdi-sync")
async def tocino_cfdi_sync(
    request: Request,
    x_tocino_sync_secret: Optional[str] = Header(None, alias="X-Tocino-Sync-Secret"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Fallback: poll Tocino for open tickets and apply same DB + CFDI + Telegram logic as webhook.

    Protected by X-Tocino-Sync-Secret (env: TOCINO_SYNC_SECRET). Uses PostgreSQL advisory lock
    so only one worker runs the sync at a time. Processes at most MAX_OPEN_TICKETS_PER_RUN
    tickets with DELAY_BETWEEN_API_CALLS_SEC between Tocino API calls.
    """
    sync_secret = os.getenv("TOCINO_SYNC_SECRET")
    if not sync_secret or x_tocino_sync_secret != sync_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing sync secret")

    # Acquire advisory lock (session-level; released on unlock or disconnect)
    lock_result = await session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": TOCINO_SYNC_ADVISORY_LOCK_KEY})
    row = lock_result.fetchone()
    locked = row[0] if row else False
    if not locked:
        logger.info("Tocino CFDI sync skipped: lock already held (another worker running)")
        raise HTTPException(status_code=409, detail="Sync already in progress")

    try:
        # Open tickets: have nova_request_id and estado_factura not in (completada, error)
        # Optional: skip tickets updated in the last N hours
        cutoff = datetime.utcnow() - timedelta(hours=SKIP_TICKETS_UPDATED_WITHIN_HOURS)
        open_conditions = [
            ExpenseReport.nova_request_id.isnot(None),
            or_(
                ExpenseReport.estado_factura.is_(None),
                ~ExpenseReport.estado_factura.in_(["completada", "error"]),
            ),
        ]
        if SKIP_TICKETS_UPDATED_WITHIN_HOURS > 0:
            open_conditions.append(
                or_(
                    ExpenseReport.updated_at.is_(None),
                    ExpenseReport.updated_at < cutoff,
                )
            )

        logger.info(
            "Tocino CFDI fallback sync started | event=cron_sync_started skip_recent_hours=%s max_open_tickets_per_run=%s",
            SKIP_TICKETS_UPDATED_WITHIN_HOURS,
            MAX_OPEN_TICKETS_PER_RUN,
            extra={
            "event": "cron_sync_started",
            "skip_recent_hours": SKIP_TICKETS_UPDATED_WITHIN_HOURS,
            "max_open_tickets_per_run": MAX_OPEN_TICKETS_PER_RUN,
            },
        )
        stmt = (
            select(ExpenseReport)
            .where(*open_conditions)
            .order_by(ExpenseReport.updated_at.asc())
            .limit(MAX_OPEN_TICKETS_PER_RUN)
        )
        result = await session.execute(stmt)
        expenses = result.scalars().all()
    except Exception:
        await session.rollback()
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": TOCINO_SYNC_ADVISORY_LOCK_KEY})
        await session.commit()
        logger.exception("Tocino CFDI sync: error loading open tickets")
        raise HTTPException(status_code=500, detail="Unexpected processing error")

    processed = 0
    errors: list[str] = []
    loop = asyncio.get_event_loop()

    try:
        client = get_tocino_client()
        for gasto in expenses:
            nova_request_id = gasto.nova_request_id
            if not nova_request_id:
                continue
            try:
                # Sync Tocino API call in executor to avoid blocking event loop
                status_data = await loop.run_in_executor(
                    None, lambda n=nova_request_id: client.check_invoice_status(n)
                )
                payload = tocino_status_response_to_webhook_payload(status_data)
                await apply_tocino_payload_to_db(session, payload)
                processed += 1
            except TocinoAPIError as e:
                errors.append(f"{nova_request_id}: {e}")
                logger.warning("Tocino API error during sync", extra={"event": "cron_sync_error", "nova_request_id": nova_request_id, "error": str(e)})
            except Exception:
                await session.rollback()
                errors.append(f"{nova_request_id}: unexpected processing error")
                logger.exception(
                    "Error applying Tocino payload during sync",
                    extra={"event": "cron_sync_error", "nova_request_id": nova_request_id},
                )

            await asyncio.sleep(DELAY_BETWEEN_API_CALLS_SEC)
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": TOCINO_SYNC_ADVISORY_LOCK_KEY})
        await session.commit()

    logger.info(
        "Tocino CFDI sync completed | event=cron_sync_completed processed=%s total_open=%s errors=%s",
        processed,
        len(expenses),
        len(errors),
        extra={
        "event": "cron_sync_completed",
        "processed": processed,
        "total_open": len(expenses),
        "errors": len(errors),
        },
    )
    return {
        "status": "success",
        "processed": processed,
        "total_open": len(expenses),
        "errors": errors[:10],
    }
