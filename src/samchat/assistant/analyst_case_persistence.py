"""Dormant runtime persistence for deterministic Analyst cases."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .analyst_case import AnalystCase, build_analyst_case
from .analyst_case_store import AnalystCaseStore
from .analyst_intent import AnalystIntent
from .analyst_workbench import AnalystWorkbenchResult


logger = logging.getLogger(__name__)

PERSISTABLE_ANALYST_STATUSES = frozenset(
    {"success", "needs_context", "provider_unavailable"}
)


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

    case = build_analyst_case(
        user_id=user_id,
        role=role,
        question=question,
        intent=intent,
        result=result,
    )
    try:
        async with session.begin_nested():
            stored, created = await session.run_sync(
                lambda sync_session: _get_or_create_case(
                    sync_session,
                    case,
                )
            )
        return _result_for_case(
            stored,
            outcome="created" if created else "reused",
        )
    except IntegrityError:
        try:
            existing = await session.run_sync(
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
        if existing is not None:
            return _result_for_case(existing, outcome="reused")
        return AnalystCasePersistenceResult(
            enabled=True,
            outcome="failed",
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
