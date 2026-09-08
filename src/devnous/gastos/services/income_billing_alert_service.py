"""Weekly, role-based Telegram alert for expected income not yet invoiced."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .telegram_outbox_service import deliver_telegram_notification


async def send_weekly_unbilled_income_alert(
    session: AsyncSession, *, as_of: date | None = None
) -> dict[str, Any]:
    """Notify configured finance/accounting leadership once per ISO week.

    Recipients are derived from their position and must have a linked Telegram
    chat.  The notification type carries the ISO week, making retries within a
    week idempotent while allowing a fresh alert next week.
    """
    cutoff = as_of or date.today()
    expected = (await session.execute(text("""
        SELECT COALESCE(SUM(p.expected_income_amount), 0) AS amount
        FROM budget_line_monthly_plan p
        JOIN budget_lines l ON l.id = p.budget_line_id
        JOIN budget_versions v ON v.id = l.budget_version_id
        WHERE COALESCE(l.line_direction, 'expense') = 'income'
          AND p.month_number <= :month
          AND v.edition_year = :year
          AND v.status IN ('approved', 'active')
    """), {"month": cutoff.month, "year": cutoff.year})).scalar_one()
    invoiced = (await session.execute(text("""
        SELECT COALESCE(SUM(amount), 0) AS amount
        FROM budget_cfdi_income_links link
        JOIN budget_versions v ON v.id = link.budget_version_id
        WHERE link.status = 'approved' AND link.income_date::date <= :cutoff
          AND v.edition_year = :year AND v.status IN ('approved', 'active')
    """), {"cutoff": cutoff, "year": cutoff.year})).scalar_one()
    pending = max(0.0, float(expected or 0) - float(invoiced or 0))
    iso_year, iso_week, _ = cutoff.isocalendar()
    notification_type = f"income_unbilled_weekly_{iso_year}_{iso_week:02d}"
    recipients = (await session.execute(text("""
        SELECT id, telegram_user_id FROM empleados
        WHERE activo IS TRUE AND telegram_user_id IS NOT NULL
          AND (LOWER(COALESCE(rol, '')) IN ('finanzas', 'contabilidad', 'admin', 'superadmin')
               OR LOWER(COALESCE(departamento, '')) IN ('finanzas', 'contabilidad', 'direccion'))
    """))).mappings().all()
    body = (
        f"Ingresos esperados acumulados al {cutoff.isoformat()}: ${float(expected or 0):,.2f}.\n"
        f"Facturados aprobados: ${float(invoiced or 0):,.2f}.\n"
        f"Pendientes de facturar: ${pending:,.2f}."
    )
    sent = 0
    for recipient in recipients:
        if await deliver_telegram_notification(
            session, notification_type=notification_type,
            header_text="Ingresos esperados sin facturar", text=body,
            chat_id=int(recipient["telegram_user_id"]),
            recipient_empleado_id=recipient["id"],
        ):
            sent += 1
    return {"expected": float(expected or 0), "invoiced": float(invoiced or 0),
            "pending": pending, "recipients": len(recipients), "sent": sent}
