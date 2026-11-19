"""Inert support-only composition. No host credentials, Events, listener or keys.

HTTP login/consent mounting remains the host's job. Public methods below consume
an authenticated SamChat Request, not user/organization arguments from an agent.
"""

from contextvars import ContextVar
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import insert, select, update
from starlette.requests import Request

from devnous.gastos.models import Empleado
from devnous.gastos.routes.batisenal_routes import read_payload, resolve_web_principal
from devnous.gastos.services import batisenal_service as owner
from devnous.gastos.services.batisenal_pilot_service import consents
from samchat.batisenal_transport.http import create_app, current_connection
from samchat.private_plugin.authorization import (
    AuthorizationConfig,
    resolve_session_principal,
)
from samchat.private_plugin.contracts import Denied
from samchat.private_plugin.oauth_adapters import SQLAlchemyIdentityRecords
from samchat.private_plugin.oauth_flow import BrowserSession, LocalOAuthFlow
from samchat.private_plugin.oauth_identity import CurrentEmployee, OAuthIdentityProvider
from samchat.private_plugin.oauth_store import LINKS

SUPPORT_SCOPE = frozenset({"batisenal.support"})
_request: ContextVar[Request] = ContextVar("support_oauth_request")
_bearer: ContextVar[str | None] = ContextVar("support_request_bearer", default=None)


@dataclass(frozen=True)
class SupportAuthorizationConfig(AuthorizationConfig):
    def __post_init__(self):
        # Reuse strict URL/client validation; do not widen legacy read-only config.
        AuthorizationConfig(
            self.issuer,
            self.resource,
            self.client_id,
            self.redirect_uri,
            frozenset({"batisenal:read"}),
        )
        if type(self.scopes) is not frozenset or self.scopes != SUPPORT_SCOPE:
            raise ValueError("SUPPORT_SCOPE_REQUIRED")


class RequestBearer:
    """Credential lifetime limited to one ASGI request, including cancellation."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = None
        if scope["type"] == "http":
            values = [
                v for k, v in scope.get("headers", ()) if k.lower() == b"authorization"
            ]
            if len(values) == 1 and values[0].startswith(b"Bearer "):
                token = values[0][7:].decode("latin1")
        context = _bearer.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _bearer.reset(context)


class SupportPilot:
    """One server-selected fixture, explicit consent, actual JWT verification.

    Constructor accepts caller-owned stores/key adapters; it never provisions
    schema, credentials, employees or permissions. Disabled unless explicit.
    """

    def __init__(
        self,
        *,
        sessions,
        employee_dependency,
        authorization,
        resource,
        oauth_store,
        signer,
        audit,
        pilot,
        clock,
        enabled=False,
        identity_records_store=None,
        case_service=None,
    ):
        if (
            not isinstance(authorization, SupportAuthorizationConfig)
            or not oauth_store.support_only
            or resource.allowed_scopes != SUPPORT_SCOPE
            or resource.allowed_clients != frozenset({authorization.client_id})
            or (authorization.issuer, authorization.resource)
            != (resource.issuer, resource.audience)
            or (resource.issuer, resource.installation_id)
            != (pilot.issuer, pilot.installation_id)
            or (
                oauth_store.issuer,
                oauth_store.installation_id,
                oauth_store.organization_id,
            )
            != (pilot.issuer, pilot.installation_id, pilot.organization)
        ):
            raise ValueError("SUPPORT_COMPOSITION_INVALID")
        if case_service is None:
            raise ValueError("CASE_COMPOSITION_REQUIRED")
        if (
            case_service.sessions is not sessions
            or (case_service.issuer, case_service.audience, case_service.client_id)
            != (resource.issuer, resource.audience, authorization.client_id)
            or pilot.source_kind
            != ("ai_agent" if case_service.registration_sha256 else "synthetic_stub")
            or identity_records_store is None
        ):
            raise ValueError("CASE_COMPOSITION_INVALID")
        self.case_service = case_service
        self.sessions, self.employee_dependency, self.pilot = (
            sessions,
            employee_dependency,
            pilot,
        )
        self.enabled, self.oauth_store, self.resource = enabled, oauth_store, resource
        if identity_records_store is not None:
            if (
                (
                    identity_records_store.issuer,
                    identity_records_store.installation_id,
                    identity_records_store.organization_id,
                )
                != (
                    oauth_store.issuer,
                    oauth_store.installation_id,
                    oauth_store.organization_id,
                )
                or identity_records_store.engine.url != oauth_store.engine.url
                or identity_records_store.engine.get_execution_options().get(
                    "postgresql_readonly"
                )
                is not True
            ):
                raise ValueError("SUPPORT_IDENTITY_STORE_INVALID")
        # Identity preflight reads do not acquire transaction guards. The final
        # object/expiry checks still belong to the guarded operation owner.
        self.records = SQLAlchemyIdentityRecords(identity_records_store or oauth_store)
        self.identity = OAuthIdentityProvider(
            config=resource,
            records=self.records,
            transport_bearer=lambda ref: (
                _bearer.get() if ref == "support-request" else None
            ),
            now=clock,
        )
        self.flow = LocalOAuthFlow(
            config=authorization,
            installation_id=resource.installation_id,
            organization_id=pilot.organization,
            store=oauth_store,
            current_browser=self.browser,
            current_employee=self.employee,
            signer=signer,
            now=clock,
        )
        from samchat.batisenal_case_bridge import CaseBridge

        self.bridge = CaseBridge(
            case_service=case_service,
            case_id=pilot.case_id,
            sessions=sessions,
            identities=self.identity,
            audit=audit,
            current_connection=current_connection,
            issuer=resource.issuer,
            audience=resource.audience,
            installation_id=resource.installation_id,
            enabled=enabled,
            clock=clock,
        )

        async def connection(request):
            try:
                self.identity.resolve_current("support-request")
            except Denied as exc:
                raise owner.BatisenalError("UNAUTHENTICATED", 401) from exc
            return "support-request"

        self.mcp = RequestBearer(
            create_app(
                self.bridge, resolve_connection=connection, events=None, enabled=enabled
            )
        )

    def gate(self):
        if self.enabled is not True:
            raise Denied("ACTIVATION_REQUIRED")

    async def employee(self, employee_id):
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(Empleado.id, Empleado.activo).where(
                        Empleado.id == UUID(employee_id)
                    )
                )
            ).first()
        return CurrentEmployee(employee_id, bool(row and row.activo is True))

    async def browser(self, *, session=None):
        self.gate()
        request = _request.get()
        from contextlib import nullcontext

        # Reauthorization inside enroll must reuse its guarded transaction;
        # opening a second composed connection would wait on our own lock.
        async with (
            self.sessions() if session is None else nullcontext(session)
        ) as session:
            principal = await resolve_session_principal(
                request=request,
                session=session,
                get_current_empleado=self.employee_dependency,
            )
            employee = await self.employee_dependency(request, session)
            scope = await resolve_web_principal(session, employee, "POST")
            from devnous.gastos.services.batisenal_case_authority import (
                current_browser_scope,
            )

            self.case_service.gate()
            await current_browser_scope(
                session,
                employee,
                self.pilot.case_id,
                provenance=dict(
                    issuer=self.case_service.issuer,
                    audience=self.case_service.audience,
                    client_id=self.case_service.client_id,
                    registration_sha256=self.case_service.registration_sha256,
                ),
            )
            await owner.visible_case(session, scope, self.pilot.case_id)
            await self.pilot.fixture(session)
        # Existing signed-session CSRF token also binds consent to this session.
        csrf = request.session.get("batisenal_csrf", "")
        return BrowserSession(principal, csrf, csrf)

    async def browser_for_request(self, request):
        """Use canonical session checks for host-rendered pages."""
        context = _request.set(request)
        try:
            return await self.browser()
        finally:
            _request.reset(context)

    def consent_scope(self):
        return dict(
            case_id=self.pilot.case_id,
            organization=self.pilot.organization,
            project=self.pilot.project,
            duration_seconds=3600,
            mode="private_draft",
            issuer=self.pilot.issuer,
            installation=self.pilot.installation_id,
            source_kind=self.pilot.source_kind,
            registration_sha256=self.case_service.registration_sha256,
        )

    async def browser_call(self, request, operation):
        """Strict existing Origin/CSRF gate; no model-selected identity or case."""
        self.gate()
        context = _request.set(request)
        try:
            payload = await read_payload(request, owner.Policy(max_image_bytes=0))
            if operation == "begin":
                if set(payload) != {"parameters"}:
                    raise Denied("INVALID_AUTHORIZATION_REQUEST")
                preview = await self.flow.begin(payload["parameters"])
                browser = await self.browser()
                scope = self.consent_scope()
                fingerprint = owner.digest([preview.fingerprint, scope])
                async with self.sessions() as session:
                    await session.execute(
                        insert(consents).values(
                            draft_hash=self.flow._secret(preview.draft_id),
                            employee=browser.principal.employee_id,
                            scope_hash=owner.digest(scope),
                            oauth_fingerprint=preview.fingerprint,
                            fingerprint=fingerprint,
                            expires_at=preview.expires_at,
                            used=False,
                        )
                    )
                    await session.commit()
                return dict(asdict(preview), fingerprint=fingerprint, pilot=scope)
            if operation == "confirm":
                if set(payload) != {
                    "draft_id",
                    "fingerprint",
                    "csrf",
                    "state",
                    "approved",
                }:
                    raise Denied("CONSENT_REJECTED")
                browser = await self.browser()
                async with self.sessions() as session:
                    pending = (
                        (
                            await session.execute(
                                select(consents).where(
                                    consents.c.draft_hash
                                    == self.flow._secret(payload["draft_id"])
                                )
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                if (
                    pending is None
                    or pending.used is not False
                    or pending.employee != browser.principal.employee_id
                    or pending.scope_hash != owner.digest(self.consent_scope())
                    or pending.fingerprint != payload["fingerprint"]
                    or self.pilot.now() >= pending.expires_at
                ):
                    raise Denied("CONSENT_REJECTED")
                code = await self.flow.confirm(
                    **dict(payload, fingerprint=pending.oauth_fingerprint)
                )
                browser = await self.browser()
                row = self.records._one(
                    select(LINKS).where(
                        LINKS.c.employee_id == browser.principal.employee_id,
                        LINKS.c.issuer == self.resource.issuer,
                        LINKS.c.installation_id == self.resource.installation_id,
                    )
                )
                link = self.records.read_link(row["link_id"]) if row else None
                async with self.sessions() as session:
                    employee = await self.employee_dependency(request, session)
                    principal = await resolve_web_principal(session, employee, "POST")

                    async def reauthorize():
                        current = await self.browser(session=session)
                        if (
                            current.principal != browser.principal
                            or self.pilot.now() >= pending.expires_at
                            or pending.scope_hash != owner.digest(self.consent_scope())
                            or link is None
                            or self.records.read_link(link.link_id) != link
                        ):
                            raise Denied("CONSENT_REJECTED")

                    await session.execute(
                        update(consents)
                        .where(consents.c.draft_hash == pending.draft_hash)
                        .values(used=True)
                    )
                    await self.pilot.enroll(
                        session, principal, link, reauthorize=reauthorize
                    )
                    await session.commit()
                return code
            if operation == "revoke" and payload == {"confirmed": True}:
                browser = await self.browser()
                async with self.sessions() as session:
                    return await self.pilot.revoke(
                        session, browser.principal.employee_id
                    )
            raise Denied("INVALID_AUTHORIZATION_REQUEST")
        finally:
            _request.reset(context)

    async def exchange(self, parameters):
        self.gate()
        return await self.flow.exchange(parameters)
