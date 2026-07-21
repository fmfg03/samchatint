"""Quota-aware scheduled SAT CFDI sync orchestration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import CFDIReport
from devnous.sat.config_handler import SATConfigHandler, SATCredentials
from devnous.sat.sat_handler import SATExpenseHandler
from devnous.sat.sat_sync_health import reclaim_stuck_solicitudes
from devnous.sat.sync_models import SATDownloadRequest, SATSyncRun, SATSyncState
from devnous.sat.sync_window import (
    compute_forward_window,
    june_2026_backfill_window,
    june_backfill_completion_cursors,
    naive_cdmx,
)

OPEN_ESTADOS = {"Aceptada", "En Proceso", "En proceso", "aceptada", "en proceso"}
FINAL_SUCCESS = {"Terminada", "terminada"}
FINAL_FAILED_ESTADOS = {"Error", "error", "Rechazada", "rechazada", "Vencida", "vencida"}
QUOTA_BLOCK_HOURS = int(os.getenv("SAT_SYNC_QUOTA_BLOCK_HOURS", "24"))
RATE_LIMIT_BACKOFF_MINUTES = int(os.getenv("SAT_SYNC_RATE_LIMIT_MINUTES", "30"))

logger = logging.getLogger(__name__)


@dataclass
class DirectionSyncResult:
    rfc: str
    direction: str
    mode: str
    status: str
    message: str = ""
    window: Optional[Dict[str, str]] = None
    cursor_before: Optional[str] = None
    cursor_after: Optional[str] = None
    ingested_cfdis: int = 0
    linked_expenses: int = 0
    linked_documentos: int = 0
    solicitud_id: Optional[str] = None
    quota_blocked: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class SyncRunReport:
    mode: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    results: List[DirectionSyncResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "results": [
                {
                    "rfc": row.rfc,
                    "direction": row.direction,
                    "mode": row.mode,
                    "status": row.status,
                    "message": row.message,
                    "window": row.window,
                    "cursor_before": row.cursor_before,
                    "cursor_after": row.cursor_after,
                    "ingested_cfdis": row.ingested_cfdis,
                    "linked_expenses": row.linked_expenses,
                    "linked_documentos": row.linked_documentos,
                    "solicitud_id": row.solicitud_id,
                    "quota_blocked": row.quota_blocked,
                    "warnings": row.warnings,
                }
                for row in self.results
            ],
        }


class SATSyncService:
    """Reuse-first SAT sync with emission-date cursor windows."""

    DIRECTIONS = ("received", "issued")

    def __init__(
        self,
        handler: Optional[SATExpenseHandler] = None,
        config_handler: Optional[SATConfigHandler] = None,
    ):
        self.handler = handler or SATExpenseHandler()
        self.config_handler = config_handler or SATConfigHandler()

    async def run(
        self,
        session: AsyncSession,
        *,
        mode: str = "auto",
    ) -> SyncRunReport:
        normalized = (mode or "auto").strip().lower()
        if normalized not in {"auto", "daily_forward", "june_2026_backfill"}:
            normalized = "auto"

        report = SyncRunReport(mode=normalized, started_at=datetime.utcnow())
        credentials = await self._list_active_credentials(session)
        if not credentials:
            report.results.append(
                DirectionSyncResult(
                    rfc="",
                    direction="",
                    mode=normalized,
                    status="skipped",
                    message="No active SAT credentials configured.",
                )
            )
            report.finished_at = datetime.utcnow()
            return report

        for cred in credentials:
            for direction in self.DIRECTIONS:
                mode_param = normalized
                if normalized == "auto":
                    state = await self._get_or_create_state(
                        session, rfc=cred.rfc, direction=direction
                    )
                    mode_param = await self._resolve_mode_async("auto", state)
                try:
                    row = await self._sync_rfc_direction(
                        session,
                        cred,
                        direction,
                        mode=mode_param,
                    )
                except Exception as exc:
                    logger.exception(
                        "SAT sync failed for %s/%s", cred.rfc, direction, exc_info=True
                    )
                    row = DirectionSyncResult(
                        rfc=cred.rfc,
                        direction=direction,
                        mode=mode_param,
                        status="error",
                        message=str(exc)[:500],
                    )
                report.results.append(row)

        report.finished_at = datetime.utcnow()
        return report

    async def run_open_jobs_only(
        self,
        session: AsyncSession,
    ) -> SyncRunReport:
        """Process only open SAT solicitudes (hourly lightweight cron)."""
        report = SyncRunReport(mode="open_jobs", started_at=datetime.utcnow())
        credentials = await self._list_active_credentials(session)
        if not credentials:
            report.results.append(
                DirectionSyncResult(
                    rfc="",
                    direction="",
                    mode="open_jobs",
                    status="skipped",
                    message="No active SAT credentials configured.",
                )
            )
            report.finished_at = datetime.utcnow()
            return report

        for cred in credentials:
            for direction in self.DIRECTIONS:
                try:
                    reclaimed = await reclaim_stuck_solicitudes(
                        session, rfc=cred.rfc, direction=direction
                    )
                    state = await self._get_or_create_state(
                        session, rfc=cred.rfc, direction=direction
                    )
                    stats = await self._process_open_requests(
                        session, cred, direction, state
                    )
                    if stats.get("pending"):
                        status = "processing"
                        message = "Solicitud still processing at SAT."
                    elif stats.get("success"):
                        status = "success"
                        message = f"Open jobs processed. Reclaimed={reclaimed}."
                    else:
                        status = "error"
                        message = stats.get("message") or "Open job processing failed."
                    report.results.append(
                        DirectionSyncResult(
                            rfc=cred.rfc,
                            direction=direction,
                            mode="open_jobs",
                            status=status,
                            message=message,
                            ingested_cfdis=stats.get("ingested_cfdis", 0),
                            linked_expenses=stats.get("linked_expenses", 0),
                            linked_documentos=stats.get("linked_documentos", 0),
                            warnings=stats.get("warnings", []),
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "SAT open jobs failed for %s/%s", cred.rfc, direction, exc_info=True
                    )
                    report.results.append(
                        DirectionSyncResult(
                            rfc=cred.rfc,
                            direction=direction,
                            mode="open_jobs",
                            status="error",
                            message=str(exc)[:500],
                        )
                    )
        report.finished_at = datetime.utcnow()
        await session.commit()
        return report

    async def _resolve_mode_async(
        self,
        requested: str,
        state: SATSyncState,
    ) -> str:
        if requested in {"daily_forward", "june_2026_backfill"}:
            return requested
        if state.june_2026_backfill_completed_at is None:
            return "june_2026_backfill"
        return "daily_forward"

    async def _list_active_credentials(
        self, session: AsyncSession
    ) -> List[SATCredentials]:
        result = await session.execute(
            select(SATCredentials).where(SATCredentials.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def _get_or_create_state(
        self,
        session: AsyncSession,
        *,
        rfc: str,
        direction: str,
    ) -> SATSyncState:
        result = await session.execute(
            select(SATSyncState).where(
                SATSyncState.rfc == rfc,
                SATSyncState.direction == direction,
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = SATSyncState(rfc=rfc, direction=direction)
            session.add(state)
            await session.flush()
        return state

    async def _sync_rfc_direction(
        self,
        session: AsyncSession,
        credentials: SATCredentials,
        direction: str,
        *,
        mode: str,
    ) -> DirectionSyncResult:
        state = await self._get_or_create_state(
            session, rfc=credentials.rfc, direction=direction
        )
        await reclaim_stuck_solicitudes(
            session, rfc=credentials.rfc, direction=direction
        )
        effective_mode = mode
        cursor_before = state.cursor_fecha_emision.isoformat() if state.cursor_fecha_emision else None

        if state.next_retry_at and state.next_retry_at > datetime.utcnow():
            return DirectionSyncResult(
                rfc=credentials.rfc,
                direction=direction,
                mode=effective_mode,
                status="skipped",
                message=f"Backoff until {state.next_retry_at.isoformat()}",
                cursor_before=cursor_before,
                cursor_after=cursor_before,
            )

        if state.quota_blocked_until and state.quota_blocked_until > datetime.utcnow():
            quota_result = await self._process_open_requests(
                session, credentials, direction, state
            )
            return DirectionSyncResult(
                rfc=credentials.rfc,
                direction=direction,
                mode=effective_mode,
                status="quota_blocked",
                message=state.last_error_message or "SAT quota blocked (5002).",
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                ingested_cfdis=quota_result.get("ingested_cfdis", 0),
                linked_expenses=quota_result.get("linked_expenses", 0),
                linked_documentos=quota_result.get("linked_documentos", 0),
                quota_blocked=True,
                warnings=quota_result.get("warnings", []),
            )

        if effective_mode == "june_2026_backfill":
            window = june_2026_backfill_window()
        else:
            window = compute_forward_window(
                cursor_fecha_emision=state.cursor_fecha_emision,
                forward_floor_fecha_emision=state.forward_floor_fecha_emision,
            )

        window_payload = {
            "fecha_inicial": window.as_sat_strings()[0],
            "fecha_final": window.as_sat_strings()[1],
        }

        open_stats = await self._process_open_requests(
            session, credentials, direction, state
        )

        if await self._window_fully_covered(session, credentials.rfc, direction, window):
            if effective_mode == "june_2026_backfill" and not state.june_2026_backfill_completed_at:
                await self._mark_june_backfill_complete(session, state)
            state.last_successful_sync_at = datetime.utcnow()
            await session.commit()
            return DirectionSyncResult(
                rfc=credentials.rfc,
                direction=direction,
                mode=effective_mode,
                status="success",
                message="Window already covered by completed solicitudes.",
                window=window_payload,
                cursor_before=cursor_before,
                cursor_after=state.cursor_fecha_emision.isoformat() if state.cursor_fecha_emision else None,
                ingested_cfdis=open_stats.get("ingested_cfdis", 0),
                linked_expenses=open_stats.get("linked_expenses", 0),
                linked_documentos=open_stats.get("linked_documentos", 0),
                warnings=open_stats.get("warnings", []),
            )

        create_result = await self._ensure_solicitud_for_window(
            session,
            credentials,
            direction,
            window=window,
            state=state,
        )
        if create_result.get("quota_blocked"):
            await session.commit()
            return DirectionSyncResult(
                rfc=credentials.rfc,
                direction=direction,
                mode=effective_mode,
                status="quota_blocked",
                message=create_result.get("message", "SAT 5002 quota blocked."),
                window=window_payload,
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                ingested_cfdis=open_stats.get("ingested_cfdis", 0),
                quota_blocked=True,
            )

        if (
            create_result.get("status") in {"error", "rate_limited"}
            and not create_result.get("solicitud_id")
        ):
            await session.commit()
            return DirectionSyncResult(
                rfc=credentials.rfc,
                direction=direction,
                mode=effective_mode,
                status="error",
                message=create_result.get("message") or "Could not create SAT solicitud.",
                window=window_payload,
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                ingested_cfdis=open_stats.get("ingested_cfdis", 0),
                warnings=open_stats.get("warnings", []),
            )

        process_stats = await self._process_open_requests(
            session, credentials, direction, state
        )
        total_ingested = open_stats.get("ingested_cfdis", 0) + process_stats.get("ingested_cfdis", 0)
        total_linked_exp = open_stats.get("linked_expenses", 0) + process_stats.get("linked_expenses", 0)
        total_linked_doc = open_stats.get("linked_documentos", 0) + process_stats.get("linked_documentos", 0)
        warnings = open_stats.get("warnings", []) + process_stats.get("warnings", [])
        window_covered = await self._window_fully_covered(
            session, credentials.rfc, direction, window
        )
        made_progress = (
            window_covered
            or total_ingested > 0
            or bool(create_result.get("solicitud_id"))
            or process_stats.get("pending")
        )

        if process_stats.get("success") and not process_stats.get("pending") and made_progress:
            await self._advance_cursors_from_ingest(
                session, state, credentials.rfc, direction
            )
            if effective_mode == "june_2026_backfill" and (
                window_covered or total_ingested > 0
            ):
                await self._mark_june_backfill_complete(session, state)
            state.last_successful_sync_at = datetime.utcnow()
            state.last_error_code = None
            state.last_error_message = None
            state.next_retry_at = None
            await session.commit()
            status = "success"
            message = "Sync completed."
        elif process_stats.get("pending"):
            await session.commit()
            status = "processing"
            message = "Solicitud still processing at SAT."
        elif not made_progress:
            await session.commit()
            status = "error"
            message = (
                create_result.get("message")
                or process_stats.get("message")
                or "No SAT solicitud progress for computed window."
            )
        elif create_result.get("solicitud_id") and create_result.get("status") != "error":
            await session.commit()
            status = "processing"
            message = (
                create_result.get("message")
                or "Solicitud still processing at SAT."
            )
        else:
            await session.commit()
            status = "error"
            message = process_stats.get("message") or create_result.get("message") or "Sync failed."

        return DirectionSyncResult(
            rfc=credentials.rfc,
            direction=direction,
            mode=effective_mode,
            status=status,
            message=message,
            window=window_payload,
            cursor_before=cursor_before,
            cursor_after=state.cursor_fecha_emision.isoformat() if state.cursor_fecha_emision else None,
            ingested_cfdis=total_ingested,
            linked_expenses=total_linked_exp,
            linked_documentos=total_linked_doc,
            solicitud_id=create_result.get("solicitud_id"),
            warnings=warnings,
        )

    async def _mark_june_backfill_complete(
        self, session: AsyncSession, state: SATSyncState
    ) -> None:
        cursor, floor, completed = june_backfill_completion_cursors()
        state.june_2026_backfill_completed_at = completed
        state.cursor_fecha_emision = cursor
        state.forward_floor_fecha_emision = floor

    async def _advance_cursors_from_ingest(
        self,
        session: AsyncSession,
        state: SATSyncState,
        rfc: str,
        direction: str,
    ) -> None:
        conds = [CFDIReport.origen == "sat"]
        if direction == "received":
            conds.append(CFDIReport.receptor_rfc == rfc)
        else:
            conds.append(CFDIReport.emisor_rfc == rfc)

        result = await session.execute(
            select(CFDIReport.fecha, CFDIReport.fecha_timbrado)
            .where(and_(*conds))
            .order_by(CFDIReport.fecha.desc())
            .limit(500)
        )
        rows = result.all()
        max_fecha = state.cursor_fecha_emision
        max_timbrado = state.cursor_fecha_timbrado
        for fecha, timbrado in rows:
            if fecha and (max_fecha is None or fecha > max_fecha):
                max_fecha = fecha
            if timbrado and (max_timbrado is None or timbrado > max_timbrado):
                max_timbrado = timbrado
        if max_fecha is not None:
            state.cursor_fecha_emision = max_fecha
        if max_timbrado is not None:
            state.cursor_fecha_timbrado = max_timbrado

    async def _window_fully_covered(
        self,
        session: AsyncSession,
        rfc: str,
        direction: str,
        window,
    ) -> bool:
        start = naive_cdmx(window.fecha_inicial)
        end = naive_cdmx(window.fecha_final)
        result = await session.execute(
            select(SATDownloadRequest)
            .where(
                SATDownloadRequest.rfc == rfc,
                SATDownloadRequest.direction == direction,
                SATDownloadRequest.is_complete.is_(True),
                func.lower(SATDownloadRequest.estado_sat) == "terminada",
                SATDownloadRequest.fecha_inicial <= start,
                SATDownloadRequest.fecha_final >= end,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _process_open_requests(
        self,
        session: AsyncSession,
        credentials: SATCredentials,
        direction: str,
        state: SATSyncState,
    ) -> Dict[str, Any]:
        result = await session.execute(
            select(SATDownloadRequest)
            .where(
                SATDownloadRequest.rfc == credentials.rfc,
                SATDownloadRequest.direction == direction,
                SATDownloadRequest.is_complete.is_(False),
            )
            .order_by(SATDownloadRequest.created_at.desc())
        )
        jobs = list(result.scalars().all())
        totals = {
            "ingested_cfdis": 0,
            "linked_expenses": 0,
            "linked_documentos": 0,
            "warnings": [],
            "success": True,
            "pending": False,
            "message": "",
        }
        for job in jobs:
            stats = await self._process_job(session, credentials, job, state)
            totals["ingested_cfdis"] += stats.get("ingested_cfdis", 0)
            totals["linked_expenses"] += stats.get("linked_expenses", 0)
            totals["linked_documentos"] += stats.get("linked_documentos", 0)
            totals["warnings"].extend(stats.get("warnings", []))
            if stats.get("pending"):
                totals["pending"] = True
            if stats.get("status") == "error":
                totals["success"] = False
                totals["message"] = stats.get("message", "")
        return totals

    async def _ensure_solicitud_for_window(
        self,
        session: AsyncSession,
        credentials: SATCredentials,
        direction: str,
        *,
        window,
        state: SATSyncState,
    ) -> Dict[str, Any]:
        start = naive_cdmx(window.fecha_inicial)
        end = naive_cdmx(window.fecha_final)

        existing = await session.execute(
            select(SATDownloadRequest)
            .where(
                SATDownloadRequest.rfc == credentials.rfc,
                SATDownloadRequest.direction == direction,
                SATDownloadRequest.fecha_inicial == start,
                SATDownloadRequest.fecha_final == end,
                SATDownloadRequest.is_complete.is_(False),
            )
            .limit(1)
        )
        job = existing.scalar_one_or_none()
        if job and job.solicitud_id:
            return {"solicitud_id": job.solicitud_id, "reused": True}

        filters = self._direction_filters(credentials.rfc, direction)
        create = await self.handler.create_download_request(
            session,
            fecha_inicial=start,
            fecha_final=end,
            rfc=credentials.rfc,
            **filters,
        )
        payload = create.get("result") or {}
        cod = payload.get("cod_estatus") or 0
        mensaje = create.get("message") or payload.get("mensaje") or ""
        solicitud_id = (payload.get("solicitud_id") or "").strip()

        if cod == 5002 or "5002" in mensaje or "agotado" in mensaje.lower():
            state.quota_blocked_until = datetime.utcnow() + timedelta(hours=QUOTA_BLOCK_HOURS)
            state.last_error_code = 5002
            state.last_error_message = mensaje
            return {"quota_blocked": True, "message": mensaje}

        if cod in {5003, 5011}:
            state.next_retry_at = datetime.utcnow() + timedelta(minutes=RATE_LIMIT_BACKOFF_MINUTES)
            state.last_error_code = cod
            state.last_error_message = mensaje
            return {"status": "rate_limited", "message": mensaje}

        if cod == 5005 and solicitud_id:
            pass  # duplicate — reuse solicitud_id
        elif create.get("status") != "success" and not solicitud_id:
            state.last_error_code = cod or None
            state.last_error_message = mensaje
            return {"status": "error", "message": mensaje}

        if not job:
            job = SATDownloadRequest(
                rfc=credentials.rfc,
                direction=direction,
                sync_mode=window.mode,
                fecha_inicial=start,
                fecha_final=end,
                solicitud_id=solicitud_id or None,
                estado_sat="solicitud_creada" if solicitud_id else "error",
                last_error_code=cod or None,
                last_error_message=mensaje or None,
            )
            session.add(job)
        else:
            job.solicitud_id = solicitud_id
            job.estado_sat = "solicitud_creada"
            job.last_error_code = cod or None
            job.last_error_message = mensaje or None

        await session.flush()
        return {"solicitud_id": solicitud_id, "job_id": str(job.id)}

    async def _process_job(
        self,
        session: AsyncSession,
        credentials: SATCredentials,
        job: SATDownloadRequest,
        state: SATSyncState,
    ) -> Dict[str, Any]:
        if not job.solicitud_id:
            return {"status": "error", "message": "Missing solicitud_id"}

        processed = await self.handler.process_download_request(
            session,
            solicitud_id=job.solicitud_id,
            rfc=credentials.rfc,
            poll_until_complete=False,
        )
        verification = (processed.get("result") or {}).get("verification") or {}
        estado = verification.get("estado") or ""
        job.estado_sat = estado
        job.last_verified_at = datetime.utcnow()
        reported = verification.get("num_cfdis")
        if reported is not None:
            job.sat_num_cfdis = int(reported or 0)
        job.package_ids = verification.get("paquetes") or job.package_ids or []
        batch_ingested = (processed.get("result") or {}).get("ingested_cfdis") or 0
        batch_linked_exp = (processed.get("result") or {}).get("linked_expenses") or 0
        batch_linked_doc = (processed.get("result") or {}).get("linked_documentos") or 0
        job.ingested_cfdis = (job.ingested_cfdis or 0) + batch_ingested
        job.linked_expenses = (job.linked_expenses or 0) + batch_linked_exp
        job.linked_documentos = (job.linked_documentos or 0) + batch_linked_doc
        job.updated_at = datetime.utcnow()

        packages = (processed.get("result") or {}).get("packages") or []
        downloaded = dict(job.packages_downloaded or {})
        ingested_map = dict(job.packages_ingested or {})
        for pkg in packages:
            pid = pkg.get("package_id")
            if pid:
                downloaded[pid] = datetime.utcnow().isoformat()
                ingested_map[pid] = job.ingested_cfdis
        job.packages_downloaded = downloaded
        job.packages_ingested = ingested_map

        warnings = (processed.get("result") or {}).get("warnings") or []

        if estado in FINAL_FAILED_ESTADOS:
            job.is_complete = True
            job.last_error_message = (
                processed.get("message") or f"SAT solicitud en estado {estado}"
            )[:2000]
            return {
                "status": "error",
                "message": job.last_error_message,
                "warnings": warnings,
            }

        if verification.get("verify_failed"):
            job.last_error_code = verification.get("cod_estatus")
            job.last_error_message = (verification.get("mensaje") or "Verify failed")[:2000]
            return {
                "status": "pending",
                "pending": True,
                "warnings": warnings,
            }

        if processed.get("status") in {"success", "warning"} and estado in FINAL_SUCCESS:
            job.is_complete = True
            if (
                job.sat_num_cfdis
                and job.sat_num_cfdis > 0
                and job.ingested_cfdis < job.sat_num_cfdis
            ):
                warnings = list(warnings) + [
                    f"SAT reportó {job.sat_num_cfdis} CFDIs pero se ingerieron {job.ingested_cfdis}."
                ]
            return {
                "status": "success",
                "ingested_cfdis": batch_ingested,
                "linked_expenses": batch_linked_exp,
                "linked_documentos": batch_linked_doc,
                "warnings": warnings,
            }
        if processed.get("status") == "processing" or estado in OPEN_ESTADOS:
            return {"status": "pending", "pending": True, "warnings": warnings}

        job.last_error_message = processed.get("message")
        return {
            "status": "error",
            "message": processed.get("message"),
            "warnings": warnings,
        }

    @staticmethod
    def _direction_filters(rfc: str, direction: str) -> Dict[str, Optional[str]]:
        if direction == "received":
            return {"rfc_receptor": rfc, "rfc_emisor": None}
        return {"rfc_emisor": rfc, "rfc_receptor": None}

    async def get_sync_states(
        self, session: AsyncSession, rfc: Optional[str] = None
    ) -> List[SATSyncState]:
        query = select(SATSyncState)
        if rfc:
            query = query.where(SATSyncState.rfc == rfc)
        result = await session.execute(query.order_by(SATSyncState.rfc, SATSyncState.direction))
        return list(result.scalars().all())

    async def preview_next_window(
        self, state: SATSyncState
    ) -> Dict[str, str]:
        if state.june_2026_backfill_completed_at is None:
            window = june_2026_backfill_window()
        else:
            window = compute_forward_window(
                cursor_fecha_emision=state.cursor_fecha_emision,
                forward_floor_fecha_emision=state.forward_floor_fecha_emision,
            )
        start, end = window.as_sat_strings()
        return {"fecha_inicial": start, "fecha_final": end, "mode": window.mode}

    @staticmethod
    def summarize_run_results(results: List[DirectionSyncResult]) -> tuple[str, str]:
        """Return aggregate (status, summary_message) for a multi-direction run."""
        if not results:
            return "empty", "Sin resultados de sync."
        statuses = [str(row.status or "").lower() for row in results]
        if any(status in {"error", "quota_blocked"} for status in statuses):
            aggregate = "error"
        elif any(status == "processing" for status in statuses):
            aggregate = "processing"
        elif any(status == "skipped" for status in statuses):
            aggregate = "skipped"
        elif all(status == "success" for status in statuses):
            aggregate = "success"
        else:
            aggregate = statuses[0] or "unknown"
        messages = [
            f"{row.rfc}/{row.direction}: {row.message}"
            for row in results
            if (row.message or "").strip()
        ]
        ingested = sum(int(row.ingested_cfdis or 0) for row in results)
        summary = "; ".join(messages[:3]) if messages else "Sync completado."
        if ingested:
            summary = f"{summary} ({ingested} CFDI ingeridos)".strip()
        return aggregate, summary[:2000]

    async def persist_sync_run(
        self,
        session: AsyncSession,
        *,
        mode: str,
        trigger_source: str,
        started_at: datetime,
        finished_at: Optional[datetime],
        status: str,
        summary_message: str = "",
        results: Optional[List[DirectionSyncResult]] = None,
        report: Optional[SyncRunReport] = None,
        http_status: Optional[int] = None,
    ) -> SATSyncRun:
        payload_results = None
        if report is not None:
            payload_results = report.to_dict().get("results")
        elif results is not None:
            payload_results = [
                {
                    "rfc": row.rfc,
                    "direction": row.direction,
                    "mode": row.mode,
                    "status": row.status,
                    "message": row.message,
                    "window": row.window,
                    "cursor_before": row.cursor_before,
                    "cursor_after": row.cursor_after,
                    "ingested_cfdis": row.ingested_cfdis,
                    "linked_expenses": row.linked_expenses,
                    "linked_documentos": row.linked_documentos,
                    "solicitud_id": row.solicitud_id,
                    "quota_blocked": row.quota_blocked,
                    "warnings": row.warnings,
                }
                for row in results
            ]
        row = SATSyncRun(
            mode=(mode or "auto").strip().lower(),
            trigger_source=(trigger_source or "api").strip().lower(),
            started_at=started_at,
            finished_at=finished_at,
            status=(status or "unknown").strip().lower(),
            summary_message=(summary_message or "")[:4000] or None,
            results=payload_results,
            http_status=http_status,
        )
        session.add(row)
        await session.flush()
        return row

    async def get_recent_sync_runs(
        self,
        session: AsyncSession,
        *,
        limit: int = 40,
    ) -> List[SATSyncRun]:
        capped = max(1, min(int(limit or 40), 200))
        result = await session.execute(
            select(SATSyncRun)
            .order_by(SATSyncRun.started_at.desc(), SATSyncRun.created_at.desc())
            .limit(capped)
        )
        return list(result.scalars().all())
