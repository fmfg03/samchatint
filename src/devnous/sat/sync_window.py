"""Date-window and cursor helpers for SAT scheduled sync."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

CDMX = ZoneInfo("America/Mexico_City")

JUNE_2026_BACKFILL_START = datetime(2026, 6, 1, 0, 0, 0, tzinfo=CDMX)
JUNE_2026_BACKFILL_END = datetime(2026, 6, 30, 23, 59, 59, tzinfo=CDMX)
FORWARD_FLOOR_CDMX = datetime(2026, 7, 1, 0, 0, 0, tzinfo=CDMX)

MIN_WINDOW_SECONDS = 2


def sat_sync_overlap_hours() -> int:
    raw = os.getenv("SAT_SYNC_OVERLAP_HOURS", "24").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 24


def now_cdmx() -> datetime:
    return datetime.now(CDMX)


def to_cdmx(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=CDMX)
    return value.astimezone(CDMX)


def to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


@dataclass(frozen=True)
class SyncWindow:
    fecha_inicial: datetime
    fecha_final: datetime
    mode: str

    def as_sat_strings(self) -> tuple[str, str]:
        start = to_cdmx(self.fecha_inicial)
        end = to_cdmx(self.fecha_final)
        return (
            start.strftime("%Y-%m-%dT%H:%M:%S"),
            end.strftime("%Y-%m-%dT%H:%M:%S"),
        )


def ensure_min_window(
    fecha_inicial: datetime,
    fecha_final: datetime,
) -> tuple[datetime, datetime]:
    """SAT requires fecha_inicial strictly before fecha_final (>= 2s span)."""
    start = to_cdmx(fecha_inicial)
    end = to_cdmx(fecha_final)
    if end <= start:
        end = start + timedelta(seconds=MIN_WINDOW_SECONDS)
    elif (end - start).total_seconds() < MIN_WINDOW_SECONDS:
        end = start + timedelta(seconds=MIN_WINDOW_SECONDS)
    return start, end


def june_2026_backfill_window() -> SyncWindow:
    start, end = ensure_min_window(JUNE_2026_BACKFILL_START, JUNE_2026_BACKFILL_END)
    return SyncWindow(fecha_inicial=start, fecha_final=end, mode="june_2026_backfill")


def compute_forward_window(
    *,
    cursor_fecha_emision: Optional[datetime],
    forward_floor_fecha_emision: Optional[datetime],
    overlap_hours: Optional[int] = None,
    now: Optional[datetime] = None,
) -> SyncWindow:
    """Build daily forward window from high-water emission cursor."""
    current = now or now_cdmx()
    overlap = timedelta(hours=overlap_hours if overlap_hours is not None else sat_sync_overlap_hours())

    floor = to_cdmx(forward_floor_fecha_emision) or FORWARD_FLOOR_CDMX
    cursor = to_cdmx(cursor_fecha_emision) or floor

    fecha_inicial = max(floor, cursor - overlap)
    fecha_final = current
    start, end = ensure_min_window(fecha_inicial, fecha_final)
    return SyncWindow(fecha_inicial=start, fecha_final=end, mode="daily_forward")


def naive_cdmx(value: datetime) -> datetime:
    """Persist CDMX wall-clock components without tzinfo (matches SAT string semantics)."""
    return to_cdmx(value).replace(tzinfo=None)


def june_backfill_completion_cursors(
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime, datetime]:
    """Return (cursor_fecha_emision, forward_floor, completed_at) after June backfill."""
    completed_at = to_naive_utc(now or now_cdmx())
    return (
        naive_cdmx(JUNE_2026_BACKFILL_END),
        naive_cdmx(FORWARD_FLOOR_CDMX),
        completed_at,
    )
