"""Unwired authorization-code policy over the existing SamChat session.

No endpoints, tokens, grants, persistence or login implementation. The host must
supply canonical get_current_empleado and atomic durable code/consent storage
before this policy can form part of an authorization server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from .contracts import Denied
from .perimeter import digest


@dataclass(frozen=True)
class AuthorizationConfig:
    issuer: str
    resource: str
    client_id: str
    redirect_uri: str
    scopes: frozenset[str]

    def __post_init__(self):
        for value in (self.issuer, self.resource, self.redirect_uri):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.query
                or any(c.isspace() for c in value)
            ):
                raise ValueError("INVALID_AUTHORIZATION_CONFIG")
        if (
            not self.client_id
            or type(self.scopes) is not frozenset
            or not self.scopes
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*:read", s) for s in self.scopes)
        ):
            raise ValueError("INVALID_AUTHORIZATION_CONFIG")

    def resource_metadata(self):
        return {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "scopes_supported": sorted(self.scopes),
            "bearer_methods_supported": ["header"],
        }

    def authorization_metadata(self):
        # Candidate metadata only. These paths are not implemented or mounted.
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer.rstrip("/") + "/authorize",
            "token_endpoint": self.issuer.rstrip("/") + "/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": sorted(self.scopes),
        }


@dataclass(frozen=True)
class SessionPrincipal:
    employee_id: str


async def resolve_session_principal(*, request, session, get_current_empleado):
    """Use the canonical signed-session dependency; never accept model identity.

    Request must be a trusted framework request after SessionMiddleware. No
    cookie decoding, password verification or role decision is duplicated here.
    """
    try:
        if request.session.get("impersonator_empleado_id"):
            raise Denied("UNAUTHENTICATED")
        employee = await get_current_empleado(request, session)
        employee_id = str(UUID(str(employee.id)))
        if (
            employee.activo is not True
            or getattr(employee, "impersonator_empleado_id", None)
            or request.session.get("impersonator_empleado_id")
            or str(request.session.get("empleado_id")) != employee_id
        ):
            raise Denied("UNAUTHENTICATED")
        return SessionPrincipal(employee_id)
    except Exception:
        raise Denied("UNAUTHENTICATED") from None


@dataclass(frozen=True)
class AuthorizationRequest:
    client_id: str
    redirect_uri: str
    resource: str
    scopes: frozenset[str]
    state: str
    challenge: str


def validate_authorization_request(config, parameters):
    """Reject unknown/duplicate parameters before any callback redirect.

    Accept a list of pairs to retain duplicate query parameters. Requests cannot
    carry an actor, organization, employee ID, login hint or permission override.
    """
    expected = {
        "response_type",
        "client_id",
        "redirect_uri",
        "resource",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
    }
    try:
        if type(parameters) not in (list, tuple) or any(
            type(pair) not in (list, tuple)
            or len(pair) != 2
            or any(type(part) is not str for part in pair)
            for pair in parameters
        ):
            raise ValueError()
        values = dict(parameters)
        if len(parameters) != len(values) or set(values) != expected:
            raise ValueError()
        parts = values["scope"].split(" ")
        scopes = frozenset(parts)
        if (
            values["response_type"] != "code"
            or values["client_id"] != config.client_id
            or values["redirect_uri"] != config.redirect_uri
            or values["resource"] != config.resource
            or values["code_challenge_method"] != "S256"
            or len(scopes) != len(parts)
            or not scopes
            or not scopes <= config.scopes
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", values["code_challenge"])
            or not re.fullmatch(r"[\x21-\x7e]{16,512}", values["state"])
        ):
            raise ValueError()
        return AuthorizationRequest(
            config.client_id,
            config.redirect_uri,
            config.resource,
            scopes,
            values["state"],
            values["code_challenge"],
        )
    except Exception:
        raise Denied("INVALID_AUTHORIZATION_REQUEST") from None


def pkce_matches(verifier, challenge):
    if type(challenge) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
        return False
    if not isinstance(verifier, str) or not re.fullmatch(
        r"[A-Za-z0-9._~-]{43,128}", verifier
    ):
        return False
    computed = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return hmac.compare_digest(computed, challenge)


@dataclass(frozen=True)
class ConsentDraft:
    """Server-held draft, never reconstructed from client-submitted fields."""

    employee_id: str
    request: AuthorizationRequest
    browser_binding_hash: str
    csrf_hash: str
    expires_at: int

    @property
    def fingerprint(self):
        return digest(
            {
                "employee": self.employee_id,
                "request": {
                    **vars(self.request),
                    "scopes": sorted(self.request.scopes),
                },
                "browser": self.browser_binding_hash,
                "csrf": self.csrf_hash,
                "expires_at": self.expires_at,
            }
        )


def confirm_consent(
    *,
    draft,
    principal,
    browser_binding,
    csrf,
    fingerprint,
    approved,
    now,
):
    """Validate explicit consent only; does not issue a code or persistent grant.

    Host must atomically consume its draft and persist audit before issuing any
    authorization code. Browser binding and CSRF must be server-generated high
    entropy values; OAuth state is not a substitute for either.
    """
    try:
        if (
            approved is not True
            or type(now) is not int
            or type(draft.expires_at) is not int
            or now >= draft.expires_at
            or principal.employee_id != draft.employee_id
            or not hmac.compare_digest(draft.fingerprint, fingerprint)
            or not hmac.compare_digest(
                digest(browser_binding), draft.browser_binding_hash
            )
            or not hmac.compare_digest(digest(csrf), draft.csrf_hash)
        ):
            raise ValueError()
        return draft.request
    except Exception:
        raise Denied("CONSENT_REJECTED") from None


def validate_code_exchange(
    *, config, consented_request, client_id, redirect_uri, resource, verifier
):
    """Validate binding before host's atomic one-use code consumption.

    Host still must check code expiry, revocation, current linked employee,
    durable single use, and audit. This function alone cannot authorize a token.
    """
    try:
        request = consented_request
        if (
            client_id != config.client_id
            or client_id != request.client_id
            or redirect_uri != config.redirect_uri
            or redirect_uri != request.redirect_uri
            or resource != config.resource
            or resource != request.resource
            or type(request.scopes) is not frozenset
            or not request.scopes
            or not request.scopes <= config.scopes
            or not pkce_matches(verifier, request.challenge)
        ):
            raise ValueError()
    except Exception:
        raise Denied("INVALID_GRANT") from None
