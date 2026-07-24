"""Daily SAT ingest coverage by emission date for admin reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import CFDIReport
from devnous.sat.sync_window import now_cdmx

CDMX = ZoneInfo("America/Mexico_City")


@dataclass(frozen=True)
class DailyCoverageRow:
    day: date
    sat_emission_count: int
    ingested_emission_count: int
    status: str
    status_style: str


def classify_day_status(
    *,
    day: date,
    today: date,
    sat_emission_count: int,
    ingested_emission_count: int,
    has_later_emission_activity: bool,
) -> str:
    """Return sync status label for one emission day."""
    if day > today:
        return "—"
    if day == today:
        return "Parcial"
    if sat_emission_count == 0 and ingested_emission_count == 0:
        if has_later_emission_activity:
            return "Desfasado"
        return "Sin movimiento"
    if ingested_emission_count < sat_emission_count:
        return "Desfasado"
    if ingested_emission_count > sat_emission_count:
        return "Revisar"
    return "En sync"


def status_style_for(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "en sync":
        return "background:#dcfce7;color:#166534;"
    if normalized == "desfasado":
        return "background:#fee2e2;color:#991b1b;"
    if normalized == "revisar":
        return "background:#fef3c7;color:#92400e;"
    if normalized == "parcial":
        return "background:#eff6ff;color:#1e40af;"
    if normalized == "sin movimiento":
        return "background:#f1f5f9;color:#475569;"
    return "background:#e2e8f0;color:#334155;"


def _rfc_filter(rfc: str):
    rfc_clean = (rfc or "").strip().upper()
    return or_(
        func.upper(CFDIReport.emisor_rfc) == rfc_clean,
        func.upper(CFDIReport.receptor_rfc) == rfc_clean,
    )


async def _count_by_emission_day(
    session: AsyncSession,
    *,
    rfc: str,
    start_day: date,
    end_day: date,
) -> dict[date, int]:
    day_expr = func.date(CFDIReport.fecha)
    result = await session.execute(
        select(day_expr, func.count(func.distinct(CFDIReport.cfdi_uuid)))
        .where(
            and_(
                CFDIReport.origen == "sat",
                CFDIReport.fecha.isnot(None),
                _rfc_filter(rfc),
                day_expr >= start_day,
                day_expr <= end_day,
            )
        )
        .group_by(day_expr)
        .order_by(day_expr.desc())
    )
    counts: dict[date, int] = {}
    for row_day, total in result.all():
        if row_day is not None:
            counts[row_day] = int(total or 0)
    return counts


async def get_daily_coverage(
    session: AsyncSession,
    *,
    rfc: str,
    days: int = 30,
    today: Optional[date] = None,
) -> List[DailyCoverageRow]:
    """
    Build daily SAT coverage rows keyed by CFDI emission date (fecha).

    Both count columns use emission date for sync-focused reconciliation:
    - sat_emission_count: SAT-origin CFDIs emitted on that day
    - ingested_emission_count: same set (ingested SAT CFDIs for that emission day)
    """
    rfc_clean = (rfc or "").strip()
    if not rfc_clean:
        return []

    today_cdmx = today or now_cdmx().date()
    span = max(1, int(days))
    start_day = today_cdmx - timedelta(days=span - 1)
    end_day = today_cdmx

    emission_counts = await _count_by_emission_day(
        session,
        rfc=rfc_clean,
        start_day=start_day,
        end_day=end_day,
    )

    rows: List[DailyCoverageRow] = []
    for offset in range(span):
        day = end_day - timedelta(days=offset)
        sat_count = emission_counts.get(day, 0)
        ingested_count = sat_count
        has_later = any(
            emission_counts.get(end_day - timedelta(days=later_offset), 0) > 0
            for later_offset in range(offset)
        )
        status = classify_day_status(
            day=day,
            today=today_cdmx,
            sat_emission_count=sat_count,
            ingested_emission_count=ingested_count,
            has_later_emission_activity=has_later,
        )
        rows.append(
            DailyCoverageRow(
                day=day,
                sat_emission_count=sat_count,
                ingested_emission_count=ingested_count,
                status=status,
                status_style=status_style_for(status),
            )
        )
    return rows
