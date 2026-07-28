"""Read-only local source inspection for tournament goal drafts.

This module deliberately reads only the local PostgreSQL authority.  It does
not call the legacy tournaments-v2/Supabase adapters and it never owns a
transaction boundary.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.copa_telmex.models import Player, Team
from devnous.gastos.models import Tournament, TournamentOperationsLink
from devnous.gastos.services.tournament_phase_service import (
    get_tournament_scope_options,
)
from devnous.gastos.services.tournament_project_visibility import (
    normalize_form_visibility_departments,
)


SOURCE_SCHEMA_VERSION = "2026-07-27.v1"


class TournamentSourceError(ValueError):
    """Base error for local tournament source resolution."""


class TournamentSourceNotFoundError(TournamentSourceError):
    """Raised when the requested local tournament does not exist."""


class TournamentSourceAmbiguousError(TournamentSourceError):
    """Raised when a name resolves to more than one local tournament."""


class TournamentSourceSelector(BaseModel):
    """Exactly one stable selector for a local tournament project."""

    tournament_id: Optional[UUID] = None
    tournament_name: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_exactly_one_selector(self) -> "TournamentSourceSelector":
        name = (self.tournament_name or "").strip()
        if bool(self.tournament_id) == bool(name):
            raise ValueError("provide exactly one of tournament_id or tournament_name")
        self.tournament_name = name or None
        return self


class LocalTournamentProject(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    active: bool
    display_order: int
    cuenta_contable_relacionada: Optional[str] = None
    etapas: list[str] = Field(default_factory=list)
    categorias: list[str] = Field(default_factory=list)
    form_visibility_departments: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class LocalTournamentOperationsLink(BaseModel):
    operations_tournament_id: str
    operations_tournament_slug: Optional[str] = None


class LocalTournamentOperationsAggregate(BaseModel):
    available: bool = False
    scope_slug: Optional[str] = None
    teams_count: int = 0
    players_count: int = 0
    categories: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    municipalities: list[str] = Field(default_factory=list)


class TournamentSourceSnapshot(BaseModel):
    schema_version: str = SOURCE_SCHEMA_VERSION
    project: LocalTournamentProject
    operations_link: Optional[LocalTournamentOperationsLink] = None
    observed_operations: LocalTournamentOperationsAggregate
    unavailable_components: list[str] = Field(default_factory=list)
    source_hash: str
    domain_write_performed: bool = False


def _clean_labels(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _normalize_dimension(values: Any) -> list[str]:
    """Return stable non-empty labels deduplicated case-insensitively."""

    canonical_by_folded: dict[str, str] = {}
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        folded = value.casefold()
        current = canonical_by_folded.get(folded)
        if current is None or value < current:
            canonical_by_folded[folded] = value
    return [canonical_by_folded[key] for key in sorted(canonical_by_folded)]


def _json_default(value: Any) -> str:
    if isinstance(value, (UUID, date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


async def _resolve_project(
    session: AsyncSession,
    selector: TournamentSourceSelector,
) -> Tournament:
    if selector.tournament_id is not None:
        statement = select(Tournament).where(Tournament.id == selector.tournament_id)
    else:
        statement = select(Tournament).where(
            func.lower(func.trim(Tournament.name))
            == str(selector.tournament_name).casefold()
        )
    result = await session.execute(statement)
    matches = list(result.scalars().all())
    if not matches:
        raise TournamentSourceNotFoundError("local tournament was not found")
    if len(matches) > 1:
        raise TournamentSourceAmbiguousError(
            "tournament name resolves to more than one local project"
        )
    return matches[0]


async def _load_operations_link(
    session: AsyncSession,
    tournament_id: UUID,
) -> Optional[TournamentOperationsLink]:
    result = await session.execute(
        select(TournamentOperationsLink).where(
            TournamentOperationsLink.tournament_id == tournament_id
        )
    )
    return result.scalar_one_or_none()


async def _load_operations_aggregate(
    session: AsyncSession,
    slug: Optional[str],
) -> LocalTournamentOperationsAggregate:
    exact_slug = str(slug or "").strip()
    if not exact_slug:
        return LocalTournamentOperationsAggregate()

    team_count = (
        await session.execute(
            select(func.count(Team.id)).where(Team.tournament_slug == exact_slug)
        )
    ).scalar_one()
    player_count = (
        await session.execute(
            select(func.count(Player.id))
            .select_from(Player)
            .join(Team, Player.team_id == Team.id)
            .where(Team.tournament_slug == exact_slug)
        )
    ).scalar_one()
    dimension_rows = (
        await session.execute(
            select(Team.category, Team.gender, Team.state, Team.municipality)
            .where(Team.tournament_slug == exact_slug)
            .distinct()
        )
    ).all()

    return LocalTournamentOperationsAggregate(
        available=True,
        scope_slug=exact_slug,
        teams_count=int(team_count or 0),
        players_count=int(player_count or 0),
        categories=_normalize_dimension(row.category for row in dimension_rows),
        branches=_normalize_dimension(row.gender for row in dimension_rows),
        states=_normalize_dimension(row.state for row in dimension_rows),
        municipalities=_normalize_dimension(row.municipality for row in dimension_rows),
    )


async def inspect_tournament_source(
    session: AsyncSession,
    *,
    tournament_id: Optional[UUID | str] = None,
    tournament_name: Optional[str] = None,
) -> TournamentSourceSnapshot:
    """Inspect a tournament source without mutating any domain table.

    The caller retains ownership of the session and its transaction.  Rich
    schedule/config/media data is intentionally reported unavailable because
    current ``main`` exposes those components only through the external
    tournaments-v2 authority.
    """

    selector = TournamentSourceSelector(
        tournament_id=tournament_id,
        tournament_name=tournament_name,
    )
    tournament = await _resolve_project(session, selector)
    link = await _load_operations_link(session, tournament.id)
    operations = await _load_operations_aggregate(
        session,
        getattr(link, "operations_tournament_slug", None),
    )
    scope = get_tournament_scope_options(tournament)
    project = LocalTournamentProject(
        id=tournament.id,
        name=str(tournament.name),
        description=getattr(tournament, "description", None),
        active=bool(getattr(tournament, "active", False)),
        display_order=int(getattr(tournament, "display_order", 0) or 0),
        cuenta_contable_relacionada=getattr(
            tournament, "cuenta_contable_relacionada", None
        ),
        etapas=_clean_labels(scope.get("etapas")),
        categorias=_clean_labels(scope.get("categorias")),
        form_visibility_departments=normalize_form_visibility_departments(
            getattr(tournament, "form_visibility_areas", None)
        ),
        created_at=getattr(tournament, "created_at", None),
        updated_at=getattr(tournament, "updated_at", None),
    )
    link_snapshot = (
        LocalTournamentOperationsLink(
            operations_tournament_id=str(link.operations_tournament_id),
            operations_tournament_slug=(
                str(link.operations_tournament_slug).strip()
                if link.operations_tournament_slug
                else None
            ),
        )
        if link is not None
        else None
    )
    unavailable = [
        "communications",
        "matches_and_schedule",
        "media",
        "rich_tournament_config",
        "rich_tournament_dates",
    ]
    hash_payload = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "project": project.model_dump(mode="json"),
        "operations_link": (
            link_snapshot.model_dump(mode="json") if link_snapshot else None
        ),
        "observed_operations": operations.model_dump(mode="json"),
        "unavailable_components": unavailable,
    }
    return TournamentSourceSnapshot(
        project=project,
        operations_link=link_snapshot,
        observed_operations=operations,
        unavailable_components=unavailable,
        source_hash=_content_hash(hash_payload),
    )


__all__ = [
    "LocalTournamentOperationsAggregate",
    "LocalTournamentOperationsLink",
    "LocalTournamentProject",
    "TournamentSourceAmbiguousError",
    "TournamentSourceError",
    "TournamentSourceNotFoundError",
    "TournamentSourceSelector",
    "TournamentSourceSnapshot",
    "inspect_tournament_source",
]
