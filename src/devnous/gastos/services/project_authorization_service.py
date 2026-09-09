"""Project-based, position-backed authorization routes.

This module deliberately stores employee assignments separately from access
roles: ``empleados.rol`` grants application access and is not an organisational
job title.  Routes are snapshotted when a document enters the workflow so a
later staffing change cannot rewrite its approval evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Empleado


DIRECTOR_OPERACIONES = "director_operaciones"
DIRECCION_ADMINISTRACION_FINANZAS = "direccion_administracion_finanzas"
DIRECCION_GOAT = "direccion_goat"
DIRECCION_GENERAL = "direccion_general"

BENEFICIARY_EXCEPTION_POSITIONS = frozenset(
    {DIRECTOR_OPERACIONES, DIRECCION_ADMINISTRACION_FINANZAS, DIRECCION_GOAT}
)
BENEFICIARY_EXCEPTION_APPROVERS = (
    DIRECCION_GENERAL,
    DIRECCION_ADMINISTRACION_FINANZAS,
)


@dataclass(frozen=True)
class ProjectAuthorizationRoute:
    eligible_position_keys: tuple[str, ...]
    requires_operations_reference: bool
    source: str


def _keys(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def resolve_project_authorization_route(
    *,
    beneficiary_position_keys: Iterable[str],
    project_rule: Optional[ProjectAuthorizationRoute],
) -> Optional[ProjectAuthorizationRoute]:
    """Resolve the documented beneficiary exception before the project rule."""
    if set(beneficiary_position_keys) & BENEFICIARY_EXCEPTION_POSITIONS:
        return ProjectAuthorizationRoute(
            eligible_position_keys=BENEFICIARY_EXCEPTION_APPROVERS,
            requires_operations_reference=False,
            source="beneficiary_position_exception",
        )
    return project_rule


async def ensure_project_authorization_schema(session: AsyncSession) -> None:
    """Create config tables for installations not yet migrated."""
    await session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS authorization_positions (
            position_key VARCHAR(100) PRIMARY KEY,
            label VARCHAR(200) NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """
        )
    )
    await session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS authorization_position_assignments (
            position_key VARCHAR(100) NOT NULL
                REFERENCES authorization_positions(position_key),
            empleado_id UUID NOT NULL REFERENCES empleados(id),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (position_key, empleado_id)
        )
    """
        )
    )
    await session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS project_authorization_rules (
            tournament_id UUID PRIMARY KEY REFERENCES tournaments(id),
            eligible_position_keys JSONB NOT NULL,
            requires_operations_reference BOOLEAN NOT NULL DEFAULT FALSE,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """
        )
    )
    await session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS documento_authorization_routes (
            documento_id UUID PRIMARY KEY
                REFERENCES documentos(id) ON DELETE CASCADE,
            eligible_position_keys JSONB NOT NULL,
            requires_operations_reference BOOLEAN NOT NULL DEFAULT FALSE,
            source VARCHAR(100) NOT NULL,
            resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """
        )
    )


async def resolve_and_snapshot_document_route(
    session: AsyncSession, documento: object
) -> Optional[ProjectAuthorizationRoute]:
    """Return and persist the effective route for a document, if configured."""
    document_id = str(getattr(documento, "id"))
    existing = await session.execute(
        text(
            """
        SELECT eligible_position_keys, requires_operations_reference, source
        FROM documento_authorization_routes WHERE documento_id = :documento_id
    """
        ),
        {"documento_id": document_id},
    )
    row = existing.mappings().first()
    if row:
        return ProjectAuthorizationRoute(
            _keys(row["eligible_position_keys"]),
            bool(row["requires_operations_reference"]),
            str(row["source"]),
        )

    beneficiary_id = getattr(documento, "beneficiario_empleado_id", None)
    if beneficiary_id is None:
        beneficiary_id = getattr(documento, "empleado_id", None)
    beneficiary = await session.execute(
        text(
            """
        SELECT position_key FROM authorization_position_assignments
        WHERE empleado_id = :empleado_id AND active = TRUE
    """
        ),
        {"empleado_id": str(beneficiary_id)},
    )
    positions = tuple(beneficiary.scalars().all())
    tournament_id = getattr(documento, "torneo_id", None)
    rule_row = None
    if tournament_id:
        rule_row = (
            (
                await session.execute(
                    text(
                        """
            SELECT eligible_position_keys, requires_operations_reference
            FROM project_authorization_rules
            WHERE tournament_id = :tournament_id AND active = TRUE
        """
                    ),
                    {"tournament_id": str(tournament_id)},
                )
            )
            .mappings()
            .first()
        )
    rule = None
    if rule_row:
        rule = ProjectAuthorizationRoute(
            _keys(rule_row["eligible_position_keys"]),
            bool(rule_row["requires_operations_reference"]),
            "project",
        )
    route = resolve_project_authorization_route(
        beneficiary_position_keys=positions, project_rule=rule
    )
    if route is None:
        return None
    await session.execute(
        text(
            """
        INSERT INTO documento_authorization_routes (
            documento_id, eligible_position_keys,
            requires_operations_reference, source
        ) VALUES (
            :documento_id, CAST(:positions AS jsonb),
            :requires_reference, :source
        )
    """
        ),
        {
            "documento_id": document_id,
            "positions": json.dumps(route.eligible_position_keys),
            "requires_reference": route.requires_operations_reference,
            "source": route.source,
        },
    )
    return route


async def actor_is_route_approver(
    session: AsyncSession, *, actor_id: object, documento_id: object
) -> bool:
    """True only for an active holder of a snapshotted eligible position."""
    result = await session.execute(
        text(
            """
        SELECT 1
        FROM documento_authorization_routes route
        JOIN authorization_position_assignments assignment
          ON assignment.position_key = ANY(
              ARRAY(
                  SELECT jsonb_array_elements_text(
                      route.eligible_position_keys
                  )
              )
          )
        WHERE route.documento_id = :documento_id
          AND assignment.empleado_id = :actor_id AND assignment.active = TRUE
        LIMIT 1
    """
        ),
        {"documento_id": str(documento_id), "actor_id": str(actor_id)},
    )
    return result.scalar_one_or_none() is not None


async def route_approvers_for_document(
    session: AsyncSession, documento_id: object
) -> Optional[list[Any]]:
    """Return active route holders, or ``None`` when the document uses legacy routing."""
    route = await session.execute(
        text(
            "SELECT 1 FROM documento_authorization_routes "
            "WHERE documento_id = :documento_id"
        ),
        {"documento_id": str(documento_id)},
    )
    if route.scalar_one_or_none() is None:
        return None
    employees = await session.execute(
        text(
            """
            SELECT e.id
            FROM empleados e
            JOIN authorization_position_assignments assignment
              ON assignment.empleado_id = e.id AND assignment.active = TRUE
            JOIN documento_authorization_routes route
              ON route.documento_id = :documento_id
             AND assignment.position_key = ANY(
                ARRAY(
                    SELECT jsonb_array_elements_text(route.eligible_position_keys)
                )
             )
            WHERE e.activo = TRUE
            ORDER BY e.nombre
            """
        ),
        {"documento_id": str(documento_id)},
    )
    employee_ids = list(employees.scalars().all())
    return [await session.get(Empleado, employee_id) for employee_id in employee_ids]


async def list_position_assignments(session: AsyncSession) -> list[dict[str, Any]]:
    """List configured positions with active holders for the admin screen."""
    await ensure_project_authorization_schema(session)
    result = await session.execute(
        text(
            """
            SELECT p.position_key, p.label, e.id AS empleado_id, e.nombre
            FROM authorization_positions p
            LEFT JOIN authorization_position_assignments a
              ON a.position_key = p.position_key AND a.active = TRUE
            LEFT JOIN empleados e ON e.id = a.empleado_id AND e.activo = TRUE
            WHERE p.active = TRUE
            ORDER BY p.label, e.nombre
            """
        )
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in result.mappings():
        item = grouped.setdefault(
            row["position_key"],
            {"key": row["position_key"], "label": row["label"], "holders": []},
        )
        if row["empleado_id"]:
            item["holders"].append(
                {"id": str(row["empleado_id"]), "nombre": row["nombre"]}
            )
    return list(grouped.values())


async def replace_position_holders(
    session: AsyncSession, *, position_key: str, employee_ids: Iterable[object]
) -> None:
    """Replace a position's active holders; the position key remains stable."""
    await ensure_project_authorization_schema(session)
    exists = await session.execute(
        text(
            "SELECT 1 FROM authorization_positions "
            "WHERE position_key = :position_key AND active = TRUE"
        ),
        {"position_key": position_key},
    )
    if exists.scalar_one_or_none() is None:
        raise ValueError("Puesto de autorización no encontrado.")
    valid = list(dict.fromkeys(str(value) for value in employee_ids if value))
    await session.execute(
        text(
            "UPDATE authorization_position_assignments SET active = FALSE "
            "WHERE position_key = :position_key"
        ),
        {"position_key": position_key},
    )
    for employee_id in valid:
        await session.execute(
            text(
                """
                INSERT INTO authorization_position_assignments(
                    position_key, empleado_id, active
                ) VALUES (:position_key, :employee_id, TRUE)
                ON CONFLICT (position_key, empleado_id)
                DO UPDATE SET active = TRUE
                """
            ),
            {"position_key": position_key, "employee_id": employee_id},
        )
    await session.commit()
