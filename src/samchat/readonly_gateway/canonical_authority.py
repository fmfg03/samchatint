"""Offline-ready canonical composition; never installed into a production factory.

The binding provider is authenticated server context, never model arguments.
Its ceilings restrict canonical access; they cannot grant it. Authority sessions
must be fresh READ COMMITTED, READ ONLY transactions separate from data sessions.
No credentials, schema creation, confirmation, or executor is implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import text

from .contracts import ReadError
from .expanded import Authority, DomainScope, digest
from .expanded_catalog import DATASETS, Query


@dataclass(frozen=True)
class ServerBinding:
    actor_binding: str
    employee_id: UUID
    installation: str
    generation: str
    policy_version: str
    expires_at: datetime
    ceilings: frozenset[DomainScope]


async def minimal_actor(session: Any, employee_id: UUID) -> Any:
    # Explicit projection: no password hashes, account numbers, or contact list.
    row = (
        (
            await session.execute(
                text(
                    "SELECT id,activo,rol,departamento,correo FROM empleados WHERE id=:id"
                ),
                {"id": str(employee_id)},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not row["activo"]:
        raise ReadError("AUTH_REQUIRED")
    actor = SimpleNamespace(**dict(row))
    actor.id = UUID(str(actor.id))
    return actor


class MinimalActorSession:
    """Canonical AMEX owners get a fresh minimal actor, not a credential row."""

    def __init__(self, session: Any):
        self.session = session

    async def get(self, model, identity, **kwargs):
        from devnous.gastos.models import Empleado

        if kwargs.get("with_for_update"):
            raise ReadError("WRITE_LOCK_FORBIDDEN")
        if model is Empleado:
            return await minimal_actor(self.session, UUID(str(identity)))
        return await self.session.get(model, identity, **kwargs)

    async def execute(self, *args, **kwargs):
        return await self.session.execute(*args, **kwargs)


class CanonicalAuthorityResolver:
    def __init__(
        self, binding_provider: Callable[[], ServerBinding], authority_session_scope
    ):
        self.binding_provider = binding_provider
        self.authority_session_scope = authority_session_scope

    def binding(self) -> ServerBinding:
        value = self.binding_provider()
        if (
            not isinstance(value, ServerBinding)
            or not isinstance(value.employee_id, UUID)
            or not all(
                (
                    value.actor_binding,
                    value.installation,
                    value.generation,
                    value.policy_version,
                )
            )
            or value.expires_at.tzinfo is None
            or value.expires_at <= datetime.now(timezone.utc)
            or not isinstance(value.ceilings, frozenset)
            or len(value.ceilings) > 20
        ):
            raise ReadError("AUTH_REQUIRED")
        for scope in value.ceilings:
            if scope.dataset not in DATASETS or not 1 <= len(scope.object_ids) <= 100:
                raise ReadError("AUTH_REQUIRED")
            for identifier in (*scope.object_ids, scope.tournament_id):
                if str(UUID(identifier)) != identifier:
                    raise ReadError("AUTH_REQUIRED")
            if DATASETS[scope.dataset].version_required:
                if scope.object_ids != frozenset({scope.version_id}):
                    raise ReadError("AUTH_REQUIRED")
            elif scope.version_id:
                raise ReadError("AUTH_REQUIRED")
        return value

    async def _amex(self, session, binding, object_id):
        from devnous.gastos.services.access_control_service import (
            explicit_tool_decision,
        )
        from devnous.gastos.services.amex_statement_workflow_service import (
            _authorize,
            _statement,
        )
        from devnous.gastos.services.amex_direct_common import load_statement

        safe = MinimalActorSession(session)
        actor = await minimal_actor(session, binding.employee_id)
        # Finance identity before even the exact-ID mode probe; both owner paths
        # repeat identity/role checks and enforce their own source constraints.
        await _authorize(safe, actor)
        mode = (
            await session.execute(
                text("SELECT workflow_mode FROM amex_statement_imports WHERE id=:id"),
                {"id": str(UUID(object_id))},
            )
        ).scalar_one_or_none()
        if mode == "legacy":
            if (
                await explicit_tool_decision(
                    session, actor, "admin.contabilidad", "ver"
                )
                is False
            ):
                raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
            statement = await _statement(safe, UUID(object_id), locked=False)
            proof = {"mode": mode, "card": str(statement.card_account_id)}
        elif mode == "direct":
            statement, state, context, config = await load_statement(
                safe,
                binding.employee_id,
                UUID(object_id),
                "ver",
                finance=True,
                locked=False,
            )
            proof = {
                "mode": mode,
                "card": str(statement.card_account_id),
                "config": config,
                "context": str(getattr(context, "id", "")),
                "context_hash": getattr(context, "config_hash", None),
                "state_context": str(getattr(state, "context_id", "")),
            }
        else:
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        return statement, digest(proof)

    async def amex_reader(self, session, authority: Authority, object_id: str):
        object_id = str(UUID(str(object_id)))
        binding = self.binding()
        if (
            binding.actor_binding != authority.actor_binding
            or binding.installation != authority.installation
            or binding.policy_version != authority.policy_version
            or binding.expires_at != authority.expires_at
            or not any(
                s.dataset == "amex.statement" and object_id in s.object_ids
                for s in authority.scopes
            )
            or not any(
                s.dataset == "amex.statement" and object_id in s.object_ids
                for s in binding.ceilings
            )
        ):
            raise ReadError("AUTH_CHANGED")
        return (await self._amex(session, binding, object_id))[0]

    async def __call__(self, query: Query | None) -> Authority:
        from devnous.gastos.routes.dependencies import (
            _load_effective_permissions,
            require_admin_finanzas,
        )
        from devnous.gastos.routes.admin_routes import _budget_can_view
        from devnous.gastos.routes.user_routes import _can_read_documento
        from devnous.gastos.services.access_control_service import (
            explicit_tool_decision,
        )
        from devnous.gastos.services.payment_run_service import can_access_payment_run

        binding = self.binding()
        scopes, proofs = set(), []
        # This context exits before financial reads, and opens anew on each call.
        async with self.authority_session_scope() as session:
            actor = await minimal_actor(session, binding.employee_id)
            actor.effective_permissions = await _load_effective_permissions(
                session, actor.id, fail_closed=True
            )
            actor.permissions = actor.effective_permissions
            proofs.append(
                (
                    str(actor.id),
                    actor.rol,
                    actor.departamento,
                    actor.correo,
                    sorted(actor.effective_permissions),
                )
            )
            for scope in sorted(
                binding.ceilings,
                key=lambda s: (
                    s.dataset,
                    s.tournament_id,
                    s.version_id,
                    sorted(s.object_ids),
                ),
            ):
                if query and not (
                    scope.dataset == query.dataset
                    and scope.tournament_id == query.tournament_id
                    and scope.version_id == query.version_id
                ):
                    continue
                tool = {
                    "solicitud": "gastos.solicitudes",
                    "budget.snapshot": "presupuestos.ingresos",
                    "ar.summary": "admin.finanzas",
                    "payment_run": "admin.finanzas",
                }.get(scope.dataset)
                if (
                    tool
                    and await explicit_tool_decision(session, actor, tool, "ver")
                    is False
                ):
                    continue
                if scope.dataset == "budget.snapshot" and not _budget_can_view(actor):
                    continue
                if scope.dataset == "ar.summary":
                    try:
                        await require_admin_finanzas().dependency(
                            current_empleado=actor
                        )
                    except Exception:
                        continue
                if scope.dataset == "payment_run" and not can_access_payment_run(actor):
                    continue
                allowed = set()
                for object_id in sorted(scope.object_ids):
                    if query and object_id not in query.object_ids:
                        continue
                    if scope.dataset == "amex.statement":
                        # Canonical AMEX failures fail the whole request closed.
                        _, proof = await self._amex(session, binding, object_id)
                        proofs.append((object_id, proof))
                    elif scope.dataset in {"solicitud", "payment_run"}:
                        row = (
                            (
                                await session.execute(
                                    text(
                                        "SELECT id,empleado_id,tipo,estado,pagado_en,referencia_operaciones "
                                        "FROM documentos WHERE id=:id AND torneo_id=:tor AND tipo='SOLICITUD'"
                                    ),
                                    {"id": object_id, "tor": scope.tournament_id},
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if row is None:
                            continue
                        doc = SimpleNamespace(**dict(row))
                        doc.empleado_id = UUID(str(doc.empleado_id))
                        if scope.dataset == "solicitud" and not _can_read_documento(
                            doc, actor
                        ):
                            continue
                        proofs.append(dict(row))
                    else:
                        row = (
                            (
                                await session.execute(
                                    text(
                                        "SELECT id,status,edition_year FROM budget_versions WHERE id=:id "
                                        "AND status IN ('approved','frozen') AND EXISTS (SELECT 1 FROM budget_lines "
                                        "WHERE budget_version_id=:id AND tournament_id=:tor)"
                                    ),
                                    {"id": object_id, "tor": scope.tournament_id},
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if row is None or (
                            query and row["edition_year"] != query.start.year
                        ):
                            continue
                        proofs.append(dict(row))
                    allowed.add(object_id)
                if allowed:
                    scopes.add(
                        DomainScope(
                            scope.dataset,
                            scope.tournament_id,
                            frozenset(allowed),
                            scope.version_id,
                        )
                    )
        if self.binding() != binding:
            raise ReadError("AUTH_CHANGED")
        return Authority(
            binding.actor_binding,
            binding.installation,
            digest((binding.generation, str(binding.employee_id), proofs)),
            binding.policy_version,
            binding.expires_at,
            frozenset(scopes),
        )


def compose_candidate(
    *,
    binding_provider,
    authority_session_scope,
    data_session_scope,
    audit,
    source_commit,
):
    """Server-only construction helper. Does not register a tool or open a connection."""
    from .expanded import ExpandedReads
    from .expanded_adapters import CanonicalDatasetAdapter

    resolver = CanonicalAuthorityResolver(binding_provider, authority_session_scope)
    adapter = CanonicalDatasetAdapter(
        data_session_scope, amex_authorized_reader=resolver.amex_reader
    )
    return ExpandedReads(
        resolve=lambda: None,
        resolve_async=resolver,
        adapters={name: adapter for name in DATASETS},
        audit=audit,
        source_commit=source_commit,
    )
