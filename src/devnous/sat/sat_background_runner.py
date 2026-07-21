"""Background execution for long-running SAT sync and CFDI download jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.sat.sat_handler import SATExpenseHandler
from devnous.sat.sat_sync_service import SATSyncService
from devnous.sat.sync_models import SATSyncRun

logger = logging.getLogger(__name__)

SAT_SYNC_ADVISORY_LOCK_KEY = 876543210987654

_db_session_maker: Optional[Callable[..., Any]] = None
_spawn_lock = asyncio.Lock()
_active_tasks: set[asyncio.Task] = set()


def set_session_maker(session_maker: Callable[..., Any]) -> None:
    global _db_session_maker
    _db_session_maker = session_maker


def _require_session_maker() -> Callable[..., Any]:
    if _db_session_maker is None:
        raise RuntimeError(
            "SAT background runner session maker not set. "
            "Call set_session_maker() during app startup."
        )
    return _db_session_maker


async def has_running_sat_job(session: AsyncSession) -> bool:
    result = await session.execute(
        select(SATSyncRun.id)
        .where(SATSyncRun.status == "running")
        .order_by(SATSyncRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_sat_job(session: AsyncSession, run_id: UUID) -> Optional[SATSyncRun]:
    return await session.get(SATSyncRun, run_id)


async def create_running_sat_job(
    session: AsyncSession,
    *,
    mode: str,
    trigger_source: str,
    job_meta: Optional[Dict[str, Any]] = None,
) -> SATSyncRun:
    started_at = datetime.utcnow()
    row = SATSyncRun(
        mode=(mode or "auto").strip().lower(),
        trigger_source=(trigger_source or "admin").strip().lower(),
        started_at=started_at,
        finished_at=None,
        status="running",
        summary_message="Job en ejecución…",
        results=job_meta,
        http_status=None,
    )
    session.add(row)
    await session.flush()
    return row


async def _finalize_run(
    session: AsyncSession,
    run: SATSyncRun,
    *,
    status: str,
    summary_message: str,
    results: Any = None,
    http_status: Optional[int] = None,
) -> None:
    run.status = (status or "error").strip().lower()
    run.summary_message = (summary_message or "")[:4000] or None
    run.finished_at = datetime.utcnow()
    run.http_status = http_status
    if results is not None:
        run.results = results
    await session.flush()


async def _execute_scheduled_sync(run_id: UUID, mode: str, trigger_source: str) -> None:
    session_maker = _require_session_maker()
    service = SATSyncService()

    async with session_maker() as session:
        run = await session.get(SATSyncRun, run_id)
        if run is None:
            logger.error("SAT background sync missing run row %s", run_id)
            return

        lock_result = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": SAT_SYNC_ADVISORY_LOCK_KEY},
        )
        row = lock_result.fetchone()
        locked = row[0] if row else False
        if not locked:
            await _finalize_run(
                session,
                run,
                status="skipped",
                summary_message="Otra ejecución de sync SAT ya estaba en curso.",
                http_status=409,
            )
            await session.commit()
            return

        try:
            logger.info(
                "SAT background sync started run_id=%s mode=%s source=%s",
                run_id,
                mode,
                trigger_source,
            )
            report = await service.run(session, mode=mode)
            aggregate_status, summary_message = service.summarize_run_results(
                report.results
            )
            await _finalize_run(
                session,
                run,
                status=aggregate_status,
                summary_message=summary_message,
                results=report.to_dict().get("results"),
                http_status=200,
            )
            await session.commit()
            logger.info(
                "SAT background sync completed run_id=%s status=%s",
                run_id,
                aggregate_status,
            )
        except Exception as exc:
            await session.rollback()
            run = await session.get(SATSyncRun, run_id)
            if run is not None:
                await _finalize_run(
                    session,
                    run,
                    status="error",
                    summary_message=str(exc)[:2000],
                    http_status=500,
                )
                await session.commit()
            logger.exception("SAT background sync failed run_id=%s", run_id)
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": SAT_SYNC_ADVISORY_LOCK_KEY},
            )
            await session.commit()


async def _execute_process_download(
    run_id: UUID,
    *,
    solicitud_id: str,
    expense_id: Optional[str],
) -> None:
    session_maker = _require_session_maker()
    handler = SATExpenseHandler()

    async with session_maker() as session:
        run = await session.get(SATSyncRun, run_id)
        if run is None:
            logger.error("SAT background process missing run row %s", run_id)
            return

        try:
            expense = None
            expense_id_clean = (expense_id or "").strip()
            if expense_id_clean:
                from uuid import UUID as UUIDType

                from devnous.gastos.models import ExpenseReport

                expense = await session.get(ExpenseReport, UUIDType(expense_id_clean))

            result = await handler.process_download_request(
                session,
                solicitud_id=(solicitud_id or "").strip(),
                expense=expense,
                poll_until_complete=False,
            )
            process_result = result.get("result") or {}
            verification = process_result.get("verification") or {}
            status = result.get("status") or "error"
            if status in {"success", "warning"}:
                final_status = "success"
            elif status == "processing":
                final_status = "processing"
            else:
                final_status = "error"

            payload = {
                "job_kind": "process_download",
                "solicitud_id": (solicitud_id or "").strip(),
                "expense_id": expense_id_clean or None,
                "estado": verification.get("estado") or status,
                "num_cfdis": verification.get("num_cfdis"),
                "ingested_cfdis": process_result.get("ingested_cfdis", 0),
                "message": "; ".join(process_result.get("warnings") or [])
                or result.get("message")
                or "",
            }
            await _finalize_run(
                session,
                run,
                status=final_status,
                summary_message=str(payload.get("message") or result.get("message") or "")[:2000],
                results=payload,
                http_status=200 if final_status == "success" else 500,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            run = await session.get(SATSyncRun, run_id)
            if run is not None:
                await _finalize_run(
                    session,
                    run,
                    status="error",
                    summary_message=str(exc)[:2000],
                    results={
                        "job_kind": "process_download",
                        "solicitud_id": (solicitud_id or "").strip(),
                        "expense_id": (expense_id or "").strip() or None,
                    },
                    http_status=500,
                )
                await session.commit()
            logger.exception("SAT background process failed run_id=%s", run_id)


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _active_tasks.add(task)

    def _cleanup(done_task: asyncio.Task) -> None:
        _active_tasks.discard(done_task)
        _log_task_failure(done_task)

    task.add_done_callback(_cleanup)
    return task


def _log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("SAT background task failed: %s", exc, exc_info=exc)


async def recover_orphaned_sat_jobs() -> int:
    """Mark stale running jobs after an unclean process exit."""
    session_maker = _require_session_maker()
    async with session_maker() as session:
        result = await session.execute(
            select(SATSyncRun).where(SATSyncRun.status == "running")
        )
        runs = list(result.scalars().all())
        for run in runs:
            await _finalize_run(
                session,
                run,
                status="error",
                summary_message="Job interrumpido por reinicio del servicio.",
                http_status=500,
            )
        if runs:
            await session.commit()
            logger.warning(
                "Recovered %s orphaned SAT job(s) left in running state",
                len(runs),
            )
        return len(runs)


async def shutdown_background_jobs() -> None:
    """Best-effort cancel + persist interrupted state before process exit."""
    if _active_tasks:
        tasks = list(_active_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    session_maker = _require_session_maker()
    async with session_maker() as session:
        result = await session.execute(
            select(SATSyncRun).where(SATSyncRun.status == "running")
        )
        runs = list(result.scalars().all())
        for run in runs:
            await _finalize_run(
                session,
                run,
                status="error",
                summary_message="Job cancelado por reinicio del servicio.",
                http_status=500,
            )
        if runs:
            await session.commit()
            logger.warning(
                "Finalized %s SAT job(s) interrupted during shutdown",
                len(runs),
            )


async def _execute_open_jobs(run_id: UUID, trigger_source: str) -> None:
    session_maker = _require_session_maker()
    service = SATSyncService()

    async with session_maker() as session:
        run = await session.get(SATSyncRun, run_id)
        if run is None:
            logger.error("SAT open jobs missing run row %s", run_id)
            return

        lock_result = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": SAT_SYNC_ADVISORY_LOCK_KEY},
        )
        row = lock_result.fetchone()
        locked = row[0] if row else False
        if not locked:
            await _finalize_run(
                session,
                run,
                status="skipped",
                summary_message="Otra ejecución de sync SAT ya estaba en curso.",
                http_status=409,
            )
            await session.commit()
            return

        try:
            logger.info(
                "SAT open jobs started run_id=%s source=%s",
                run_id,
                trigger_source,
            )
            report = await service.run_open_jobs_only(session)
            aggregate_status, summary_message = service.summarize_run_results(
                report.results
            )
            await _finalize_run(
                session,
                run,
                status=aggregate_status,
                summary_message=summary_message,
                results=report.to_dict().get("results"),
                http_status=200,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            run = await session.get(SATSyncRun, run_id)
            if run is not None:
                await _finalize_run(
                    session,
                    run,
                    status="error",
                    summary_message=str(exc)[:2000],
                    http_status=500,
                )
                await session.commit()
            logger.exception("SAT open jobs failed run_id=%s", run_id)
        finally:
            await session.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": SAT_SYNC_ADVISORY_LOCK_KEY},
            )
            await session.commit()


async def enqueue_scheduled_sync(
    session: AsyncSession,
    *,
    mode: str,
    trigger_source: str,
) -> SATSyncRun:
    async with _spawn_lock:
        if await has_running_sat_job(session):
            raise RuntimeError("SAT sync already running")
        run = await create_running_sat_job(
            session,
            mode=mode,
            trigger_source=trigger_source,
            job_meta={"job_kind": "scheduled_sync", "mode": mode},
        )
        await session.commit()
        run_id = run.id

    _spawn(_execute_scheduled_sync(run_id, mode, trigger_source))
    return run


async def enqueue_open_jobs(
    session: AsyncSession,
    *,
    trigger_source: str = "cron",
) -> SATSyncRun:
    async with _spawn_lock:
        if await has_running_sat_job(session):
            raise RuntimeError("SAT sync already running")
        run = await create_running_sat_job(
            session,
            mode="open_jobs",
            trigger_source=trigger_source,
            job_meta={"job_kind": "open_jobs"},
        )
        await session.commit()
        run_id = run.id

    _spawn(_execute_open_jobs(run_id, trigger_source))
    return run


async def enqueue_process_download(
    session: AsyncSession,
    *,
    solicitud_id: str,
    expense_id: Optional[str] = None,
    trigger_source: str = "admin",
) -> SATSyncRun:
    solicitud_clean = (solicitud_id or "").strip()
    if not solicitud_clean:
        raise ValueError("solicitud_id is required")

    async with _spawn_lock:
        if await has_running_sat_job(session):
            raise RuntimeError("SAT job already running")
        run = await create_running_sat_job(
            session,
            mode="process_download",
            trigger_source=trigger_source,
            job_meta={
                "job_kind": "process_download",
                "solicitud_id": solicitud_clean,
                "expense_id": (expense_id or "").strip() or None,
            },
        )
        await session.commit()
        run_id = run.id

    _spawn(
        _execute_process_download(
            run_id,
            solicitud_id=solicitud_clean,
            expense_id=expense_id,
        )
    )
    return run
