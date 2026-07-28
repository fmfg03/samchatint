"""Local read authority for approval-ready tournament draft work.

The helper binds a draft to a current local tournament source and an active
employee owner.  It intentionally owns no transaction boundary and performs no
domain mutation or external lookup.
"""

from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import Empleado

from .tournament_goal_source import (
    TournamentSourceSnapshot,
    inspect_tournament_source,
)


SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class TournamentDraftAuthorityError(ValueError):
    """Base error for local draft authority inspection."""


class TournamentDraftOwnerNotFoundError(TournamentDraftAuthorityError):
    """Raised when the selected employee owner does not exist."""


class TournamentDraftOwnerInactiveError(TournamentDraftAuthorityError):
    """Raised when the selected employee owner is inactive."""


class TournamentDraftSourceStaleError(TournamentDraftAuthorityError):
    """Raised when the expected source hash no longer matches local state."""


class TournamentDraftOwnerSnapshot(BaseModel):
    id: UUID
    nombre: str
    departamento: Optional[str] = None
    rol: str
    activo: bool


class TournamentDraftAuthoritySnapshot(BaseModel):
    owner: TournamentDraftOwnerSnapshot
    source: TournamentSourceSnapshot
    expected_source_hash: str
    source_hash_verified: bool = True
    domain_write_performed: bool = False


def _parse_uuid(value: UUID | str, *, field_name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError) as exc:
        raise TournamentDraftAuthorityError(
            f"{field_name} must be a valid UUID"
        ) from exc


def _parse_source_hash(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise TournamentDraftAuthorityError(
            "expected_source_hash must be sha256 followed by 64 hex digits"
        )
    return normalized


async def _inspect_active_owner(
    session: AsyncSession,
    owner_employee_id: UUID,
) -> TournamentDraftOwnerSnapshot:
    result = await session.execute(
        select(Empleado).where(Empleado.id == owner_employee_id)
    )
    owner = result.scalar_one_or_none()
    if owner is None:
        raise TournamentDraftOwnerNotFoundError("tournament owner was not found")
    if not bool(getattr(owner, "activo", False)):
        raise TournamentDraftOwnerInactiveError("tournament owner is inactive")
    return TournamentDraftOwnerSnapshot(
        id=owner.id,
        nombre=str(owner.nombre or "").strip(),
        departamento=(str(owner.departamento).strip() if owner.departamento else None),
        rol=str(owner.rol or "").strip(),
        activo=True,
    )


async def inspect_active_tournament_owner(
    session: AsyncSession,
    owner_employee_id: UUID | str,
) -> TournamentDraftOwnerSnapshot:
    """Resolve one active local employee as tournament draft owner."""

    owner_id = _parse_uuid(owner_employee_id, field_name="owner_employee_id")
    return await _inspect_active_owner(session, owner_id)


async def inspect_tournament_draft_authority(
    session: AsyncSession,
    *,
    owner_employee_id: UUID | str,
    expected_source_hash: str,
    source_tournament_id: Optional[UUID | str] = None,
    source_tournament_name: Optional[str] = None,
) -> TournamentDraftAuthoritySnapshot:
    """Reinspect local source and resolve one active employee owner.

    ``expected_source_hash`` is compared with the freshly computed local source
    hash before owner resolution.  A stale draft therefore fails closed without
    producing an approval-ready authority snapshot.
    """

    owner_id = _parse_uuid(owner_employee_id, field_name="owner_employee_id")
    expected_hash = _parse_source_hash(expected_source_hash)
    source_id = (
        _parse_uuid(source_tournament_id, field_name="source_tournament_id")
        if source_tournament_id is not None
        else None
    )
    source = await inspect_tournament_source(
        session,
        tournament_id=source_id,
        tournament_name=source_tournament_name,
    )
    if source.source_hash.casefold() != expected_hash:
        raise TournamentDraftSourceStaleError(
            "tournament source changed after the draft was created"
        )
    owner = await inspect_active_tournament_owner(session, owner_id)
    return TournamentDraftAuthoritySnapshot(
        owner=owner,
        source=source,
        expected_source_hash=expected_hash,
    )


__all__ = [
    "TournamentDraftAuthorityError",
    "TournamentDraftAuthoritySnapshot",
    "TournamentDraftOwnerInactiveError",
    "TournamentDraftOwnerNotFoundError",
    "TournamentDraftOwnerSnapshot",
    "TournamentDraftSourceStaleError",
    "inspect_active_tournament_owner",
    "inspect_tournament_draft_authority",
]
