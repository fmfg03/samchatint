"""Conversation-local pointer to the active tournament goal case."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import AssistantConversation


ACTIVE_TOURNAMENT_CASE_KEY = "active_tournament_goal_case"
CASE_ID_PATTERN = re.compile(r"^analyst_case_[0-9a-f]{32}$")


class TournamentCasePointerError(ValueError):
    """Raised when the active-case pointer cannot be accessed safely."""


def _parse_uuid(value: str, *, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value or "").strip())
    except (TypeError, ValueError, AttributeError) as exc:
        raise TournamentCasePointerError(f"Invalid {field_name}") from exc


async def _owned_conversation(
    session: AsyncSession,
    *,
    conversation_id: str,
    employee_id: str,
    for_update: bool = False,
) -> AssistantConversation:
    conversation_uuid = _parse_uuid(conversation_id, field_name="conversation_id")
    employee_uuid = _parse_uuid(employee_id, field_name="employee_id")
    statement = select(AssistantConversation).where(
        AssistantConversation.id == conversation_uuid,
        AssistantConversation.empleado_id == employee_uuid,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    result = await session.execute(statement)
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise TournamentCasePointerError("Assistant conversation was not found")
    return conversation


async def get_active_tournament_case_pointer(
    session: AsyncSession,
    *,
    conversation_id: str,
    employee_id: str,
) -> Optional[Dict[str, Any]]:
    conversation = await _owned_conversation(
        session,
        conversation_id=conversation_id,
        employee_id=employee_id,
    )
    metadata = (
        dict(conversation.metadata_) if isinstance(conversation.metadata_, dict) else {}
    )
    pointer = metadata.get(ACTIVE_TOURNAMENT_CASE_KEY)
    if not isinstance(pointer, dict):
        return None
    case_id = str(pointer.get("case_id") or "").strip()
    if not CASE_ID_PATTERN.fullmatch(case_id):
        return None
    return {
        "case_id": case_id,
        "case_version": int(pointer.get("case_version") or 0),
        "status": str(pointer.get("status") or "").strip(),
    }


async def set_active_tournament_case_pointer(
    session: AsyncSession,
    *,
    conversation_id: str,
    employee_id: str,
    case_id: str,
    case_version: int,
    status: str,
) -> Dict[str, Any]:
    normalized_case_id = str(case_id or "").strip()
    if not CASE_ID_PATTERN.fullmatch(normalized_case_id):
        raise TournamentCasePointerError("Invalid tournament case_id")
    if int(case_version) < 1:
        raise TournamentCasePointerError("case_version must be positive")
    conversation = await _owned_conversation(
        session,
        conversation_id=conversation_id,
        employee_id=employee_id,
        for_update=True,
    )
    metadata = (
        dict(conversation.metadata_) if isinstance(conversation.metadata_, dict) else {}
    )
    pointer = {
        "case_id": normalized_case_id,
        "case_version": int(case_version),
        "status": str(status or "").strip(),
    }
    metadata[ACTIVE_TOURNAMENT_CASE_KEY] = pointer
    conversation.metadata_ = metadata
    return pointer


__all__ = [
    "ACTIVE_TOURNAMENT_CASE_KEY",
    "TournamentCasePointerError",
    "get_active_tournament_case_pointer",
    "set_active_tournament_case_pointer",
]
