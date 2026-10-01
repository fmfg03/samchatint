"""Unwired JWT resource verification for the existing SamChat installation.

This is not an authorization server, token issuer, account linker or provisioner.
Only pre-existing, currently active grant/link/employee records may resolve an
identity. Product permissions and reason-social/object scope remain canonical
checks after authentication. No production issuer or keys are configured here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from .contracts import Denied, Identity

_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})


@dataclass(frozen=True)
class VerificationKey:
    """A public key bound to its algorithm by trusted local configuration."""

    algorithm: str
    public_key: Any = field(repr=False)


@dataclass(frozen=True)
class OAuthResourceConfig:
    """One installation; key IDs index only this local trusted key map.

    Algorithm choice must not be taken from untrusted JWT headers. The header
    may select an existing key ID, then must match that key's fixed algorithm.
    Key refresh/rotation and IdP integration remain separate reviewed work.
    """

    issuer: str
    audience: str
    installation_id: str
    allowed_scopes: frozenset[str]
    allowed_clients: frozenset[str]
    keys: Mapping[str, VerificationKey] = field(repr=False)

    def __post_init__(self):
        if (
            not all(
                _identifier(v)
                for v in (self.issuer, self.audience, self.installation_id)
            )
            or not _scope_set(self.allowed_scopes)
            or not _scope_set(self.allowed_clients)
            or not self.keys
            or any(
                not _identifier(kid)
                or not isinstance(key, VerificationKey)
                or key.algorithm not in _ASYMMETRIC_ALGORITHMS
                or not _public_key_matches(key)
                for kid, key in self.keys.items()
            )
        ):
            raise ValueError("INVALID_OAUTH_RESOURCE_CONFIGURATION")
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))


@dataclass(frozen=True)
class ExistingGrant:
    grant_id: str
    issuer: str
    subject: str
    token_id: str
    client_id: str
    installation_id: str
    link_id: str
    scopes: frozenset[str]
    expires_at: int
    active: bool
    revoked: bool


@dataclass(frozen=True)
class ExistingLink:
    link_id: str
    issuer: str
    subject: str
    installation_id: str
    employee_id: str
    organization_id: str
    profile_id: str
    active: bool


@dataclass(frozen=True)
class CurrentEmployee:
    employee_id: str
    active: bool


class ExistingIdentityRecords(Protocol):
    """Read existing authoritative rows on EVERY resolution, with no cache.

    Missing/ambiguous records return None. Implementations must never create,
    link, reactivate or promote users. Link organization is the server's known
    installation namespace, not token-derived reason-social/object authority.
    profile_id is an existing stable opaque public account identifier, never an
    email, employee identifier, mutable name or newly generated per-token ID.
    """

    def read_grant(
        self, issuer: str, subject: str, token_id: str
    ) -> ExistingGrant | None: ...

    def read_link(self, link_id: str) -> ExistingLink | None: ...

    def read_employee(self, employee_id: str) -> CurrentEmployee | None: ...


def _identifier(value: Any) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 512
        and not any(char.isspace() or ord(char) < 32 for char in value)
    )


def _public_key_matches(key: VerificationKey) -> bool:
    if key.algorithm == "RS256":
        return (
            isinstance(key.public_key, rsa.RSAPublicKey)
            and key.public_key.key_size >= 2048
        )
    return (
        key.algorithm == "ES256"
        and isinstance(key.public_key, ec.EllipticCurvePublicKey)
        and isinstance(key.public_key.curve, ec.SECP256R1)
    )


def _scope_set(value: Any) -> bool:
    return (
        type(value) is frozenset
        and 0 < len(value) <= 100
        and all(
            _identifier(scope)
            and all(
                33 <= ord(char) <= 126 and char not in {'"', "\\"} for char in scope
            )
            for scope in value
        )
    )


class OAuthIdentityProvider:
    """Verify a transport bearer and reload the existing local account link.

    transport_bearer resolves an opaque reference from authenticated transport
    context, never tool/model arguments. It returns the bearer value only (no
    Authorization prefix). No bearer, payload or raw dependency error is logged
    or persisted. The model must never get access to this provider as a tool.
    """

    def __init__(
        self,
        *,
        config: OAuthResourceConfig,
        transport_bearer: Callable[[str], str | None],
        records: ExistingIdentityRecords,
        now: Callable[[], int],
    ):
        self._config = config
        self._bearer = transport_bearer
        self._records = records
        self._now = now

    def resolve_current(self, connection_ref: str) -> Identity:
        """Return current restricted identity or a single sanitized denial."""
        try:
            return self._resolve(connection_ref)
        except Exception:
            raise Denied("UNAUTHENTICATED") from None

    def _resolve(self, connection_ref: str) -> Identity:
        if not _identifier(connection_ref):
            raise Denied("UNAUTHENTICATED")
        token = self._bearer(connection_ref)
        if type(token) is not str or not 0 < len(token) <= 16_384:
            raise Denied("UNAUTHENTICATED")
        config = self._config
        header = jwt.get_unverified_header(token)
        if (
            set(header) != {"alg", "kid", "typ"}
            or header["typ"] != "at+jwt"
            or not _identifier(header["kid"])
        ):
            raise Denied("UNAUTHENTICATED")
        key = config.keys.get(header["kid"])
        if key is None or header["alg"] != key.algorithm:
            raise Denied("UNAUTHENTICATED")
        claims = jwt.decode(
            token,
            key.public_key,
            algorithms=[key.algorithm],
            issuer=config.issuer,
            audience=config.audience,
            options={
                "require": [
                    "iss",
                    "aud",
                    "sub",
                    "exp",
                    "nbf",
                    "iat",
                    "jti",
                    "scope",
                    "client_id",
                ],
                "strict_aud": True,
                # Strict NumericDate types and the injected server clock below
                # replace library coercion/wall clock; signature stays verified.
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
        now = self._now()
        if (
            type(now) is not int
            or any(type(claims[k]) is not int for k in ("exp", "nbf", "iat"))
            or not 0 <= claims["iat"] <= claims["nbf"] <= now < claims["exp"]
            or not _identifier(claims["sub"])
            or not _identifier(claims["jti"])
            or claims["client_id"] not in config.allowed_clients
            or claims.get("azp", claims["client_id"]) != claims["client_id"]
            or claims.get("installation_id", config.installation_id)
            != config.installation_id
            or type(claims["scope"]) is not str
        ):
            raise Denied("UNAUTHENTICATED")
        scope_parts = claims["scope"].split(" ")
        scopes = frozenset(scope_parts)
        if (
            not _scope_set(scopes)
            or len(scopes) != len(scope_parts)
            or not scopes.issubset(config.allowed_scopes)
        ):
            raise Denied("UNAUTHENTICATED")
        grant = self._records.read_grant(config.issuer, claims["sub"], claims["jti"])
        if (
            not isinstance(grant, ExistingGrant)
            or grant.active is not True
            or grant.revoked is not False
            or type(grant.expires_at) is not int
            or grant.expires_at <= now
            or not _identifier(grant.grant_id)
            or not _identifier(grant.link_id)
            or grant.issuer != config.issuer
            or grant.subject != claims["sub"]
            or grant.token_id != claims["jti"]
            or grant.client_id != claims["client_id"]
            or grant.installation_id != config.installation_id
            or not _scope_set(grant.scopes)
        ):
            raise Denied("UNAUTHENTICATED")
        effective_scopes = scopes & grant.scopes & config.allowed_scopes
        if not effective_scopes:
            raise Denied("UNAUTHENTICATED")
        link = self._records.read_link(grant.link_id)
        if (
            not isinstance(link, ExistingLink)
            or link.active is not True
            or link.link_id != grant.link_id
            or link.issuer != config.issuer
            or link.subject != claims["sub"]
            or link.installation_id != config.installation_id
            or not all(
                _identifier(v)
                for v in (link.employee_id, link.organization_id, link.profile_id)
            )
            or "@" in link.profile_id
            or link.profile_id in {link.employee_id, link.subject}
        ):
            raise Denied("UNAUTHENTICATED")
        employee = self._records.read_employee(link.employee_id)
        if (
            not isinstance(employee, CurrentEmployee)
            or employee.active is not True
            or employee.employee_id != link.employee_id
        ):
            raise Denied("UNAUTHENTICATED")
        return Identity(
            actor_id=employee.employee_id,
            installation_id=config.installation_id,
            organization_id=link.organization_id,
            grant_id=grant.grant_id,
            expires_at=min(claims["exp"], grant.expires_at),
            issuer=config.issuer,
            audience=config.audience,
            oauth_scopes=effective_scopes,
            active=True,
            revoked=False,
            profile_id=link.profile_id,
        )
