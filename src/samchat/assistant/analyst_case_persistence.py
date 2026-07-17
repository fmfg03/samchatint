"""Dormant runtime persistence for deterministic Analyst cases."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .analyst_case import (
    CASE_STATUS_CLOSED,
    CASE_STATUS_REVIEWED,
    AnalystCase,
    build_analyst_case,
)
from .analyst_case_store import AnalystCaseStore, version_id_for
from .analyst_intent import AnalystIntent
from .analyst_workbench import AnalystWorkbenchResult


logger = logging.getLogger(__name__)

PERSISTABLE_ANALYST_STATUSES = frozenset(
    {"success", "needs_context", "provider_unavailable"}
)
TERMINAL_ANALYST_CASE_STATUSES = frozenset(
    {CASE_STATUS_REVIEWED, CASE_STATUS_CLOSED}
)
MAX_CASE_SUCCESSOR_DEPTH = 16


@dataclass(frozen=True)
class AnalystCasePersistenceResult:
    """Non-sensitive outcome exposed to the assistant trace."""

    enabled: bool
    outcome: str
    case_id: Optional[str] = None
    status: Optional[str] = None
    version_number: Optional[int] = None

    def trace(self) -> dict[str, Any]:
        """Return the bounded trace contract without case contents."""

        payload = asdict(self)
        payload.update(
            {
                "product_case_write": self.outcome == "created",
                "operational_writes": False,
                "actions_executed": [],
            }
        )
        return payload


def analyst_case_persistence_enabled() -> bool:
    """Return whether product-internal case persistence is enabled."""

    value = os.getenv(
        "ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED",
        "false",
    )
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_or_create_case(
    session: Session,
    case: AnalystCase,
) -> Tuple[AnalystCase, bool]:
    store = AnalystCaseStore(session)
    existing = store.get_case(case.case_id)
    if existing is not None:
        return existing, False
    return store.create_case(case), True


def _get_case(session: Session, case_id: str) -> Optional[AnalystCase]:
    return AnalystCaseStore(session).get_case(case_id)


def _analysis_scope(
    *,
    conversation_id: str,
    result: AnalystWorkbenchResult,
) -> str:
    payload = {
        "conversation_id": str(conversation_id),
        "status": result.status,
        "answer": result.answer,
        "evidence": result.evidence,
        "next_questions": result.next_questions,
        "suggested_routes": result.suggested_routes,
        "caveats": result.caveats,
        "answer_contract": result.answer_contract,
    }
    canonical = json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case_with_scope(case: AnalystCase, scope: str) -> AnalystCase:
    raw = f"{case.case_id}|{scope}"
    case_id = f"analyst_case_{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex}"
    versions = [
        replace(
            version,
            version_id=version_id_for(
                case_id,
                version.version_number,
            ),
        )
        for version in case.versions
    ]
    return replace(case, case_id=case_id, versions=versions)


def _result_for_case(
    case: AnalystCase,
    *,
    outcome: str,
) -> AnalystCasePersistenceResult:
    version_number = None
    if case.versions:
        version_number = case.versions[-1].version_number
    return AnalystCasePersistenceResult(
        enabled=True,
        outcome=outcome,
        case_id=case.case_id,
        status=case.status,
        version_number=version_number,
    )


async def persist_analyst_case(
    *,
    session: Any,
    conversation_id: str,
    current_empleado: Any,
    question: str,
    intent: AnalystIntent,
    result: AnalystWorkbenchResult,
) -> AnalystCasePersistenceResult:
    """Create or reuse an Analyst case inside an isolated savepoint."""

    if not analyst_case_persistence_enabled():
        return AnalystCasePersistenceResult(
            enabled=False,
            outcome="skipped",
        )
    if result.status not in PERSISTABLE_ANALYST_STATUSES:
        return AnalystCasePersistenceResult(
            enabled=True,
            outcome="skipped",
        )

    user_id = str(getattr(current_empleado, "id", "") or "").strip()
    role = str(getattr(current_empleado, "rol", "") or "").strip()
    if not user_id or not role or not str(conversation_id or "").strip():
        return AnalystCasePersistenceResult(
            enabled=True,
            outcome="skipped",
        )

    base_case = build_analyst_case(
        user_id=user_id,
        role=role,
        question=question,
        intent=intent,
        result=result,
    )
    scope = _analysis_scope(
        conversation_id=conversation_id,
        result=result,
    )

    for _depth in range(MAX_CASE_SUCCESSOR_DEPTH):
        case = _case_with_scope(base_case, scope)
        try:
            async with session.begin_nested():
                stored, created = await session.run_sync(
                    lambda sync_session: _get_or_create_case(
                        sync_session,
                        case,
                    )
                )
        except IntegrityError:
            try:
                stored = await session.run_sync(
                    lambda sync_session: _get_case(
                        sync_session,
                        case.case_id,
                    )
                )
            except Exception as exc:
                _log_persistence_failure(
                    exc,
                    case_id=case.case_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                return AnalystCasePersistenceResult(
                    enabled=True,
                    outcome="failed",
                )
            if stored is None:
                return AnalystCasePersistenceResult(
                    enabled=True,
                    outcome="failed",
                )
            created = False
        except Exception as exc:
            _log_persistence_failure(
                exc,
                case_id=case.case_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            return AnalystCasePersistenceResult(
                enabled=True,
                outcome="failed",
            )

        if created:
            return _result_for_case(stored, outcome="created")
        if stored.status not in TERMINAL_ANALYST_CASE_STATUSES:
            return _result_for_case(stored, outcome="reused")

        terminal_version = (
            stored.versions[-1].version_id
            if stored.versions
            else stored.case_id
        )
        scope = f"{scope}|after:{terminal_version}"

    _log_persistence_failure(
        RuntimeError("Analyst case successor depth exceeded"),
        case_id=base_case.case_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    return AnalystCasePersistenceResult(
        enabled=True,
        outcome="failed",
    )


def _log_persistence_failure(
    exc: Exception,
    *,
    case_id: str,
    conversation_id: str,
    user_id: str,
) -> None:
    logger.warning(
        "Analyst case persistence failed",
        extra={
            "case_id": case_id,
            "conversation_id": str(conversation_id),
            "user_id": user_id,
            "error_type": type(exc).__name__,
        },
    )
