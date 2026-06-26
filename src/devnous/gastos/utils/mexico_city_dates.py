"""Mexico City (CDMX) calendar-date helpers for gastos surfaces."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional, Union

from zoneinfo import ZoneInfo

MEXICO_CITY_TZ = ZoneInfo("America/Mexico_City")


def today_mexico_city() -> date:
    """Return today's calendar date in Mexico City (CDMX)."""
    return datetime.now(MEXICO_CITY_TZ).date()


def to_mexico_city_date(
    value: Optional[Union[date, datetime, Any]],
) -> Optional[date]:
    """Normalize date/datetime values to a CDMX calendar date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MEXICO_CITY_TZ).date()
    if isinstance(value, date):
        return value
    return None


def to_mexico_city_datetime(
    value: Optional[Union[datetime, Any]],
) -> Optional[datetime]:
    """Normalize datetime values to Mexico City local time."""
    if value is None or not isinstance(value, datetime):
        return None
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MEXICO_CITY_TZ)


def to_mexico_city_date_iso(
    value: Optional[Union[date, datetime, Any]],
) -> str:
    """Format a value as YYYY-MM-DD in CDMX."""
    mexico_date = to_mexico_city_date(value)
    return mexico_date.strftime("%Y-%m-%d") if mexico_date else ""
