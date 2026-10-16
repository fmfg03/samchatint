"""Minimal SOLICITUD projection for separately authorized connector reads.

Does not call web GET routes (which can commit or send notifications). The
trusted caller must resolve canonical visibility for every supplied UUID and
use an isolated read-only transaction. Related records are intentionally absent.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID


async def read_solicitudes(
    session: Any,
    *,
    object_ids: tuple[str, ...],
    tournament_id: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    from sqlalchemy import bindparam, text

    if not 1 <= len(object_ids) <= 100:
        raise ValueError("Bounded exact selection required")
    ids = [str(UUID(value)) for value in object_ids]
    tournament = str(UUID(tournament_id))
    statement = text(
        "SELECT CAST(id AS TEXT) AS object_id,tipo AS kind,estado AS state,"
        "creado_en AS created_at,currency,monto_total AS amount "
        "FROM documentos WHERE tipo = 'SOLICITUD' "
        "AND id IN :object_ids "
        "AND torneo_id = :tournament "
        "AND creado_en >= :date_from AND creado_en < :date_to ORDER BY id LIMIT 101"
    ).bindparams(bindparam("object_ids", expanding=True))
    result = await session.execute(
        statement,
        {
            "object_ids": ids,
            "tournament": tournament,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    return [dict(row) for row in result.mappings().all()]
