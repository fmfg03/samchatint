"""Trusted server contracts, separate from model-supplied tool arguments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Denied(ValueError):
    """Stable, non-sensitive error code suitable for a tool result."""


@dataclass(frozen=True)
class Identity:
    """Returned by server verification, never deserialized from tool arguments."""

    actor_id: str
    installation_id: str
    organization_id: str
    grant_id: str
    expires_at: int
    issuer: str
    audience: str
    oauth_scopes: frozenset[str]
    active: bool
    revoked: bool


@dataclass(frozen=True)
class Target:
    """Requested identifiers are selectors; the policy owner must resolve them."""

    object_id: str
    organization_id: str
    legal_entity_id: str
    portfolio_id: str
    tournament_id: str
    version: str


@dataclass(frozen=True)
class Attempt:
    action: str
    target: Target
    payload_digest: str
    draft_id: str = ""
    amount_minor: int | None = None
    currency: str = ""
    destination_id: str = ""
    idempotency_key: str = ""
    confirmation_ref: str = ""


@dataclass(frozen=True)
class Approval:
    """Trusted human approval record; a model-supplied boolean is not approval."""

    actor_id: str
    grant_id: str
    binding_digest: str
    expires_at: int
    consumed: bool = False


class IdentityProvider(Protocol):
    def resolve_current(self, connection_ref: str) -> Identity:
        """Verify signature/introspection, issuer/audience, expiry and revocation.

        Resolve an active local employee from the per-user connection on EVERY
        attempt. Never accept actor, scopes or roles from tool arguments. The
        reference comes from authenticated transport context, never the model.
        Do not store/log the bearer credential. No implementation is wired yet.
        """


class PolicyOwner(Protocol):
    def authorize_current(self, identity: Identity, attempt: Attempt) -> bool:
        """Reload faculties and resolve object, organization/legal entity,
        portfolio/tournament scope and specific denials from canonical services.
        Missing/ambiguous mapping must deny. Token scopes only restrict access.
        Commit owner must repeat this check under its transaction/row lock.
        """


class ApprovalStore(Protocol):
    def read_for_actor(self, reference: str, identity: Identity) -> Approval | None:
        """Load human approval via authenticated out-of-band interaction.

        Must not create approval from a model call or trust an 'approved' field.
        Consumption, idempotency and domain commit must be atomic or reconciled
        with a durable outbox before any mutation can be enabled.
        """


class AuditSink(Protocol):
    def append(self, *, actor_id: str | None, action: str, outcome: str) -> str:
        """Persist a minimal receipt, return its durable ID, or raise.

        Must never retain tool payload, token, filename, child PII or raw error.
        Production adapter, retention and transaction boundary remain unproven.
        """
