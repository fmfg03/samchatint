"""Unwired identity-to-binding adapter; no login, transport or token issuance.

The host supplies the authenticated connection reference and existing identity
verifier. A separately trusted owner supplies ceilings, never tool arguments.
This synchronous adapter matches IdentityProvider's current contract; a blocking
store must not be mounted in the async web runtime without reviewed composition.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID

from samchat.private_plugin.contracts import Identity, IdentityProvider

from .canonical_authority import ServerBinding, validate_server_binding
from .contracts import ReadError
from .expanded import digest

# Exact consent contract reported by inspection of the synthetic SupportPilot.
# This admits no dataset, case, transport or runtime by itself.
_PILOT_CONSENT_SCOPES = frozenset({"batisenal.support"})


@dataclass(frozen=True)
class BindingAdmission:
    """Exact, server-owned audience/namespace and restrictive consent scopes.

    There is no default admission. Supplying this object registers nothing and
    grants no domain authority. A real scope mapping needs separate admission.
    The synthetic pilot's exact scope is accepted separately from read scopes;
    it cannot be combined with them or inferred from a prefix/suffix.
    """

    issuer: str
    audience: str
    installation_id: str
    organization_id: str
    required_scopes: frozenset[str]

    def __post_init__(self):
        if any(
            type(value) is not str or not value.strip() or len(value) > 512
            for value in (
                self.issuer,
                self.audience,
                self.installation_id,
                self.organization_id,
            )
        ) or (
            type(self.required_scopes) is not frozenset
            or not self.required_scopes
            or (
                self.required_scopes != _PILOT_CONSENT_SCOPES
                and any(
                    type(scope) is not str
                    or not re.fullmatch(r"[a-z][a-z0-9_]*:read", scope)
                    for scope in self.required_scopes
                )
            )
        ):
            raise ValueError("INVALID_BINDING_ADMISSION")


class AuthenticatedBindingProvider:
    """Re-resolve connection identity on EVERY canonical authority evaluation.

    No cache or fallback to a service account. Identity scopes only restrict
    the separately supplied ceiling; CanonicalAuthorityResolver still checks
    employee, explicit denials and each domain/object owner afterwards.
    """

    def __init__(
        self,
        *,
        identities: IdentityProvider,
        current_connection: Callable[[], str],
        ceilings_for: Callable[[Identity], ServerBinding],
        admission: BindingAdmission,
    ):
        if not isinstance(admission, BindingAdmission):
            raise ValueError("INVALID_BINDING_ADMISSION")
        self.identities = identities
        self.current_connection = current_connection
        self.ceilings_for = ceilings_for
        self.admission = admission

    def __call__(self) -> ServerBinding:
        try:
            return self._resolve()
        except Exception:
            # No token, connection reference, identity or backend error leaks.
            raise ReadError("AUTH_REQUIRED") from None

    def _resolve(self) -> ServerBinding:
        reference = self.current_connection()
        if type(reference) is not str or not reference.strip() or len(reference) > 512:
            raise ReadError("AUTH_REQUIRED")
        identity = self.identities.resolve_current(reference)
        config = self.admission
        if (
            not isinstance(identity, Identity)
            or identity.active is not True
            or identity.revoked is not False
            or type(identity.expires_at) is not int
            or identity.issuer != config.issuer
            or identity.audience != config.audience
            or identity.installation_id != config.installation_id
            or identity.organization_id != config.organization_id
            or type(identity.oauth_scopes) is not frozenset
            or any(type(scope) is not str for scope in identity.oauth_scopes)
            or not config.required_scopes <= identity.oauth_scopes
            or (
                config.required_scopes == _PILOT_CONSENT_SCOPES
                and identity.oauth_scopes != _PILOT_CONSENT_SCOPES
            )
            or any(
                type(value) is not str or not value.strip() or len(value) > 512
                for value in (identity.actor_id, identity.grant_id, identity.profile_id)
            )
        ):
            raise ReadError("AUTH_REQUIRED")
        employee_id = UUID(identity.actor_id)
        if str(employee_id) != identity.actor_id:
            raise ReadError("AUTH_REQUIRED")
        expiration = datetime.fromtimestamp(identity.expires_at, timezone.utc)
        if expiration <= datetime.now(timezone.utc):
            raise ReadError("AUTH_REQUIRED")
        ceiling = validate_server_binding(self.ceilings_for(identity))
        if (
            ceiling.employee_id != employee_id
            or ceiling.installation != identity.installation_id
        ):
            raise ReadError("AUTH_REQUIRED")
        account = {
            "issuer": identity.issuer,
            "audience": identity.audience,
            "installation": identity.installation_id,
            "organization": identity.organization_id,
            "employee": identity.actor_id,
            "profile": identity.profile_id,
            "owner_binding": ceiling.actor_binding,
        }
        result = replace(
            ceiling,
            actor_binding=digest(account),
            generation=digest(
                {
                    "account": account,
                    "connection": reference,
                    "grant": identity.grant_id,
                    "expiration": identity.expires_at,
                    "consent_scopes": sorted(identity.oauth_scopes),
                    "owner_generation": ceiling.generation,
                    "policy": ceiling.policy_version,
                    "ceilings": sorted(
                        (s.dataset, s.tournament_id, s.version_id, sorted(s.object_ids))
                        for s in ceiling.ceilings
                    ),
                }
            ),
            expires_at=min(expiration, ceiling.expires_at),
        )
        return validate_server_binding(result)
