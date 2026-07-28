"""Bounded local-domain writer for one approved tournament projection.

The caller owns the outer transaction and its commit/rollback decision.  This
module owns only a savepoint around the insert so a normalized-name race can be
reported without committing unrelated work.  It creates exactly one local
``Tournament`` row and no linked or child domain objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import Tournament
from devnous.gastos.services.tournament_project_visibility import (
    canonical_departamento,
)


PROJECTION_FIELDS = frozenset(
    {
        "name",
        "description",
        "active",
        "display_order",
        "accounting_account",
        "stages",
        "categories",
        "visibility_areas",
    }
)


class TournamentApplicationError(ValueError):
    """Base error for bounded local tournament creation."""


class TournamentApplicationContractError(TournamentApplicationError):
    """Raised when the approved eight-field projection is invalid."""


class TournamentApplicationDuplicateNameError(TournamentApplicationError):
    """Raised when a normalized local tournament name already exists."""


class TournamentApplicationVerificationError(TournamentApplicationError):
    """Raised when the flushed row differs from the approved projection."""


def _is_name_uniqueness_violation(exc: IntegrityError) -> bool:
    current: Optional[BaseException] = exc
    seen = set()
    sqlstate = ""
    constraint = ""
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if not sqlstate:
            sqlstate = str(
                getattr(current, "sqlstate", None)
                or getattr(current, "pgcode", None)
                or ""
            )
        diagnostic = getattr(current, "diag", None)
        if not constraint:
            constraint = str(
                getattr(current, "constraint_name", None)
                or getattr(diagnostic, "constraint_name", None)
                or ""
            )
        current = (
            getattr(current, "orig", None)
            or getattr(current, "__cause__", None)
            or getattr(current, "__context__", None)
        )
    return sqlstate == "23505" and constraint in {
        "ux_tournaments_name_normalized",
        "ix_tournaments_name",
        "tournaments_name_key",
    }


def _optional_text(value: Any, *, field_name: str, max_length: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TournamentApplicationContractError(f"{field_name} must be text or null")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise TournamentApplicationContractError(
            f"{field_name} exceeds {max_length} characters"
        )
    return normalized or None


def _text_sequence(
    value: Any,
    *,
    field_name: str,
    visibility: bool = False,
) -> Tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TournamentApplicationContractError(
            f"{field_name} must be an array of text values"
        )
    normalized = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            raise TournamentApplicationContractError(
                f"{field_name} must contain only text values"
            )
        text = item.strip()
        if not text:
            raise TournamentApplicationContractError(
                f"{field_name} cannot contain blank values"
            )
        if visibility:
            canonical = canonical_departamento(text)
            if canonical is None:
                raise TournamentApplicationContractError(
                    f"{field_name} contains an unsupported department"
                )
            text = canonical
        identity = text.casefold()
        if identity in seen:
            raise TournamentApplicationContractError(
                f"{field_name} cannot contain duplicate values"
            )
        seen.add(identity)
        normalized.append(text)
    return tuple(normalized)


@dataclass(frozen=True)
class TournamentApplicationProjection:
    """The complete and only approved field set materialized by this writer."""

    name: str
    description: Optional[str]
    active: bool
    display_order: int
    accounting_account: Optional[str]
    stages: Tuple[str, ...]
    categories: Tuple[str, ...]
    visibility_areas: Tuple[str, ...]

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any]
    ) -> "TournamentApplicationProjection":
        if not isinstance(payload, Mapping):
            raise TournamentApplicationContractError("projection must be an object")
        if any(not isinstance(key, str) for key in payload):
            raise TournamentApplicationContractError(
                "projection field names must be text"
            )
        supplied = set(payload)
        missing = sorted(PROJECTION_FIELDS - supplied)
        unknown = sorted(supplied - PROJECTION_FIELDS)
        if missing or unknown:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unknown:
                details.append("unsupported: " + ", ".join(unknown))
            raise TournamentApplicationContractError(
                "projection must contain exactly eight fields ("
                + "; ".join(details)
                + ")"
            )

        raw_name = payload["name"]
        if not isinstance(raw_name, str):
            raise TournamentApplicationContractError("name must be text")
        name = raw_name.strip()
        if not name:
            raise TournamentApplicationContractError("name is required")
        if len(name) > 200:
            raise TournamentApplicationContractError("name exceeds 200 characters")

        active = payload["active"]
        if not isinstance(active, bool):
            raise TournamentApplicationContractError("active must be boolean")
        display_order = payload["display_order"]
        if isinstance(display_order, bool) or not isinstance(display_order, int):
            raise TournamentApplicationContractError("display_order must be an integer")
        if display_order < 0:
            raise TournamentApplicationContractError("display_order cannot be negative")

        return cls(
            name=name,
            description=_optional_text(
                payload["description"], field_name="description", max_length=500
            ),
            active=active,
            display_order=display_order,
            accounting_account=_optional_text(
                payload["accounting_account"],
                field_name="accounting_account",
                max_length=200,
            ),
            stages=_text_sequence(payload["stages"], field_name="stages"),
            categories=_text_sequence(payload["categories"], field_name="categories"),
            visibility_areas=_text_sequence(
                payload["visibility_areas"],
                field_name="visibility_areas",
                visibility=True,
            ),
        )

    @property
    def normalized_name(self) -> str:
        """Identity used by the PostgreSQL lower(btrim(name)) index."""

        return self.name.lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "active": self.active,
            "display_order": self.display_order,
            "accounting_account": self.accounting_account,
            "stages": list(self.stages),
            "categories": list(self.categories),
            "visibility_areas": list(self.visibility_areas),
        }


@dataclass(frozen=True)
class LocalTournamentApplicationResult:
    tournament_id: UUID
    projection: TournamentApplicationProjection
    domain_write_count: int = 1
    committed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tournament_id": str(self.tournament_id),
            "projection": self.projection.to_dict(),
            "domain_write_count": self.domain_write_count,
            "committed": self.committed,
        }


def projection_from_tournament(
    tournament: Tournament,
) -> TournamentApplicationProjection:
    """Project a persisted ORM row back into the exact approved contract."""

    return TournamentApplicationProjection.from_mapping(
        {
            "name": tournament.name,
            "description": tournament.description,
            "active": tournament.active,
            "display_order": tournament.display_order,
            "accounting_account": tournament.cuenta_contable_relacionada,
            "stages": tournament.etapas or [],
            "categories": tournament.categorias or [],
            "visibility_areas": tournament.form_visibility_areas or [],
        }
    )


async def create_local_tournament_from_projection(
    session: AsyncSession,
    *,
    projection: Mapping[str, Any],
) -> LocalTournamentApplicationResult:
    """Flush one local Tournament and verify it without committing.

    The caller must commit the outer transaction after composing any durable
    application receipt.  The internal savepoint only contains the insert and
    turns a normalized-name race into a domain conflict.
    """

    approved = TournamentApplicationProjection.from_mapping(projection)
    existing = (
        await session.execute(
            select(Tournament.id).where(
                func.lower(func.btrim(Tournament.name)) == approved.normalized_name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise TournamentApplicationDuplicateNameError(
            "a local tournament with this normalized name already exists"
        )

    tournament = Tournament(
        name=approved.name,
        description=approved.description,
        active=approved.active,
        display_order=approved.display_order,
        cuenta_contable_relacionada=approved.accounting_account,
        etapas=list(approved.stages),
        categorias=list(approved.categories),
        form_visibility_areas=list(approved.visibility_areas),
    )
    try:
        async with session.begin_nested():
            session.add(tournament)
            await session.flush()
    except IntegrityError as exc:
        if _is_name_uniqueness_violation(exc):
            raise TournamentApplicationDuplicateNameError(
                "a local tournament with this normalized name already exists"
            ) from exc
        raise

    if not isinstance(tournament.id, UUID):
        raise TournamentApplicationVerificationError(
            "flushed tournament did not receive a UUID"
        )
    persisted = (
        await session.execute(
            select(Tournament)
            .where(Tournament.id == tournament.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if persisted is None:
        raise TournamentApplicationVerificationError(
            "flushed tournament could not be read back"
        )
    observed = projection_from_tournament(persisted)
    if observed != approved:
        raise TournamentApplicationVerificationError(
            "persisted tournament differs from the approved projection"
        )
    return LocalTournamentApplicationResult(
        tournament_id=tournament.id,
        projection=observed,
    )


__all__ = [
    "LocalTournamentApplicationResult",
    "PROJECTION_FIELDS",
    "TournamentApplicationContractError",
    "TournamentApplicationDuplicateNameError",
    "TournamentApplicationError",
    "TournamentApplicationProjection",
    "TournamentApplicationVerificationError",
    "create_local_tournament_from_projection",
    "projection_from_tournament",
]
