"""SAT sync health: open solicitudes, stuck reclaim, and emission-day SLA coverage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import CFDIReport
from devnous.sat.sync_models import SATDownloadRequest, SATSyncState
from devnous.sat.sync_window import now_cdmx, to_cdmx

CDMX_TZ = now_cdmx().tzinfo
FINAL_FAILED_ESTADOS = {"error", "rechazada", "vencida", "reclamada"}
SUCCESS_ESTADO = "terminada"


def sat_solicitud_stuck_hours() -> int:
    raw = os.getenv("SAT_SOLICITUD_STUCK_HOURS", "72").strip()
    try:
        return max(24, int(raw))
    except ValueError:
        return 72


def sat_emission_sla_hours() -> int:
    raw = os.getenv("SAT_EMISSION_SLA_HOURS", "24").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 24


@dataclass(frozen=True)
class OpenSolicitudRow:
    rfc: str
    direction: str
    solicitud_id: str
    estado_sat: str
    age_hours: int
    fecha_inicial: datetime
    fecha_final: datetime
    ingested_cfdis: int
    sat_num_cfdis: Optional[int]
    last_verified_at: Optional[datetime]
    last_error_message: str
    quota_blocked: bool


@dataclass(frozen=True)
class DailySlaRow:
    day: date
    cfdis_in_db: int
    received_covered: bool
    issued_covered: bool
    status: str
    status_style: str


@dataclass(frozen=True)
class DirectionHealthSummary:
    rfc: str
    direction: str
    open_jobs: int
    quota_blocked: bool
    quota_blocked_until: Optional[datetime]
    last_successful_sync_at: Optional[datetime]
    cursor_fecha_emision: Optional[datetime]


def status_style_for(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"ok", "en sync"}:
        return "background:#dcfce7;color:#166534;"
    if normalized in {"parcial", "en proceso"}:
        return "background:#eff6ff;color:#1e40af;"
    if normalized in {"pendiente cobertura", "pendiente descarga", "desfasado", "hueco"}:
        return "background:#fee2e2;color:#991b1b;"
    if normalized in {"sin movimiento"}:
        return "background:#f1f5f9;color:#475569;"
    if normalized in {"revisar", "cuota"}:
        return "background:#fef3c7;color:#92400e;"
    return "background:#e2e8f0;color:#334155;"


def classify_sla_day(
    *,
    day: date,
    today: date,
    now: datetime,
    cfdis_in_db: int,
    received_covered: bool,
    issued_covered: bool,
    has_later_cfdis: bool,
    sla_hours: int,
) -> str:
    del now  # SLA classification uses calendar-day grace derived from sla_hours
    if day > today:
        return "—"
    if day == today:
        return "Parcial"
    if cfdis_in_db > 0 and received_covered and issued_covered:
        return "OK"
    if cfdis_in_db > 0:
        return "OK"
    if received_covered and issued_covered:
        return "OK"

    grace_days = max(1, (sla_hours + 23) // 24)
    if (today - day).days <= grace_days:
        return "Parcial"

    if cfdis_in_db == 0 and has_later_cfdis:
        return "Hueco"
    if not received_covered or not issued_covered:
        return "Pendiente cobertura"
    return "Sin movimiento"


def _normalize_estado(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _rfc_filter(rfc: str):
    rfc_clean = (rfc or "").strip().upper()
    return or_(
        func.upper(CFDIReport.emisor_rfc) == rfc_clean,
        func.upper(CFDIReport.receptor_rfc) == rfc_clean,
    )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = to_cdmx(datetime.combine(day, time(0, 0, 0)))
    end = to_cdmx(datetime.combine(day, time(23, 59, 59)))
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


async def _count_cfdis_for_day(
    session: AsyncSession,
    *,
    rfc: str,
    day: date,
) -> int:
    day_expr = func.date(CFDIReport.fecha)
    result = await session.execute(
        select(func.count(func.distinct(CFDIReport.cfdi_uuid))).where(
            and_(
                CFDIReport.origen == "sat",
                CFDIReport.fecha.isnot(None),
                _rfc_filter(rfc),
                day_expr == day,
            )
        )
    )
    return int(result.scalar_one_or_none() or 0)


async def day_has_successful_coverage(
    session: AsyncSession,
    *,
    rfc: str,
    direction: str,
    day: date,
) -> bool:
    day_start, day_end = _day_bounds(day)
    result = await session.execute(
        select(SATDownloadRequest.id)
        .where(
            SATDownloadRequest.rfc == rfc,
            SATDownloadRequest.direction == direction,
            SATDownloadRequest.is_complete.is_(True),
            func.lower(SATDownloadRequest.estado_sat) == SUCCESS_ESTADO,
            SATDownloadRequest.fecha_inicial <= day_end,
            SATDownloadRequest.fecha_final >= day_start,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_open_solicitudes(
    session: AsyncSession,
    *,
    rfc: Optional[str] = None,
    limit: int = 50,
) -> List[OpenSolicitudRow]:
    query = (
        select(SATDownloadRequest)
        .where(SATDownloadRequest.is_complete.is_(False))
        .order_by(SATDownloadRequest.updated_at.desc())
        .limit(max(1, limit))
    )
    if rfc:
        query = query.where(SATDownloadRequest.rfc == rfc)

    sync_states = await _load_sync_states(session, rfc=rfc)
    quota_map = {
        (st.rfc, st.direction): (
            st.quota_blocked_until and st.quota_blocked_until > datetime.utcnow()
        )
        for st in sync_states
    }

    result = await session.execute(query)
    rows: List[OpenSolicitudRow] = []
    now = datetime.utcnow()
    for job in result.scalars().all():
        created = job.created_at or now
        age_hours = max(0, int((now - created).total_seconds() // 3600))
        rows.append(
            OpenSolicitudRow(
                rfc=job.rfc,
                direction=job.direction,
                solicitud_id=(job.solicitud_id or "—"),
                estado_sat=(job.estado_sat or "—"),
                age_hours=age_hours,
                fecha_inicial=job.fecha_inicial,
                fecha_final=job.fecha_final,
                ingested_cfdis=int(job.ingested_cfdis or 0),
                sat_num_cfdis=getattr(job, "sat_num_cfdis", None),
                last_verified_at=getattr(job, "last_verified_at", None),
                last_error_message=(job.last_error_message or ""),
                quota_blocked=bool(quota_map.get((job.rfc, job.direction))),
            )
        )
    return rows


async def reclaim_stuck_solicitudes(
    session: AsyncSession,
    *,
    rfc: str,
    direction: str,
    stuck_hours: Optional[int] = None,
) -> int:
    """Close failed open solicitudes so sync can recreate windows."""
    threshold = stuck_hours if stuck_hours is not None else sat_solicitud_stuck_hours()
    result = await session.execute(
        select(SATDownloadRequest).where(
            SATDownloadRequest.rfc == rfc,
            SATDownloadRequest.direction == direction,
            SATDownloadRequest.is_complete.is_(False),
        )
    )
    jobs = list(result.scalars().all())
    reclaimed = 0
    now = datetime.utcnow()
    for job in jobs:
        estado = _normalize_estado(job.estado_sat)
        age_hours = 0
        if job.created_at:
            age_hours = (now - job.created_at).total_seconds() / 3600.0
        should_reclaim = estado in FINAL_FAILED_ESTADOS or age_hours >= threshold
        if not should_reclaim:
            continue
        reason = (
            f"Solicitud {job.estado_sat or 'abierta'} reclamada tras {int(age_hours)}h."
            if age_hours >= threshold and estado not in FINAL_FAILED_ESTADOS
            else f"Solicitud SAT en estado final fallido: {job.estado_sat or 'desconocido'}."
        )
        job.is_complete = True
        job.estado_sat = "Reclamada" if estado not in FINAL_FAILED_ESTADOS else job.estado_sat
        job.last_error_message = reason[:2000]
        job.updated_at = now
        reclaimed += 1
    if reclaimed:
        await session.flush()
    return reclaimed


async def get_daily_sla_rows(
    session: AsyncSession,
    *,
    rfc: str,
    days: int = 30,
    today: Optional[date] = None,
) -> List[DailySlaRow]:
    rfc_clean = (rfc or "").strip()
    if not rfc_clean:
        return []

    today_cdmx = today or now_cdmx().date()
    now = now_cdmx()
    span = max(1, int(days))
    start_day = today_cdmx - timedelta(days=span - 1)
    sla_hours = sat_emission_sla_hours()

    cfdi_counts: dict[date, int] = {}
    for offset in range(span):
        day = today_cdmx - timedelta(days=offset)
        cfdi_counts[day] = await _count_cfdis_for_day(session, rfc=rfc_clean, day=day)

    rows: List[DailySlaRow] = []
    for offset in range(span):
        day = today_cdmx - timedelta(days=offset)
        cfdis = cfdi_counts.get(day, 0)
        received = await day_has_successful_coverage(
            session, rfc=rfc_clean, direction="received", day=day
        )
        issued = await day_has_successful_coverage(
            session, rfc=rfc_clean, direction="issued", day=day
        )
        has_later = any(
            cfdi_counts.get(today_cdmx - timedelta(days=later_offset), 0) > 0
            for later_offset in range(offset)
        )
        status = classify_sla_day(
            day=day,
            today=today_cdmx,
            now=now,
            cfdis_in_db=cfdis,
            received_covered=received,
            issued_covered=issued,
            has_later_cfdis=has_later,
            sla_hours=sla_hours,
        )
        rows.append(
            DailySlaRow(
                day=day,
                cfdis_in_db=cfdis,
                received_covered=received,
                issued_covered=issued,
                status=status,
                status_style=status_style_for(status),
            )
        )
    return rows


async def get_direction_health_summaries(
    session: AsyncSession,
    *,
    rfc: Optional[str] = None,
) -> List[DirectionHealthSummary]:
    states = await _load_sync_states(session, rfc=rfc)
    now = datetime.utcnow()
    summaries: List[DirectionHealthSummary] = []
    for st in states:
        open_result = await session.execute(
            select(func.count())
            .select_from(SATDownloadRequest)
            .where(
                SATDownloadRequest.rfc == st.rfc,
                SATDownloadRequest.direction == st.direction,
                SATDownloadRequest.is_complete.is_(False),
            )
        )
        open_jobs = int(open_result.scalar_one() or 0)
        summaries.append(
            DirectionHealthSummary(
                rfc=st.rfc,
                direction=st.direction,
                open_jobs=open_jobs,
                quota_blocked=bool(
                    st.quota_blocked_until and st.quota_blocked_until > now
                ),
                quota_blocked_until=st.quota_blocked_until,
                last_successful_sync_at=st.last_successful_sync_at,
                cursor_fecha_emision=st.cursor_fecha_emision,
            )
        )
    return summaries


async def _load_sync_states(
    session: AsyncSession,
    *,
    rfc: Optional[str],
) -> Sequence[SATSyncState]:
    query = select(SATSyncState)
    if rfc:
        query = query.where(SATSyncState.rfc == rfc)
    result = await session.execute(query.order_by(SATSyncState.rfc, SATSyncState.direction))
    return list(result.scalars().all())
