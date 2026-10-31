"""Explicit SQL adapters for the existing registration authority only.

The owner-installed functions retain legacy operations while excluding the
protected v2 cohort. This module creates no capability, grants or transaction.
Callers must keep their existing capability and receipt checks. No fallback
to direct PostgreSQL writes is permitted if the functions are unavailable.
"""

from datetime import date, datetime
import json
from uuid import UUID

from sqlalchemy import select, text

from .models import Player, Team
from .persistence_authority import PersistenceAuthorityDenied


def uses_legacy_sql_boundary(session) -> bool:
    bind = getattr(session, "bind", None)
    return getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"


def _deny():
    raise PersistenceAuthorityDenied(
        "LEGACY_WRITE_BOUNDARY_DENIED", "legacy registration write denied"
    ) from None


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def materialized_insert_values(entity) -> dict:
    """Evaluate the same Python column defaults previously used by ORM flush."""
    if type(entity) not in (Team, Player):
        _deny()
    values = {}
    for column in entity.__table__.columns:
        value = getattr(entity, column.name)
        if value is None and column.default is not None:
            default = column.default
            if default.is_scalar:
                value = default.arg
            elif default.is_callable:
                value = default.arg(None)
            else:
                _deny()
            setattr(entity, column.name, value)
        values[column.name] = _json_value(value)
    return values


class LegacyRegistrationWriter:
    def __init__(self, session):
        if not uses_legacy_sql_boundary(session):
            _deny()
        self._session = session

    async def _execute(self, statement, parameters=None):
        try:
            return await self._session.execute(statement, parameters)
        except Exception:
            # SQLAlchemy statement errors include bound personal values.
            # Preserve cancellation, but never propagate those diagnostics.
            _deny()

    async def _refresh(self, entity):
        try:
            await self._session.refresh(entity)
        except Exception:
            _deny()

    async def insert(self, entity):
        values = materialized_insert_values(entity)
        function = {
            Team: "public.legacy_insert_team",
            Player: "public.legacy_insert_player",
        }[type(entity)]
        with self._session.no_autoflush:
            result = await self._execute(
                text("SELECT " + function + "(CAST(:payload AS jsonb))"),
                {"payload": json.dumps(values, ensure_ascii=False, allow_nan=False)},
            )
            row = result.scalar_one()
            if isinstance(row, str):
                row = json.loads(row)
            if type(row) is not dict or row.get("id") != values["id"]:
                _deny()
            persisted = (
                await self._execute(
                    select(type(entity)).where(type(entity).id == entity.id)
                )
            ).scalar_one()
        return persisted

    async def finalize_player(self, player, receipt_id):
        with self._session.no_autoflush:
            await self._execute(
                text("SELECT public.legacy_finalize_player(:player_id,:receipt_id)"),
                {"player_id": player.id, "receipt_id": receipt_id},
            )
            await self._refresh(player)
        return player

    async def project(self, entity, *, base_revision, base_hash, updates):
        entity_type = {Team: "TEAM", Player: "PLAYER"}.get(type(entity))
        if entity_type is None:
            _deny()
        with self._session.no_autoflush:
            await self._execute(
                text(
                    "SELECT public.legacy_apply_postcommit_projection("
                    ":kind,:entity_id,:revision,:hash,CAST(:updates AS jsonb))"
                ),
                {
                    "kind": entity_type,
                    "entity_id": entity.id,
                    "revision": base_revision,
                    "hash": base_hash,
                    "updates": json.dumps(
                        {name: _json_value(value) for name, value in updates.items()},
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                },
            )
            await self._refresh(entity)
        return entity
