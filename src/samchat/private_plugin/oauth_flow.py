"""Local-only authorization-code flow; no routes, login or production wiring.

Canonical browser-session and current-employee resolvers are mandatory trusted
dependencies. All records and signing keys used by the tests are synthetic.
No passwords, user provisioning, role changes, refresh tokens or network calls.
"""

from __future__ import annotations

import hmac
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from .authorization import (
    AuthorizationConfig,
    ConsentDraft,
    SessionPrincipal,
    confirm_consent,
    validate_authorization_request,
    validate_code_exchange,
)
from .contracts import Denied
from .oauth_identity import CurrentEmployee, ExistingGrant
from .oauth_store import CodeRecord, OAuthStore, OAuthStoreError, PendingRecord
from .perimeter import digest


@dataclass(frozen=True)
class BrowserSession:
    """Host-generated browser secrets with a freshly resolved canonical user."""

    principal: SessionPrincipal
    browser_binding: str = field(repr=False)
    csrf: str = field(repr=False)


@dataclass(frozen=True)
class ConsentPreview:
    draft_id: str
    fingerprint: str
    client_id: str
    resource: str
    scopes: tuple[str, ...]
    expires_at: int
    csrf: str = field(repr=False)
    state: str = field(repr=False)
    receipt_id: str


@dataclass(frozen=True)
class CodeRedirect:
    redirect_uri: str
    code: str = field(repr=False)
    state: str = field(repr=False)
    receipt_id: str


@dataclass(frozen=True)
class TokenResult:
    access_token: str = field(repr=False)
    token_type: str
    expires_in: int
    scope: str
    grant_id: str
    receipt_id: str


@dataclass(frozen=True)
class RevocationResult:
    revoked: bool
    receipt_id: str


@dataclass(frozen=True)
class JWTSigner:
    """Injected in-memory synthetic key; this module never creates or loads keys."""

    key_id: str
    private_key: object = field(repr=False)

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.key_id):
            raise ValueError("INVALID_SIGNER")
        if not (
            isinstance(self.private_key, rsa.RSAPrivateKey)
            and self.private_key.key_size >= 2048
            or isinstance(self.private_key, ec.EllipticCurvePrivateKey)
            and isinstance(self.private_key.curve, ec.SECP256R1)
        ):
            raise ValueError("INVALID_SIGNER")

    @property
    def algorithm(self) -> str:
        return "RS256" if isinstance(self.private_key, rsa.RSAPrivateKey) else "ES256"

    def sign(self, claims: dict) -> str:
        return jwt.encode(
            claims,
            self.private_key,
            algorithm=self.algorithm,
            headers={"kid": self.key_id, "typ": "at+jwt"},
        )


class LocalOAuthFlow:
    """Reviewed in-process flow; constructing it exposes no HTTP endpoint.

    current_browser must use the existing canonical signed SamChat session,
    deny impersonation and return host-generated browser binding/CSRF secrets.
    current_employee reloads by the employee stored in the code, never a client
    parameter. organization_id is one trusted installation namespace, not RFC
    permission. Durable store transactions own code consumption and audit.
    """

    def __init__(
        self,
        *,
        config: AuthorizationConfig,
        installation_id: str,
        organization_id: str,
        store: OAuthStore,
        current_browser: Callable[[], Awaitable[BrowserSession]],
        current_employee: Callable[[str], Awaitable[CurrentEmployee]],
        signer: JWTSigner,
        now: Callable[[], int],
        consent_lifetime: int = 300,
        code_lifetime: int = 120,
        token_lifetime: int = 300,
    ):
        if any(
            type(v) is not int or not 1 <= v <= 600
            for v in (
                consent_lifetime,
                code_lifetime,
                token_lifetime,
            )
        ) or any(
            type(v) is not str or not v or len(v) > 512
            for v in (installation_id, organization_id)
        ):
            raise ValueError("INVALID_FLOW_CONFIG")
        self.config, self.installation_id, self.organization_id = (
            config,
            installation_id,
            organization_id,
        )
        self.store, self._browser_resolver, self._employee_resolver = (
            store,
            current_browser,
            current_employee,
        )
        self.signer, self._now = signer, now
        self.consent_lifetime, self.code_lifetime, self.token_lifetime = (
            consent_lifetime,
            code_lifetime,
            token_lifetime,
        )

    def _time(self) -> int:
        now = self._now()
        if type(now) is not int or now < 0:
            raise Denied("OAUTH_UNAVAILABLE")
        return now

    async def _employee(self, employee_id: str) -> CurrentEmployee:
        if str(UUID(employee_id)) != employee_id:
            raise Denied("UNAUTHENTICATED")
        employee = await self._employee_resolver(employee_id)
        if (
            not isinstance(employee, CurrentEmployee)
            or employee.active is not True
            or employee.employee_id != employee_id
        ):
            raise Denied("UNAUTHENTICATED")
        return employee

    async def _browser(self) -> BrowserSession:
        browser = await self._browser_resolver()
        if not isinstance(browser, BrowserSession) or not isinstance(
            browser.principal, SessionPrincipal
        ):
            raise Denied("UNAUTHENTICATED")
        for value in (browser.browser_binding, browser.csrf):
            if type(value) is not str or not re.fullmatch(
                r"[A-Za-z0-9_-]{43,128}", value
            ):
                raise Denied("UNAUTHENTICATED")
        await self._employee(browser.principal.employee_id)
        return browser

    def _scope_digest(self, employee_id, request) -> str:
        return digest(
            {
                "employee": employee_id,
                "installation": self.installation_id,
                "organization": self.organization_id,
                "client": request.client_id,
                "resource": request.resource,
                "scopes": sorted(request.scopes),
            }
        )

    @staticmethod
    def _secret(value: str) -> str:
        if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise Denied("INVALID_GRANT")
        return digest(value)

    @staticmethod
    def _failure(exc: Exception) -> Denied:
        allowed = {
            "UNAUTHENTICATED",
            "INVALID_AUTHORIZATION_REQUEST",
            "CONSENT_REJECTED",
            "INVALID_GRANT",
            "FORBIDDEN",
        }
        return Denied(
            str(exc)
            if isinstance(exc, Denied) and str(exc) in allowed
            else "OAUTH_UNAVAILABLE"
        )

    async def begin(self, parameters: list[tuple[str, str]]) -> ConsentPreview:
        try:
            request = validate_authorization_request(self.config, parameters)
            browser, now = await self._browser(), self._time()
            draft_id = secrets.token_urlsafe(32)
            # OAuth state is returned only to its originating browser/callback.
            # Persistence holds its digest, not the correlation secret itself.
            draft = ConsentDraft(
                browser.principal.employee_id,
                replace(request, state=digest(request.state)),
                digest(browser.browser_binding),
                digest(browser.csrf),
                now + self.consent_lifetime,
            )
            with self.store.transaction(now=now, clock=self._time) as tx:
                tx.put_pending(PendingRecord(self._secret(draft_id), draft))
                receipt = tx.append_audit(
                    actor_id=draft.employee_id,
                    action="oauth.begin",
                    outcome="CONSENT_PREPARED",
                    evidence_digest=draft.fingerprint,
                    scope_digest=self._scope_digest(draft.employee_id, request),
                )
            return ConsentPreview(
                draft_id,
                draft.fingerprint,
                request.client_id,
                request.resource,
                tuple(sorted(request.scopes)),
                draft.expires_at,
                browser.csrf,
                request.state,
                receipt,
            )
        except Exception as exc:
            raise self._failure(exc) from None

    async def confirm(
        self, *, draft_id: str, fingerprint: str, csrf: str, state: str, approved: bool
    ) -> CodeRedirect:
        try:
            browser, now = await self._browser(), self._time()
            pending_hash = self._secret(draft_id)
            with self.store.transaction(now=now, clock=self._time) as tx:
                pending = tx.read_pending(pending_hash)
                if pending is None or pending.used:
                    raise Denied("CONSENT_REJECTED")
                if type(state) is not str or not hmac.compare_digest(
                    digest(state), pending.draft.request.state
                ):
                    raise Denied("CONSENT_REJECTED")
                request = confirm_consent(
                    draft=pending.draft,
                    principal=browser.principal,
                    browser_binding=browser.browser_binding,
                    csrf=csrf,
                    fingerprint=fingerprint,
                    approved=approved,
                    now=now,
                )
                # Also reject a CSRF token rotated since consent preparation.
                if type(csrf) is not str or not hmac.compare_digest(csrf, browser.csrf):
                    raise Denied("CONSENT_REJECTED")
                link = tx.get_or_create_link(browser.principal.employee_id)
                if (
                    link.active is not True
                    or link.employee_id != browser.principal.employee_id
                    or link.installation_id != self.installation_id
                    or link.organization_id != self.organization_id
                    or link.issuer != self.config.issuer
                ):
                    raise Denied("FORBIDDEN")
                tx.consume_pending(pending_hash)
                code = secrets.token_urlsafe(32)
                tx.put_code(
                    CodeRecord(
                        self._secret(code),
                        link.employee_id,
                        request,
                        link,
                        now + self.code_lifetime,
                    )
                )
                receipt = tx.append_audit(
                    actor_id=link.employee_id,
                    action="oauth.confirm",
                    outcome="CODE_ISSUED",
                    evidence_digest=digest(
                        {
                            "draft": pending.draft.fingerprint,
                            "code_hash": self._secret(code),
                        }
                    ),
                    scope_digest=self._scope_digest(link.employee_id, request),
                )
            return CodeRedirect(request.redirect_uri, code, state, receipt)
        except Exception as exc:
            if isinstance(exc, OAuthStoreError) and str(exc) in {
                "STORE_EXPIRED",
                "STORE_ALREADY_USED_OR_EXPIRED",
            }:
                raise Denied("CONSENT_REJECTED") from None
            raise self._failure(exc) from None

    async def exchange(self, parameters: list[tuple[str, str]]) -> TokenResult:
        try:
            expected = {
                "grant_type",
                "code",
                "client_id",
                "redirect_uri",
                "resource",
                "code_verifier",
            }
            if type(parameters) not in (list, tuple) or any(
                type(pair) not in (list, tuple)
                or len(pair) != 2
                or any(type(p) is not str for p in pair)
                for pair in parameters
            ):
                raise Denied("INVALID_GRANT")
            values = dict(parameters)
            if (
                len(values) != len(parameters)
                or set(values) != expected
                or values["grant_type"] != "authorization_code"
            ):
                raise Denied("INVALID_GRANT")
            now, code_hash = self._time(), self._secret(values["code"])
            with self.store.transaction(now=now, clock=self._time) as tx:
                code = tx.read_code(code_hash)
                if (
                    code is None
                    or code.used
                    or type(code.expires_at) is not int
                    or now >= code.expires_at
                ):
                    raise Denied("INVALID_GRANT")
                validate_code_exchange(
                    config=self.config,
                    consented_request=code.request,
                    client_id=values["client_id"],
                    redirect_uri=values["redirect_uri"],
                    resource=values["resource"],
                    verifier=values["code_verifier"],
                )
                employee = await self._employee(code.employee_id)
                now = self._time()
                if now >= code.expires_at:
                    raise Denied("INVALID_GRANT")
                # Global write lock order: existing employee before link/grant.
                tx.current_employee(employee.employee_id)
                link = tx.read_link(code.link.link_id)
                if (
                    link != code.link
                    or link.active is not True
                    or link.employee_id != employee.employee_id
                    or link.organization_id != self.organization_id
                    or link.installation_id != self.installation_id
                    or link.issuer != self.config.issuer
                ):
                    raise Denied("INVALID_GRANT")
                grant_id, token_id = secrets.token_urlsafe(32), secrets.token_urlsafe(
                    32
                )
                expires_at = now + self.token_lifetime
                token = self.signer.sign(
                    {
                        "iss": self.config.issuer,
                        "aud": self.config.resource,
                        "sub": link.subject,
                        "jti": token_id,
                        "client_id": self.config.client_id,
                        "installation_id": self.installation_id,
                        "iat": now,
                        "nbf": now,
                        "exp": expires_at,
                        "scope": " ".join(sorted(code.request.scopes)),
                    }
                )
                if (
                    type(token) is not str
                    or not token
                    or self._time() >= code.expires_at
                ):
                    raise Denied("INVALID_GRANT")
                tx.consume_code(code_hash)
                tx.put_grant(
                    ExistingGrant(
                        grant_id,
                        self.config.issuer,
                        link.subject,
                        token_id,
                        self.config.client_id,
                        self.installation_id,
                        link.link_id,
                        code.request.scopes,
                        expires_at,
                        True,
                        False,
                        link.employee_id,
                        link.organization_id,
                        link.profile_id,
                    )
                )
                receipt = tx.append_audit(
                    actor_id=employee.employee_id,
                    action="oauth.exchange",
                    outcome="TOKEN_ISSUED",
                    evidence_digest=digest(
                        {"code_hash": code_hash, "grant": grant_id, "jti": token_id}
                    ),
                    scope_digest=self._scope_digest(employee.employee_id, code.request),
                )
            return TokenResult(
                token,
                "Bearer",
                self.token_lifetime,
                " ".join(sorted(code.request.scopes)),
                grant_id,
                receipt,
            )
        except Exception as exc:
            if isinstance(exc, OAuthStoreError) and str(exc) in {
                "STORE_EXPIRED",
                "STORE_ALREADY_USED_OR_EXPIRED",
            }:
                raise Denied("INVALID_GRANT") from None
            raise self._failure(exc) from None

    async def revoke(self, *, grant_id: str, csrf: str) -> RevocationResult:
        try:
            browser, now = await self._browser(), self._time()
            if type(csrf) is not str or not hmac.compare_digest(csrf, browser.csrf):
                raise Denied("FORBIDDEN")
            self._secret(grant_id)
            with self.store.transaction(now=now, clock=self._time) as tx:
                tx.current_employee(browser.principal.employee_id)
                grant = tx.read_grant(grant_id)
                if (
                    grant is None
                    or grant.employee_id != browser.principal.employee_id
                    or grant.installation_id != self.installation_id
                    or grant.organization_id != self.organization_id
                    or grant.issuer != self.config.issuer
                ):
                    raise Denied("FORBIDDEN")
                tx.revoke_grant(grant_id, employee_id=browser.principal.employee_id)
                receipt = tx.append_audit(
                    actor_id=browser.principal.employee_id,
                    action="oauth.revoke",
                    outcome="GRANT_REVOKED",
                    evidence_digest=digest({"grant": grant_id}),
                    scope_digest=digest(
                        {
                            "employee": grant.employee_id,
                            "installation": self.installation_id,
                            "organization": self.organization_id,
                        }
                    ),
                )
            return RevocationResult(True, receipt)
        except Exception as exc:
            raise self._failure(exc) from None
