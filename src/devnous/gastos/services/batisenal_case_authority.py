"""Current object authority for the single synthetic-installation case.

Trusted identity comes from existing transport verification, never tool JSON.
The caller must use the explicitly composed synthetic transaction. This module
is not a financial-company resolver and never assigns a real report to a tenant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.orm import load_only, raiseload

from samchat.private_plugin.contracts import Identity
from samchat.private_plugin.oauth_store import GRANTS, LINKS

from ..models import CuentaDeGastos, Documento, Empleado, Tournament
from ..routes.batisenal_routes import resolve_web_principal
from ..routes.dependencies import _load_effective_permissions
from . import batisenal_pilot_service as pilots
from . import batisenal_service as owner
from .access_control_service import can_access_tool, explicit_tool_decision
from .documento_telegram import requester_can_view_document


@dataclass(frozen=True)
class CaseAuthority:
    binding: dict
    actor: object
    principal: owner.Principal
    report: Documento
    grant: dict
    expires_at: int
    observed_at: datetime


def denied() -> None:
    raise owner.BatisenalError("CASE_AUTHORITY_UNAVAILABLE", 403)


def verified_provenance(
    binding, *, issuer, audience, client_id, registration_sha256=None
):
    """Validate owner-bound provenance against this server's approved registration.

    Missing migration fields fail closed; an argument from the model never enters
    this function. A stub cannot be promoted by merely changing a display label.
    """
    kind = binding.get("agent_kind")
    if kind == "synthetic_stub":
        if registration_sha256 is not None or any(
            binding.get(key) is not None
            for key in (
                "agent_client_id",
                "agent_issuer",
                "agent_resource",
                "agent_registration_sha256",
            )
        ):
            denied()
    elif kind == "ai_agent":
        if (
            not isinstance(registration_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", registration_sha256)
            or (
                binding.get("agent_client_id"),
                binding.get("agent_issuer"),
                binding.get("agent_resource"),
                binding.get("agent_registration_sha256"),
            )
            != (client_id, issuer, audience, registration_sha256)
        ):
            denied()
    else:
        denied()
    return kind


async def current_case_authority(
    session,
    identity: Identity,
    *,
    case_ref: str,
    issuer: str,
    audience: str,
    client_id: str,
    registration_sha256: str | None = None,
    write: bool = False,
) -> CaseAuthority:
    """Revalidate with canonical owners on this same locked connection.

    No promotion, implicit support-to-finance authority, lookup by display label,
    report normalization, remote source access or business write occurs here.
    """
    if not isinstance(identity, Identity) or type(case_ref) is not str:
        denied()
    marker = (
        await session.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM support_batisenal_v01_transactions "
                "WHERE transaction_id=pg_current_xact_id_if_assigned())"
            )
        )
    ).scalar_one()
    if not marker:
        raise owner.BatisenalError("V01_WRITER_PROTOCOL_REQUIRED", 503)
    binding = (
        (
            await session.execute(
                text(
                    "SELECT b.*, g.epoch AS policy_epoch FROM"
                    " support_batisenal_v01_case_binding b CROSS JOIN"
                    " support_batisenal_v01_policy_guard g WHERE b.singleton AND"
                    " g.singleton"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if binding is None:
        denied()
    if (
        case_ref != str(binding.ticket_id)
        or identity.actor_id != str(binding.actor_id)
        or identity.installation_id != binding.installation_id
        or identity.organization_id != binding.organization_namespace
        or identity.issuer != issuer
        or identity.audience != audience
        or not identity.active
        or identity.revoked
        or "batisenal.support" not in identity.oauth_scopes
        or binding.synthetic_only is not True
    ):
        denied()
    source_kind = verified_provenance(
        binding,
        issuer=issuer,
        audience=audience,
        client_id=client_id,
        registration_sha256=registration_sha256,
    )
    grant = (
        (
            await session.execute(
                select(GRANTS).where(
                    GRANTS.c.grant_id == identity.grant_id,
                    GRANTS.c.issuer == issuer,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if grant is None or (
        grant.active is not True
        or grant.revoked is not False
        or str(grant.employee_id) != identity.actor_id
        or grant.installation_id != identity.installation_id
        or grant.organization_id != identity.organization_id
        or grant.profile_id != identity.profile_id
        or grant.client_id != client_id
        or "batisenal.support" not in grant.scopes_json
    ):
        denied()
    link = (
        (
            await session.execute(
                select(LINKS).where(
                    LINKS.c.link_id == grant.link_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if link is None or (
        link.active is not True
        or str(link.employee_id) != identity.actor_id
        or link.issuer != grant.issuer
        or link.subject != grant.subject
        or link.installation_id != grant.installation_id
        or link.organization_id != grant.organization_id
        or link.profile_id != grant.profile_id
    ):
        denied()
    actor, principal, report = await _business_authority(
        session, binding, case_ref, write=write
    )
    pilot = (
        (
            await session.execute(
                select(pilots.pilots).where(
                    pilots.pilots.c.ticket_id == binding.ticket_id,
                    pilots.pilots.c.employee == actor.id,
                    pilots.pilots.c.profile_id == identity.profile_id,
                    pilots.pilots.c.issuer == issuer,
                    pilots.pilots.c.installation_id == identity.installation_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    fixture = (
        (
            await session.execute(
                select(pilots.fixtures).where(
                    pilots.fixtures.c.ticket_id == binding.ticket_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
    if (
        pilot is None
        or fixture is None
        or fixture.active is not True
        or pilot.revoked is not False
        or pilot.organization != identity.organization_id
        or pilot.project != binding.support_project
        or pilot.source_kind != source_kind
        or fixture.attestation_sha256 != binding.attestation_sha256
        or pilot.expires_at > pilot.starts_at + 3600
        or pilot.starts_at > now.timestamp()
        or fixture.starts_at is None
        or fixture.expires_at is None
        or fixture.starts_at > now.timestamp()
        or fixture.expires_at != fixture.starts_at + 3600
    ):
        denied()
    expires = min(
        identity.expires_at, grant.expires_at, pilot.expires_at, fixture.expires_at
    )
    if now.timestamp() >= expires:
        raise owner.BatisenalError("CASE_GRANT_EXPIRED", 403)
    return CaseAuthority(
        dict(binding), actor, principal, report, dict(grant), expires, now
    )


async def _business_authority(session, binding, case_ref, *, write=False):
    employee = (
        await session.execute(
            select(Empleado)
            .where(
                Empleado.id == binding.actor_id,
            )
            .options(
                raiseload("*"),
                load_only(
                    Empleado.id,
                    Empleado.activo,
                    Empleado.rol,
                    Empleado.departamento,
                ),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if employee is None or employee.activo is not True:
        denied()
    actor = SimpleNamespace(
        id=str(employee.id),
        activo=employee.activo,
        rol=employee.rol,
        departamento=employee.departamento,
    )
    # Denials prevail, including for admins; ownership below never uses role
    # elevation or inherited support/Direction financial visibility.
    for key in ("gastos.informes", "gastos.solicitudes"):
        if await explicit_tool_decision(session, actor, key, "ver") is False:
            denied()
        if not await can_access_tool(session, actor, key, "ver"):
            denied()
    principal = await resolve_web_principal(session, actor, "POST" if write else "GET")
    tokens = await _load_effective_permissions(session, actor.id)
    if (
        not principal.permits(
            binding.organization_namespace, binding.support_project, "request"
        )
        or f"batisenal:{binding.organization_namespace}:{binding.support_project}:agent"
        not in tokens
    ):
        denied()
    case = await owner.visible_case(session, principal, case_ref)
    if case.actor != actor.id or case.requester_empleado_id != employee.id:
        denied()
    report = (
        await session.execute(
            select(Documento)
            .where(
                Documento.id == binding.report_id,
            )
            .options(
                raiseload("*"),
                load_only(
                    Documento.id,
                    Documento.empleado_id,
                    Documento.tipo,
                    Documento.torneo_id,
                    Documento.cuenta_gastos_id,
                    Documento.estado,
                    Documento.currency,
                    Documento.monto_total,
                ),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        report is None
        or report.tipo != "INFORME"
        or not requester_can_view_document(employee, report)
    ):
        denied()
    # Same project precedence as resolve_and_snapshot_document_route, without
    # calling its snapshot writer. Conflicting explicit references fail closed.
    account_project = None
    if report.cuenta_gastos_id:
        account_project = (
            await session.execute(
                select(CuentaDeGastos.torneo_id).where(
                    CuentaDeGastos.id == report.cuenta_gastos_id,
                )
            )
        ).scalar_one_or_none()
    if report.torneo_id and account_project and report.torneo_id != account_project:
        denied()
    project_id = report.torneo_id or account_project
    project = (
        await session.execute(
            select(Tournament.id).where(
                Tournament.id == project_id,
                Tournament.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if project is None or project != binding.tournament_id:
        denied()
    return actor, principal, report


async def current_browser_scope(session, employee, case_ref, *, provenance=None):
    """Canonical authenticated browser only; establishes consent scope, no grant."""
    marker = (
        await session.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM support_batisenal_v01_transactions WHERE"
                " transaction_id=pg_current_xact_id_if_assigned())"
            )
        )
    ).scalar_one()
    if not marker:
        raise owner.BatisenalError("V01_WRITER_PROTOCOL_REQUIRED", 503)
    binding = (
        (
            await session.execute(
                text(
                    "SELECT b.*, g.epoch AS policy_epoch FROM"
                    " support_batisenal_v01_case_binding b CROSS JOIN"
                    " support_batisenal_v01_policy_guard g WHERE b.singleton AND"
                    " g.singleton"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        binding is None
        or binding.synthetic_only is not True
        or str(binding.ticket_id) != case_ref
        or str(binding.actor_id) != str(employee.id)
        or employee.activo is not True
    ):
        denied()
    if provenance is not None:
        verified_provenance(binding, **provenance)
    return await _business_authority(session, binding, case_ref, write=True)
