"""
Worker for updating invoice statuses from Tocino AI API.

Polls Tocino API to check invoice status and syncs data between
invoice_reports and expense_reports tables.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ExpenseReport, InvoiceReport
from ..services.tocino_client import get_tocino_client, TocinoAPIError

logger = logging.getLogger(__name__)


def _extract_invoice_links(status_data: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extract PDF/XML links from Tocino GET response invoice payload."""
    invoice = status_data.get("invoice")
    if not isinstance(invoice, dict):
        return None, None

    # New format in Tocino GET response
    link_pdf = invoice.get("pdf") if isinstance(invoice.get("pdf"), str) else None
    link_xml = invoice.get("xml") if isinstance(invoice.get("xml"), str) else None

    # Legacy format kept for backward compatibility
    pdf_attachment = invoice.get("pdf_attachment") or {}
    xml_attachment = invoice.get("xml_attachment") or {}

    if not link_pdf and isinstance(pdf_attachment, dict):
        link_pdf = pdf_attachment.get("file")
    if not link_xml and isinstance(xml_attachment, dict):
        link_xml = xml_attachment.get("file")
    return link_pdf, link_xml


def _map_tocino_status_for_sync(status_data: Dict[str, Any]) -> tuple[Optional[str], Optional[str], bool, bool]:
    """Map Tocino status to local estado_factura and optional user-facing error."""
    tocino_status = str(status_data.get("status", "") or "").strip()
    normalized = tocino_status.lower().replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if normalized == "no facurable":
        normalized = "no facturable"

    in_process_statuses = {
        "facturando",  # Tocino current docs
        "enviando",
        "leyendo",
        "leído",
        "leido",
        "editando",
        "facturada",
        # Legacy English statuses kept for backward compatibility
        "waiting",
        "processing",
        "success",
        "invoicing",
        "invoiced",
        "email_sent",
        "whatsapp_sent",
    }

    if normalized in {"finalizado", "finalized"}:
        return "completada", None, True, False
    if normalized in {"no facturable", "not invoiceable", "error", "mantenimiento"}:
        error_info = (
            status_data.get("not_invoiceable_cause")
            or status_data.get("error_msg")
            or status_data.get("error_code")
            or status_data.get("exception_error")
            or status_data.get("error")
            or status_data.get("message")
            or status_data.get("detail")
        )
        default_error = "No facturable: Restricciones del comercio o del SAT"
        return "error", f"Error: {error_info}" if error_info else default_error, False, True
    if normalized in in_process_statuses:
        return "en_proceso", None, False, False
    return None, None, False, False


async def update_invoice_statuses(session: AsyncSession) -> Dict[str, Any]:
    """Mirror Tocino statuses into invoice_reports and sync snapshot into expense_reports.

    Strategy:
    - Select expenses that are CFDI (tipo_gasto='ticket') and have a nova_request_id.
    - Fetch status from Tocino.
    - Upsert into invoice_reports (mirror table).
    - Update snapshot fields in expense_reports (estado_factura, links, mensaje_error, updated_at).
    """

    gastos_stmt = select(ExpenseReport).where(
        ExpenseReport.tipo_gasto == "ticket",
        ExpenseReport.nova_request_id.isnot(None)
    )
    result = await session.execute(gastos_stmt)
    gastos = result.scalars().all()

    if not gastos:
        logger.info("No CFDI-linked expenses to update")
        return {"updated": 0, "completed": 0, "failed": 0, "errors": []}

    logger.info(f"Checking status for {len(gastos)} CFDI expenses")

    client = get_tocino_client()
    results = {
        "updated": 0,
        "completed": 0,
        "failed": 0,
        "errors": []
    }

    for gasto in gastos:
        if not gasto.nova_request_id:
            logger.warning(f"Expense {gasto.id} has no nova_request_id, skipping")
            continue

        try:
            # Get current status from Tocino
            status_data = client.check_invoice_status(gasto.nova_request_id)
            tocino_status = status_data.get("status", "")

            logger.info(f"Expense {gasto.id} status: {tocino_status}")

            # Map Tocino status to our database status
            was_updated = False
            estado, mensaje_error, is_completed, is_failed = _map_tocino_status_for_sync(status_data)
            link_pdf, link_xml = _extract_invoice_links(status_data)
            if estado:
                was_updated = True
                if is_completed:
                    results["completed"] += 1
                if is_failed:
                    results["failed"] += 1

            if was_updated:
                # Upsert into invoice_reports (mirror)
                invoice_result = await session.execute(
                    select(InvoiceReport).where(InvoiceReport.nova_request_id == gasto.nova_request_id)
                )
                factura = invoice_result.scalar_one_or_none()

                if not factura:
                    factura = InvoiceReport(
                        expense_id=gasto.id,
                        nova_request_id=gasto.nova_request_id,
                        estado_factura=estado or "pendiente",
                        link_pdf=link_pdf,
                        link_xml=link_xml,
                        mensaje_error=mensaje_error,
                    )
                    session.add(factura)
                else:
                    factura.estado_factura = estado or factura.estado_factura
                    factura.link_pdf = link_pdf or factura.link_pdf
                    factura.link_xml = link_xml or factura.link_xml
                    factura.mensaje_error = mensaje_error
                    factura.updated_at = datetime.utcnow()

                # Sync snapshot into expense_reports (master)
                if estado:
                    gasto.estado_factura = estado
                if link_pdf:
                    gasto.link_pdf = link_pdf
                if link_xml:
                    gasto.link_xml = link_xml
                if mensaje_error is not None:
                    gasto.mensaje_error = mensaje_error
                gasto.updated_at = datetime.utcnow()
                session.add(gasto)
                results["updated"] += 1

        except TocinoAPIError as e:
            error_msg = f"Expense {gasto.id}: Tocino API error: {str(e)}"
            logger.error(error_msg)
            results["errors"].append(error_msg)

        except Exception as e:
            error_msg = f"Expense {gasto.id}: Unexpected error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            results["errors"].append(error_msg)

    # Commit all updates
    try:
        await session.commit()
        logger.info(f"Updated {results['updated']} invoices", extra=results)
    except Exception as e:
        logger.error("Error committing invoice updates", extra={"error": str(e)})
        await session.rollback()

    return results


async def update_single_invoice(session: AsyncSession, expense_id: str) -> Optional[Dict[str, Any]]:
    """Update status for a single invoice by expense ID."""

    result = await session.execute(
        select(ExpenseReport).where(ExpenseReport.id == expense_id)
    )
    expense = result.scalar_one_or_none()

    if not expense:
        logger.warning(f"Expense {expense_id} not found")
        return None

    if not expense.nova_request_id:
        logger.warning(f"Expense {expense_id} has no nova_request_id")
        return None

    try:
        # Get current status from Tocino
        client = get_tocino_client()
        status_data = client.check_invoice_status(expense.nova_request_id)
        tocino_status = status_data.get("status", "")

        logger.info(f"Expense {expense_id} status: {tocino_status}")

        # Map Tocino status to our database status
        estado, mensaje_error, _, _ = _map_tocino_status_for_sync(status_data)
        link_pdf, link_xml = _extract_invoice_links(status_data)
        if estado:
            expense.estado_factura = estado
        if link_pdf:
            expense.link_pdf = link_pdf
        if link_xml:
            expense.link_xml = link_xml
        if mensaje_error is not None:
            expense.mensaje_error = mensaje_error

        expense.updated_at = datetime.utcnow()
        session.add(expense)
        await session.commit()

        return {"status": "success", "expense_id": expense_id, "tocino_status": tocino_status}

    except TocinoAPIError as e:
        logger.error(f"Expense {expense_id}: Tocino API error", extra={"error": str(e)})
        await session.rollback()
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.error(f"Expense {expense_id}: Unexpected error", extra={"error": str(e)})
        await session.rollback()
        return {"status": "error", "message": str(e)}
