"""Payment schedule rules for solicitud de transferencia (Fecha de pago)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from ..utils.mexico_city_dates import MEXICO_CITY_TZ

_END_OF_DAY = time(23, 59, 59)


def last_business_day_of_month(d: date) -> date:
    """Return the last non-weekend day of the calendar month containing *d*."""
    if d.month == 12:
        last_day = date(d.year, 12, 31)
    else:
        last_day = date(d.year, d.month + 1, 1) - timedelta(days=1)
    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)
    return last_day


def previous_business_day(d: date) -> date:
    """Return the business day immediately before *d*."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def friday_of_iso_week(d: date) -> date:
    """Return the Friday of the ISO week (Mon–Sun) containing *d*."""
    monday = d - timedelta(days=d.weekday())
    return monday + timedelta(days=4)


def next_friday_on_or_after(d: date) -> date:
    """Return the next Friday on or after *d* (including *d* when it is Friday)."""
    days_ahead = (4 - d.weekday()) % 7
    return d + timedelta(days=days_ahead)


def _to_cdmx_datetime(approved_at: datetime) -> datetime:
    dt = approved_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MEXICO_CITY_TZ)


def _end_of_day_cdmx(d: date) -> datetime:
    return datetime.combine(d, _END_OF_DAY, tzinfo=MEXICO_CITY_TZ)


def _qualifies_for_month_end_payment(cdmx_dt: datetime) -> bool:
    approval_date = cdmx_dt.date()
    month_end_pay = last_business_day_of_month(approval_date)
    deadline_day = previous_business_day(month_end_pay)
    return cdmx_dt <= _end_of_day_cdmx(deadline_day)


def _friday_payment_date(cdmx_dt: datetime) -> date:
    approval_date = cdmx_dt.date()
    monday = approval_date - timedelta(days=approval_date.weekday())
    wednesday = monday + timedelta(days=2)
    friday_this_week = monday + timedelta(days=4)
    if cdmx_dt <= _end_of_day_cdmx(wednesday):
        return friday_this_week
    return friday_this_week + timedelta(days=7)


def compute_fecha_pago(approved_at: datetime) -> date:
    """Map approval timestamp to payment date per client policy (CDMX cutoffs).

    When both the Friday run and the month-end run qualify, use the earliest date.
    """
    cdmx_dt = _to_cdmx_datetime(approved_at)
    candidates = [_friday_payment_date(cdmx_dt)]
    if _qualifies_for_month_end_payment(cdmx_dt):
        candidates.append(last_business_day_of_month(cdmx_dt.date()))
    return min(candidates)


def compute_urgent_fecha_pago(approved_at: datetime) -> date:
    """Return same-day CDMX payment date for urgent approved requests."""
    return _to_cdmx_datetime(approved_at).date()


def assign_fecha_pago_on_solicitud_approval(documento: Any) -> None:
    """Persist fecha_pago when a SOLICITUD is approved."""
    if getattr(documento, "tipo", None) != "SOLICITUD":
        return
    approved_at = getattr(documento, "aprobado_en", None)
    if approved_at is None:
        return
    if bool(getattr(documento, "pago_urgente", False)):
        documento.fecha_pago = compute_urgent_fecha_pago(approved_at)
        return
    documento.fecha_pago = compute_fecha_pago(approved_at)


def ensure_fecha_pago_for_approved_solicitud(documento: Any) -> bool:
    """Backfill fecha_pago for approved SOLICITUD rows that missed assignment.

    Returns True when fecha_pago was computed and should be committed.
    """
    if getattr(documento, "tipo", None) != "SOLICITUD":
        return False
    if getattr(documento, "estado", None) != "aprobado":
        return False
    if getattr(documento, "fecha_pago", None) is not None:
        return False
    if getattr(documento, "aprobado_en", None) is None:
        return False
    assign_fecha_pago_on_solicitud_approval(documento)
    return documento.fecha_pago is not None


def preview_fecha_pago_for_now(now: Optional[datetime] = None) -> date:
    """Non-binding preview: payment date if approved at *now* (defaults to CDMX now)."""
    if now is None:
        now = datetime.now(MEXICO_CITY_TZ)
    return compute_fecha_pago(now)
