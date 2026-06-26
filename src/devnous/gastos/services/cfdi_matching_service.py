from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import CFDIReport, ExpenseReport
from .cfdi_batch1_status_service import evaluate_ar_status, evaluate_three_way_match


def _expense_matching_snapshot(expense: ExpenseReport) -> Dict[str, Any]:
    empleado = getattr(expense, "empleado", None)
    cfdi = getattr(expense, "cfdi_report", None)
    ar_status = evaluate_ar_status(expense, cfdi=cfdi)
    match_status = evaluate_three_way_match(expense, cfdi=cfdi)
    return {
        "expense_id": str(expense.id),
        "numero_referencia": expense.numero_referencia,
        "fecha": expense.fecha.isoformat() if expense.fecha else None,
        "empleado_nombre": getattr(empleado, "nombre", None),
        "concepto": expense.concepto,
        "gasto_cantidad": round(float(expense.gasto_cantidad or 0), 2),
        "cfdi_uuid_manual": expense.cfdi_uuid_manual,
        "estado_factura": expense.estado_factura,
        "cfdi_report_id": str(expense.cfdi_report_id) if expense.cfdi_report_id else None,
        "cfdi_uuid": getattr(cfdi, "cfdi_uuid", None),
        "cfdi_total": (
            round(float(cfdi.total or 0), 2)
            if cfdi is not None and cfdi.total is not None
            else None
        ),
        "ar_status": ar_status.status,
        "ar_next_action": ar_status.next_action,
        "three_way_match_status": match_status.status,
        "three_way_match_exceptions": match_status.exceptions,
    }


def _cfdi_unlinked_snapshot(cfdi: CFDIReport) -> Dict[str, Any]:
    return {
        "cfdi_report_id": str(cfdi.id),
        "cfdi_uuid": cfdi.cfdi_uuid,
        "fecha": cfdi.fecha.isoformat() if cfdi.fecha else None,
        "origen": cfdi.origen,
        "emisor_nombre": cfdi.emisor_nombre,
        "receptor_nombre": cfdi.receptor_nombre,
        "total": round(float(cfdi.total or 0), 2) if cfdi.total is not None else None,
        "serie": cfdi.serie,
        "folio": cfdi.folio,
    }


async def get_cfdi_matching_overview(
    session: AsyncSession,
    *,
    view: str | None = None,
    limit: int = 100,
) -> Dict[str, Any]:
    normalized_view = (view or "").strip().lower()
    if normalized_view not in {"", "pendiente", "vinculado", "sin_gasto"}:
        raise ValueError("view must be one of: pendiente, vinculado, sin_gasto")

    capped_limit = max(1, min(int(limit or 100), 500))
    show_pending = normalized_view in {"", "pendiente"}
    show_linked = normalized_view in {"", "vinculado"}
    show_unlinked = normalized_view in {"", "sin_gasto"}

    pending_expenses: List[ExpenseReport] = []
    linked_expenses: List[ExpenseReport] = []
    unlinked_cfdis: List[CFDIReport] = []

    with session.no_autoflush:
        if show_pending:
            pending_result = await session.execute(
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.empleado))
                .where(
                    and_(
                        ExpenseReport.cfdi_uuid_manual.isnot(None),
                        ExpenseReport.cfdi_report_id.is_(None),
                        ExpenseReport.estado_gasto == "activo",
                    )
                )
                .order_by(ExpenseReport.created_at.desc())
                .limit(capped_limit)
            )
            pending_expenses = pending_result.scalars().all()

        if show_linked:
            linked_result = await session.execute(
                select(ExpenseReport)
                .options(selectinload(ExpenseReport.empleado))
                .options(selectinload(ExpenseReport.cfdi_report))
                .where(
                    and_(
                        ExpenseReport.cfdi_report_id.isnot(None),
                        ExpenseReport.estado_gasto == "activo",
                    )
                )
                .order_by(ExpenseReport.created_at.desc())
                .limit(capped_limit)
            )
            linked_expenses = linked_result.scalars().all()

        if show_unlinked:
            linked_cfdi_ids_subquery = (
                select(ExpenseReport.cfdi_report_id)
                .where(ExpenseReport.cfdi_report_id.isnot(None))
                .distinct()
            )
            unlinked_result = await session.execute(
                select(CFDIReport)
                .where(~CFDIReport.id.in_(linked_cfdi_ids_subquery))
                .order_by(CFDIReport.created_at.desc())
                .limit(capped_limit)
            )
            unlinked_cfdis = unlinked_result.scalars().all()

    pending_rows = [_expense_matching_snapshot(expense) for expense in pending_expenses]
    linked_rows = [_expense_matching_snapshot(expense) for expense in linked_expenses]
    unlinked_rows = [_cfdi_unlinked_snapshot(cfdi) for cfdi in unlinked_cfdis]

    return {
        "view": normalized_view or "all",
        "limit": capped_limit,
        "summary": {
            "pending_count": len(pending_rows),
            "linked_count": len(linked_rows),
            "unlinked_cfdi_count": len(unlinked_rows),
        },
        "pending_expenses": pending_rows,
        "linked_expenses": linked_rows,
        "unlinked_cfdis": unlinked_rows,
    }
