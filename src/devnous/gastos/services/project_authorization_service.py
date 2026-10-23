"""Project-based, position-backed authorization routes.

This module deliberately stores employee assignments separately from access
roles: ``empleados.rol`` grants application access and is not an organisational
job title.  Routes are snapshotted when a document enters the workflow so a
later staffing change cannot rewrite its approval evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Aprobacion, Documento, Empleado


DIRECTOR_OPERACIONES = "director_operaciones"
DIRECCION_ADMINISTRACION_FINANZAS = "direccion_administracion_finanzas"
DIRECCION_GOAT = "direccion_goat"
DIRECCION_GENERAL = "direccion_general"
PROVISIONAL_GN = "aprobador_federico_gn"
PROVISIONAL_SUPERVISOR = "supervision_aprobaciones_federico_gv"
PROVISIONAL_EXCEPTION = "aprobaciones_escalan_gn"
PROVISIONAL_SOURCE_PREFIX = "p20261023"
NO_OPERATIONS_PROJECTS = frozenset(
    {"gestión de patrocinios", "gestión rrss y transmisiones"}
)

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
    requester_position_keys: Iterable[str] = (),
    provisional: bool = False,
    project_rule: Optional[ProjectAuthorizationRoute],
) -> Optional[ProjectAuthorizationRoute]:
    """Resolve the documented beneficiary exception before the project rule."""
    if provisional:
        if PROVISIONAL_EXCEPTION in set(beneficiary_position_keys) | set(
            requester_position_keys
        ):
            return ProjectAuthorizationRoute(
                (PROVISIONAL_GN,),
                (
                    project_rule.requires_operations_reference
                    if project_rule
                    and not set(beneficiary_position_keys)
                    & BENEFICIARY_EXCEPTION_POSITIONS
                    else False
                ),
                "person",
            )
        return project_rule
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
            eligible_empleado_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            requires_operations_reference BOOLEAN NOT NULL DEFAULT FALSE,
            source VARCHAR(100) NOT NULL,
            resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """
        )
    )
    await session.execute(
        text(
            "ALTER TABLE documento_authorization_routes "
            "ADD COLUMN IF NOT EXISTS eligible_empleado_ids "
            "JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
    )


def provisional_policy_active_sql() -> str:
    """Activation is explicit configuration, never inferred from generic roles."""
    return (
        "EXISTS (SELECT 1 FROM authorization_positions policy_activation "
        "WHERE policy_activation.position_key = 'aprobaciones_escalan_gn' "
        "AND policy_activation.active = TRUE)"
    )


async def provisional_policy_is_active(session: AsyncSession) -> bool:
    result = await session.execute(text("SELECT " + provisional_policy_active_sql()))
    return result.scalar_one_or_none() is True


async def resolve_document_route_context(
    session: AsyncSession, documento: object
) -> dict[str, Optional[str]]:
    """Read explicit document/account parties and project without writing a route."""
    tournament_id = getattr(documento, "torneo_id", None)
    requester_id = getattr(documento, "empleado_id", None)
    beneficiary_id = getattr(documento, "beneficiario_empleado_id", None)
    provider_id = getattr(documento, "beneficiario_proveedor_cliente_id", None)
    account_id = getattr(documento, "cuenta_gastos_id", None)
    if account_id:
        result = await session.execute(
            text(
                "SELECT torneo_id, empleado_id, beneficiario_empleado_id, "
                "beneficiario_proveedor_cliente_id FROM cuentas_de_gastos "
                "WHERE id = :cuenta_id"
            ),
            {"cuenta_id": str(account_id)},
        )
        account = result.mappings().first()
        if account:
            tournament_id = tournament_id or account["torneo_id"]
            if not beneficiary_id and not provider_id:
                if not account["beneficiario_proveedor_cliente_id"]:
                    beneficiary_id = (
                        account["beneficiario_empleado_id"] or account["empleado_id"]
                    )
    if not beneficiary_id and not provider_id and not account_id:
        beneficiary_id = requester_id
    return {
        "tournament_id": str(tournament_id) if tournament_id else None,
        "requester_id": str(requester_id) if requester_id else None,
        "beneficiary_id": str(beneficiary_id) if beneficiary_id else None,
    }


def build_route_context_source(
    *,
    route: ProjectAuthorizationRoute,
    tournament_id: Optional[str],
    requester_id: Optional[str],
    beneficiary_id: Optional[str],
    eligible_employee_ids: Iterable[object],
) -> str:
    """Keep policy version/context in the existing bounded source column."""
    reason = route.source
    if reason not in {"person", "project", "unconfigured"}:
        raise ValueError("Unknown provisional route reason")
    context = {
        "policy": PROVISIONAL_SOURCE_PREFIX,
        "tournament_id": tournament_id,
        "requester_id": requester_id,
        "beneficiary_id": beneficiary_id,
        "eligible_position_keys": sorted(route.eligible_position_keys),
        "eligible_employee_ids": sorted(str(v) for v in eligible_employee_ids),
        "requires_operations_reference": route.requires_operations_reference,
    }
    fingerprint = hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    source = f"{PROVISIONAL_SOURCE_PREFIX}:{reason}:{fingerprint}"
    if len(source) > 100:
        raise ValueError("Authorization source exceeds storage contract")
    return source


async def preview_document_authorization_route(
    session: AsyncSession, documento: object
) -> dict[str, Any]:
    """Resolve current configured authority, never provision or persist it."""
    provisional = await provisional_policy_is_active(session) and str(
        getattr(documento, "tipo", "")
    ) in {"SOLICITUD", "INFORME"}
    context = await resolve_document_route_context(session, documento)
    positions: dict[str, tuple[str, ...]] = {}
    for party in ("requester_id", "beneficiary_id"):
        if not context[party]:
            positions[party] = ()
            continue
        result = await session.execute(
            text(
                "SELECT a.position_key FROM authorization_position_assignments a "
                "JOIN authorization_positions p ON p.position_key=a.position_key "
                "AND p.active=TRUE JOIN empleados e ON e.id=a.empleado_id "
                "AND e.activo=TRUE WHERE a.empleado_id=:empleado_id "
                "AND a.active=TRUE ORDER BY a.position_key"
            ),
            {"empleado_id": context[party]},
        )
        positions[party] = tuple(result.scalars().all())
    rule = None
    if context["tournament_id"]:
        result = await session.execute(
            text(
                "SELECT eligible_position_keys, requires_operations_reference "
                "FROM project_authorization_rules WHERE tournament_id=:tournament_id "
                "AND active=TRUE"
            ),
            {"tournament_id": context["tournament_id"]},
        )
        row = result.mappings().first()
        if row:
            rule = ProjectAuthorizationRoute(
                _keys(row["eligible_position_keys"]),
                bool(row["requires_operations_reference"]),
                "project",
            )
    route = resolve_project_authorization_route(
        beneficiary_position_keys=positions["beneficiary_id"],
        requester_position_keys=positions["requester_id"],
        provisional=provisional,
        project_rule=rule,
    )
    # An activated policy never falls back to an arbitrary personal/admin approver.
    if provisional and route is None:
        route = ProjectAuthorizationRoute((), False, "unconfigured")
    holders = []
    if route and route.eligible_position_keys:
        result = await session.execute(
            text(
                "SELECT DISTINCT a.empleado_id FROM authorization_position_assignments a "
                "JOIN authorization_positions p ON p.position_key=a.position_key "
                "AND p.active=TRUE JOIN empleados e ON e.id=a.empleado_id "
                "AND e.activo=TRUE WHERE a.active=TRUE "
                "AND a.position_key=ANY(CAST(:positions AS text[])) ORDER BY a.empleado_id"
            ),
            {"positions": list(route.eligible_position_keys)},
        )
        holders = [str(v) for v in result.scalars().all()]
    source = (
        build_route_context_source(
            route=route, **context, eligible_employee_ids=holders
        )
        if route and provisional
        else route.source if route else None
    )
    return {
        "route": route,
        "eligible_employee_ids": holders,
        "context": context,
        "source": source,
        "provisional": provisional,
    }


async def resolve_and_snapshot_document_route(
    session: AsyncSession,
    documento: object,
    *,
    audit_actor_id: object = None,
    audit_context: Optional[dict] = None,
) -> Optional[ProjectAuthorizationRoute]:
    """Persist a fresh route only in the owning explicit workflow transaction."""
    document_id = str(getattr(documento, "id"))
    existing = await session.execute(
        text(
            "SELECT eligible_position_keys, eligible_empleado_ids, "
            "requires_operations_reference, source FROM documento_authorization_routes "
            "WHERE documento_id=:documento_id"
        ),
        {"documento_id": document_id},
    )
    previous = existing.mappings().first()
    provisional = await provisional_policy_is_active(session) and str(
        getattr(documento, "tipo", "")
    ) in {"SOLICITUD", "INFORME"}
    if previous and not provisional:
        return ProjectAuthorizationRoute(
            _keys(previous["eligible_position_keys"]),
            bool(previous["requires_operations_reference"]),
            str(previous["source"]),
        )
    preview = await preview_document_authorization_route(session, documento)
    route = preview["route"]
    if route is None:
        return None
    effective_route = (
        ProjectAuthorizationRoute(
            route.eligible_position_keys,
            route.requires_operations_reference,
            preview["source"],
        )
        if preview["provisional"]
        else route
    )
    if previous and previous["source"] == preview["source"]:
        return effective_route
    # Reads/decisions on final documents never rewrite historical snapshots.
    if (
        str(getattr(documento, "estado", "")).lower()
        not in {"borrador", "enviado", "control_presupuestal", "rechazado"}
        or getattr(documento, "pagado_en", None)
        or str(getattr(documento, "estado_pago", "") or "").lower()
        in {"pagado", "en_proceso_pago"}
    ):
        if previous:
            return ProjectAuthorizationRoute(
                _keys(previous["eligible_position_keys"]),
                bool(previous["requires_operations_reference"]),
                str(previous["source"]),
            )
        return effective_route
    # Unattributed reads may resolve live policy, but cannot replace evidence.
    if previous and preview["provisional"] and audit_actor_id is None:
        return effective_route
    if previous and preview["provisional"]:
        session.add(
            Aprobacion(
                tipo_entidad="documento",
                entidad_id=UUID(document_id),
                aprobador_id=UUID(str(audit_actor_id)),
                accion="editar",
                comentario=json.dumps(
                    {
                        "action": "actualizar_ruta_autorizacion",
                        "policy": PROVISIONAL_SOURCE_PREFIX,
                        "audit_context": audit_context or {},
                        "before": dict(previous),
                        "context": preview["context"],
                        "after": {
                            "source": preview["source"],
                            "eligible_position_keys": list(
                                route.eligible_position_keys
                            ),
                            "eligible_empleado_ids": preview["eligible_employee_ids"],
                            "requires_operations_reference": route.requires_operations_reference,
                        },
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )
    await session.execute(
        text(
            """INSERT INTO documento_authorization_routes (
            documento_id,eligible_position_keys,eligible_empleado_ids,
            requires_operations_reference,source
        ) VALUES (:documento_id,CAST(:positions AS jsonb),CAST(:employee_ids AS jsonb),
                  :requires_reference,:source)
        ON CONFLICT (documento_id) DO UPDATE SET
            eligible_position_keys=EXCLUDED.eligible_position_keys,
            eligible_empleado_ids=EXCLUDED.eligible_empleado_ids,
            requires_operations_reference=EXCLUDED.requires_operations_reference,
            source=EXCLUDED.source,resolved_at=NOW()"""
        ),
        {
            "documento_id": document_id,
            "positions": json.dumps(route.eligible_position_keys),
            "employee_ids": json.dumps(preview["eligible_employee_ids"]),
            "requires_reference": route.requires_operations_reference,
            "source": preview["source"],
        },
    )
    return effective_route


async def invalidate_document_route(
    session: AsyncSession, documento_id: object
) -> None:
    """Legacy routes are discarded; versioned routes are context-validated on send."""
    if await provisional_policy_is_active(session):
        return
    await session.execute(
        text(
            "DELETE FROM documento_authorization_routes WHERE documento_id = :documento_id"
        ),
        {"documento_id": str(documento_id)},
    )


def has_operations_reference(documento: object) -> bool:
    """Return the presence of a historical operational reference, not authority."""
    return bool(str(getattr(documento, "referencia_operaciones", None) or "").strip())


def document_route_approver_sql(
    document_alias: str = "documentos",
    actor_parameter: str = "route_employee_id",
    *,
    include_supervisor: bool = True,
) -> str:
    """Shared read-only SQL policy; arguments are trusted query identifiers."""
    if not document_alias.isidentifier() or not actor_parameter.isidentifier():
        raise ValueError("Invalid query identifier")
    reference = (
        f"NULLIF(BTRIM({document_alias}.referencia_operaciones), '') IS NOT NULL"
    )
    valid = (
        "route.eligible_position_keys = '[\"director_operaciones\"]'::jsonb "
        "AND route.requires_operations_reference = TRUE"
    )
    snapshot = f"""EXISTS (
        SELECT 1 FROM documento_authorization_routes route
        WHERE route.documento_id = {document_alias}.id AND ({valid})
          AND :{actor_parameter} IN (
              SELECT jsonb_array_elements_text(route.eligible_empleado_ids))
    )"""
    fallback = f"""NOT EXISTS (
        SELECT 1 FROM documento_authorization_routes route
        WHERE route.documento_id = {document_alias}.id AND ({valid})
    ) AND EXISTS (
        SELECT 1 FROM authorization_position_assignments a
        JOIN authorization_positions p
          ON p.position_key = a.position_key AND p.active = TRUE
        JOIN empleados operations_holder
          ON operations_holder.id = a.empleado_id
         AND operations_holder.activo = TRUE
        WHERE a.active = TRUE AND a.position_key = 'director_operaciones'
          AND a.empleado_id::text = :{actor_parameter}
    )"""
    natural = f"""EXISTS (
        SELECT 1 FROM documento_authorization_routes route
        WHERE route.documento_id = {document_alias}.id
          AND :{actor_parameter} IN (
              SELECT jsonb_array_elements_text(route.eligible_empleado_ids))
    )"""
    legacy = f"""(
        (({reference}) AND (({snapshot}) OR ({fallback})))
        OR (NOT ({reference}) AND ({natural}))
    )"""
    active = (
        f"({provisional_policy_active_sql()}) AND "
        f"{document_alias}.tipo IN ('SOLICITUD','INFORME')"
    )
    beneficiary = effective_beneficiary_sql(document_alias)
    project = effective_tournament_sql(document_alias)
    exception = f"""EXISTS (
        SELECT 1 FROM authorization_position_assignments protected
        JOIN authorization_positions pp ON pp.position_key=protected.position_key
            AND pp.active=TRUE
        JOIN empleados protected_person ON protected_person.id=protected.empleado_id
            AND protected_person.activo=TRUE
        WHERE protected.position_key='aprobaciones_escalan_gn'
          AND protected.active=TRUE AND (
            protected.empleado_id={document_alias}.empleado_id
            OR protected.empleado_id=({beneficiary})))"""
    assigned = f"""EXISTS (
        SELECT 1 FROM authorization_position_assignments approver
        JOIN authorization_positions ap ON ap.position_key=approver.position_key
            AND ap.active=TRUE
        WHERE approver.active=TRUE AND approver.empleado_id::text=:{actor_parameter}
          AND (({exception}) AND approver.position_key='aprobador_federico_gn'
          OR NOT ({exception}) AND EXISTS (
            SELECT 1 FROM project_authorization_rules rule
            WHERE rule.tournament_id=({project}) AND rule.active=TRUE
              AND approver.position_key IN (
                  SELECT jsonb_array_elements_text(rule.eligible_position_keys)))))"""
    supervisor = (
        approval_supervisor_sql(actor_parameter) if include_supervisor else "FALSE"
    )
    return f"""(
        ({active} AND (({assigned}) OR ({supervisor})))
        OR (NOT ({active}) AND ({legacy}))
    ) AND EXISTS (SELECT 1 FROM empleados active_actor
        WHERE active_actor.id::text=:{actor_parameter} AND active_actor.activo = TRUE)"""


def effective_tournament_sql(document_alias: str) -> str:
    return (
        f"COALESCE({document_alias}.torneo_id, (SELECT cg.torneo_id "
        f"FROM cuentas_de_gastos cg WHERE cg.id={document_alias}.cuenta_gastos_id))"
    )


def effective_beneficiary_sql(document_alias: str) -> str:
    return f"""CASE WHEN {document_alias}.beneficiario_empleado_id IS NOT NULL
        THEN {document_alias}.beneficiario_empleado_id
        WHEN {document_alias}.beneficiario_proveedor_cliente_id IS NOT NULL THEN NULL
        WHEN {document_alias}.cuenta_gastos_id IS NOT NULL THEN (
            SELECT CASE WHEN cg.beneficiario_proveedor_cliente_id IS NOT NULL THEN NULL
                   ELSE COALESCE(cg.beneficiario_empleado_id,cg.empleado_id) END
            FROM cuentas_de_gastos cg WHERE cg.id={document_alias}.cuenta_gastos_id)
        ELSE {document_alias}.empleado_id END"""


def approval_supervisor_sql(actor_parameter: str = "route_employee_id") -> str:
    """Only explicitly assigned active GV supervision, never generic admin."""
    if not actor_parameter.isidentifier():
        raise ValueError("Invalid query identifier")
    return f"""EXISTS (
        SELECT 1 FROM authorization_position_assignments supervisor
        JOIN authorization_positions sp ON sp.position_key=supervisor.position_key
            AND sp.active=TRUE
        JOIN empleados supervisor_actor ON supervisor_actor.id=supervisor.empleado_id
            AND supervisor_actor.activo=TRUE
        WHERE supervisor.position_key='supervision_aprobaciones_federico_gv'
          AND supervisor.active=TRUE AND supervisor.empleado_id::text=:{actor_parameter}
          AND ({provisional_policy_active_sql()})
    )"""


async def actor_is_approval_supervisor(
    session: AsyncSession, *, actor_id: object
) -> bool:
    result = await session.execute(
        text("SELECT " + approval_supervisor_sql("actor_id")),
        {"actor_id": str(actor_id)},
    )
    return result.scalar_one_or_none() is True


async def project_excludes_operations_reference(
    session: AsyncSession, *, tournament_id: object = None
) -> bool:
    """Explicit project identity is checked before any allocator is invoked."""
    if not tournament_id:
        return False
    result = await session.execute(
        text("SELECT name FROM tournaments WHERE id=:tournament_id"),
        {"tournament_id": str(tournament_id)},
    )
    name = result.scalar_one_or_none()
    return isinstance(name, str) and name.strip().casefold() in NO_OPERATIONS_PROJECTS


async def prepare_document_authorization_route(
    session: AsyncSession, documento: object, *, audit_actor_id: object = None
) -> Optional[ProjectAuthorizationRoute]:
    """Prepare a nonterminal workflow document without approving or committing."""
    if getattr(documento, "pagado_en", None) or str(
        getattr(documento, "estado_pago", "") or ""
    ).lower() in {"pagado", "en_proceso_pago"}:
        return None
    if str(getattr(documento, "estado", "")).lower() not in {
        "borrador",
        "enviado",
        "control_presupuestal",
        "rechazado",
    }:
        return None
    route = await resolve_and_snapshot_document_route(
        session,
        documento,
        **({"audit_actor_id": audit_actor_id} if audit_actor_id is not None else {}),
    )
    # Editable drafts can move from an excluded project to a normal one. Reapply
    # the same creator emitter used at creation, independently of approver rules.
    # Pending historical documents never gain a folio through this draft repair.
    if (
        route
        and route.source.startswith(PROVISIONAL_SOURCE_PREFIX + ":")
        and str(getattr(documento, "estado", "")).lower() == "borrador"
        and not has_operations_reference(documento)
    ):
        from .documento_service import allocate_referencia_operaciones_for_empleado

        context = await resolve_document_route_context(session, documento)
        creator = await session.get(Empleado, UUID(context["requester_id"]))
        if creator is not None:
            reference = await allocate_referencia_operaciones_for_empleado(
                session, creator, torneo_id=context["tournament_id"]
            )
            if reference is not None:
                setattr(documento, "referencia_operaciones", reference)
    if (
        route
        and route.requires_operations_reference
        and not has_operations_reference(documento)
        and not await project_excludes_operations_reference(
            session,
            tournament_id=(await resolve_document_route_context(session, documento))[
                "tournament_id"
            ],
        )
    ):
        from .documento_service import allocate_next_referencia_operaciones

        setattr(
            documento,
            "referencia_operaciones",
            await allocate_next_referencia_operaciones(session),
        )
    if route and route.source.startswith(PROVISIONAL_SOURCE_PREFIX + ":"):
        return route
    if not has_operations_reference(documento):
        return route
    if (
        route
        and route.eligible_position_keys == (DIRECTOR_OPERACIONES,)
        and route.requires_operations_reference
    ):
        return route
    holders = await session.execute(
        text(
            "SELECT a.empleado_id FROM authorization_position_assignments a "
            "JOIN authorization_positions p ON p.position_key = a.position_key "
            "AND p.active = TRUE "
            "JOIN empleados e ON e.id = a.empleado_id AND e.activo = TRUE "
            "WHERE a.active = TRUE AND a.position_key = 'director_operaciones'"
        )
    )
    employee_ids = [str(value) for value in holders.scalars().all()]
    await session.execute(
        text(
            """
        INSERT INTO documento_authorization_routes (
            documento_id, eligible_position_keys, eligible_empleado_ids,
            requires_operations_reference, source
        ) VALUES (:documento_id, CAST(:positions AS jsonb),
                  CAST(:employee_ids AS jsonb), TRUE, 'operations_reference')
        ON CONFLICT (documento_id) DO UPDATE SET
            eligible_position_keys = EXCLUDED.eligible_position_keys,
            eligible_empleado_ids = EXCLUDED.eligible_empleado_ids,
            requires_operations_reference = TRUE, source = EXCLUDED.source,
            resolved_at = NOW()
    """
        ),
        {
            "documento_id": str(getattr(documento, "id")),
            "positions": json.dumps([DIRECTOR_OPERACIONES]),
            "employee_ids": json.dumps(employee_ids),
        },
    )
    return ProjectAuthorizationRoute(
        (DIRECTOR_OPERACIONES,), True, "operations_reference"
    )


async def actor_route_approver_document_ids(
    session: AsyncSession, *, actor_id: object, documento_ids: Iterable[Any]
) -> set[Any]:
    """Apply the canonical route policy to a queue in one database round trip."""
    ids = list(documento_ids)
    if not ids:
        return set()
    result = await session.execute(
        select(Documento.id)
        .where(Documento.id.in_(ids), text(document_route_approver_sql()))
        .params(route_employee_id=str(actor_id))
    )
    return set(result.scalars().all())


async def actor_is_route_approver(
    session: AsyncSession,
    *,
    actor_id: object,
    documento_id: object,
    include_supervisor: bool = True,
) -> bool:
    """Apply folio policy, retaining valid snapshotted Operations holders."""
    result = await session.execute(
        text(
            "SELECT 1 FROM documentos WHERE documentos.id = :documento_id AND ("
            + document_route_approver_sql(
                actor_parameter="actor_id", include_supervisor=include_supervisor
            )
            + ") LIMIT 1"
        ),
        {"documento_id": str(documento_id), "actor_id": str(actor_id)},
    )
    return result.scalar_one_or_none() is not None


async def route_approvers_for_document(
    session: AsyncSession, documento_id: object
) -> Optional[list[Any]]:
    """Return effective holders; None exclusively means the natural legacy lane."""
    route = await session.execute(
        text(
            """
        SELECT 1 FROM documentos d WHERE d.id = :documento_id AND (
            NULLIF(BTRIM(d.referencia_operaciones), '') IS NOT NULL OR
            EXISTS (SELECT 1 FROM authorization_positions policy_activation
                    WHERE policy_activation.position_key='aprobaciones_escalan_gn'
                    AND policy_activation.active=TRUE
                    AND d.tipo IN ('SOLICITUD','INFORME')) OR
            EXISTS (SELECT 1 FROM documento_authorization_routes r
                    WHERE r.documento_id = d.id)
        )
    """
        ),
        {"documento_id": str(documento_id)},
    )
    if route.scalar_one_or_none() is None:
        return None
    employees = await session.execute(
        text(
            "SELECT e.id FROM empleados e CROSS JOIN documentos "
            "WHERE documentos.id = :documento_id AND e.activo = TRUE AND ("
            + document_route_approver_sql(
                actor_parameter="route_employee_id", include_supervisor=False
            ).replace(":route_employee_id", "e.id::text")
            + ") ORDER BY e.nombre"
        ),
        {"documento_id": str(documento_id)},
    )
    return [
        await session.get(Empleado, employee_id)
        for employee_id in employees.scalars().all()
    ]


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
    active = await session.execute(
        text(
            "SELECT id::text FROM empleados WHERE activo = TRUE "
            "AND id = ANY(CAST(:employee_ids AS uuid[]))"
        ),
        {"employee_ids": valid},
    )
    if set(active.scalars().all()) != set(valid):
        raise ValueError("Todos los titulares deben ser empleados activos.")
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
