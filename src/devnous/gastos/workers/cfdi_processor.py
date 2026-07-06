"""
CFDI XML Processing Worker

Processes CFDI XML files and extracts data to cfdi_reports table.
Can be triggered automatically when XML link appears or run as backfill.
Uses async sessions for samchat compatibility.
"""

import logging
from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import InvoiceReport, ExpenseReport
from ..services.cfdi_ingestion_service import ingest_cfdi_xml
from ..services.cfdi_parser import download_xml

logger = logging.getLogger(__name__)


async def process_cfdi_xml_async(
    session: AsyncSession,
    nova_request_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process CFDI XML for a specific invoice (async version).

    Can be called with either nova_request_id or numero_referencia.

    Returns:
        Dict with status and details
    """
    # Find invoice
    if nova_request_id:
        result = await session.execute(
            select(InvoiceReport).where(
                InvoiceReport.nova_request_id == nova_request_id
            )
        )
        factura = result.scalar_one_or_none()
    elif numero_referencia:
        # Find via expense report
        expense_result = await session.execute(
            select(ExpenseReport).where(
                ExpenseReport.numero_referencia == numero_referencia
            )
        )
        expense = expense_result.scalar_one_or_none()
        if expense and expense.nova_request_id:
            result = await session.execute(
                select(InvoiceReport).where(
                    InvoiceReport.nova_request_id == expense.nova_request_id
                )
            )
            factura = result.scalar_one_or_none()
        else:
            factura = None
    else:
        return {
            "status": "error",
            "message": "Must provide nova_request_id or numero_referencia",
        }

    if not factura:
        return {"status": "error", "message": "Invoice not found"}

    if not factura.link_xml:
        return {"status": "error", "message": "No XML link available"}

    try:
        logger.info(f"Processing CFDI XML for nova_request_id: {nova_request_id}")
        xml_content = download_xml(factura.link_xml)
        if not xml_content:
            return {"status": "error", "message": "Failed to download CFDI XML"}

        # Get numero_referencia from expense if available
        numero_ref = numero_referencia
        if not numero_ref and factura.expense_id:
            expense_result = await session.execute(
                select(ExpenseReport).where(ExpenseReport.id == factura.expense_id)
            )
            expense = expense_result.scalar_one_or_none()
            if expense:
                numero_ref = expense.numero_referencia

        ingestion = await ingest_cfdi_xml(
            session,
            xml_content,
            source="tocino",
            entity=expense if factura.expense_id else None,
            nova_request_id=factura.nova_request_id,
            numero_referencia=numero_ref,
        )
        await session.commit()

        logger.info(
            f"Processed CFDI XML for invoice {factura.id}, UUID: {ingestion.cfdi_uuid}"
        )

        return {
            "status": (
                "already_processed"
                if ingestion.status in {"reused", "already_linked"}
                else "success"
            ),
            "ingestion_status": ingestion.status,
            "cfdi_uuid": ingestion.cfdi_uuid,
            "nova_request_id": factura.nova_request_id,
            "numero_referencia": numero_ref,
        }

    except Exception as e:
        logger.error(f"Error processing CFDI XML: {e}", exc_info=True)
        await session.rollback()
        return {
            "status": "error",
            "message": "Unexpected CFDI XML processing error",
        }


async def backfill_all_cfdis_async(session: AsyncSession) -> Dict[str, Any]:
    """
    Backfill all existing invoices with XML links (async version).

    Returns dict with processing statistics.
    """
    result = await session.execute(
        select(InvoiceReport).where(InvoiceReport.link_xml.isnot(None))
    )
    facturas = result.scalars().all()

    stats: Dict[str, Any] = {
        "total": len(facturas),
        "processed": 0,
        "failed": 0,
        "already_processed": 0,
        "errors": [],
    }

    logger.info(f"Starting CFDI backfill for {stats['total']} invoices")

    for idx, factura in enumerate(facturas, 1):
        logger.info(
            "Processing %s/%s: nova_request_id=%s",
            idx,
            stats["total"],
            factura.nova_request_id,
        )

        result = await process_cfdi_xml_async(
            session, nova_request_id=factura.nova_request_id
        )

        if result.get("status") == "success":
            stats["processed"] += 1
        elif result.get("status") == "already_processed":
            stats["already_processed"] += 1
        else:
            stats["failed"] += 1
            error_msg = (
                f"{factura.nova_request_id}: {result.get('message', 'Unknown error')}"
            )
            stats["errors"].append(error_msg)
            logger.warning(f"Failed to process: {error_msg}")

    logger.info(f"Backfill complete: {stats}")
    return stats
