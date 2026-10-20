"""Scoped, bounded business search; candidates never create a fiscal application."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from ..models import CFDIReport, ExpenseReport
from .amex_direct_common import DirectConflict, digest, load_items, load_statement
from .amex_direct_effective_source_service import effective_source_for_item
from .amex_direct_matching_service import (
    _available,
    cfdi_identity,
    current_status_receipt,
)
from .amex_direct_navigation import save_navigation_cursor

CURSOR_KEY = "amex_direct_search_cursors"


async def _search_direct_cfdis(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    navigation: dict,
    q: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    amount: Decimal | None = None,
    cursor: str | None = None,
    page_size: int = 25,
) -> dict[str, Any]:
    """Authorize first; session binds opaque cursors to scoped filters."""
    statement, state, context, _ = await load_statement(
        session, actor_id, import_id, "conciliar", finance=True
    )
    if context is None:
        raise DirectConflict("contexto_fiscal_pendiente")
    items = await load_items(session, import_id)
    item = next((row for row in items if row.id == item_id), None)
    if item is None:
        raise DirectConflict("item_context_mismatch")
    source = await effective_source_for_item(session, item)
    if source.movement_kind != "debit":
        raise DirectConflict("item_context_mismatch")
    expense = await session.get(ExpenseReport, source.imported_expense_id)
    if expense is None:
        raise DirectConflict("fiscal_source_missing")
    q = q.strip()
    if len(q) > 120 or not 1 <= page_size <= 50:
        raise DirectConflict("invalid_cfdi_search")
    if date_from and date_to and date_from > date_to:
        raise DirectConflict("invalid_cfdi_search")
    if amount is not None:
        try:
            if not amount.is_finite() or amount < 0:
                raise InvalidOperation
        except (AttributeError, InvalidOperation):
            raise DirectConflict("invalid_cfdi_search") from None
    fingerprint = digest(
        [
            str(actor_id),
            str(import_id),
            str(item_id),
            str(context.id),
            context.version,
            state.source_version,
            item.source_version,
            q,
            date_from,
            date_to,
            amount,
            page_size,
        ]
    )
    query = select(CFDIReport).where(
        CFDIReport.origen == "sat",
        func.upper(CFDIReport.receptor_rfc) == context.receiver_rfc,
        func.upper(CFDIReport.moneda) == "MXN",
        func.upper(CFDIReport.tipo_de_comprobante) == "I",
    )
    if q:
        literal = (
            "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        query = query.where(
            or_(
                CFDIReport.cfdi_uuid.ilike(literal, escape="\\"),
                CFDIReport.emisor_rfc.ilike(literal, escape="\\"),
                CFDIReport.emisor_nombre.ilike(literal, escape="\\"),
            )
        )
    if date_from:
        query = query.where(CFDIReport.fecha >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.where(CFDIReport.fecha <= datetime.combine(date_to, time.max))
    if amount is not None:
        query = query.where(CFDIReport.total == amount)
    positions = dict(navigation.get(CURSOR_KEY) or {})
    if cursor:
        position = positions.get(cursor)
        if not position or position.get("fingerprint") != fingerprint:
            raise DirectConflict("invalid_cfdi_search_cursor")
        last_id = UUID(position["id"])
        if position["date"] is None:
            query = query.where(CFDIReport.fecha.is_(None), CFDIReport.id > last_id)
        else:
            stamp = datetime.fromisoformat(position["date"])
            query = query.where(
                or_(
                    CFDIReport.fecha > stamp,
                    and_(CFDIReport.fecha == stamp, CFDIReport.id > last_id),
                    CFDIReport.fecha.is_(None),
                )
            )
    records = list(
        (
            await session.execute(
                query.order_by(
                    CFDIReport.fecha.asc().nulls_last(), CFDIReport.id.asc()
                ).limit(page_size + 1)
            )
        )
        .scalars()
        .all()
    )
    examined, has_more = records[:page_size], len(records) > page_size
    from .amex_direct_fiscal_evidence_service import (
        read_direct_cfdi_fiscal_evidence_batch,
    )

    fiscal_evidence = await read_direct_cfdi_fiscal_evidence_batch(
        session,
        examined,
        receiver_rfc=context.receiver_rfc,
        company_code=getattr(context, "company_code", None),
    )
    candidates = []
    for cfdi in examined:
        try:
            identity = cfdi_identity(cfdi)
            if identity["rfc_receptor"] != context.receiver_rfc:
                continue
            receipt = await current_status_receipt(session, cfdi)
            available = await _available(session, cfdi, expense)
        except DirectConflict:
            continue
        candidates.append(
            {
                "id": str(cfdi.id),
                "uuid": identity["uuid"],
                "issuer_rfc": identity["rfc_emisor"],
                "issuer_name": cfdi.emisor_nombre,
                "date": str(cfdi.fecha or ""),
                "total": identity["total"],
                "identity_hash": digest(identity),
                "status": (
                    receipt.observed_status if receipt else "sin vigencia reciente"
                ),
                "observed_at": str(receipt.observed_at) if receipt else None,
                "available": available,
                **fiscal_evidence[str(cfdi.id)],
            }
        )
    next_cursor = None
    if has_more and examined:
        last = examined[-1]
        next_cursor = save_navigation_cursor(
            navigation,
            CURSOR_KEY,
            {
                "fingerprint": fingerprint,
                "id": str(last.id),
                "date": last.fecha.isoformat() if last.fecha else None,
            },
        )
    return {
        "candidates": candidates,
        "has_more": has_more,
        "cursor": next_cursor,
        "examined": len(examined),
        "source_version": state.source_version,
        "item_version": item.source_version,
        "item": item,
        "row_number": source.source_json.get("row_number"),
        "statement": statement,
    }


async def search_direct_cfdis(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    navigation: dict,
    q: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    amount: Decimal | None = None,
    cursor: str | None = None,
    page_size: int = 25,
) -> dict[str, Any]:
    """Preserve manual filters and fiscal evidence without GET autoflush."""
    with session.no_autoflush:
        return await _search_direct_cfdis(
            session,
            actor_id=actor_id,
            import_id=import_id,
            item_id=item_id,
            navigation=navigation,
            q=q,
            date_from=date_from,
            date_to=date_to,
            amount=amount,
            cursor=cursor,
            page_size=page_size,
        )
