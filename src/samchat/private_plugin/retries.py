"""Canonical mutation-owner replay contract; not an execution mechanism."""

from dataclasses import dataclass

from .contracts import Attempt, Denied, Identity
from .perimeter import confirmation_binding, digest


def reservation_key(identity: Identity, attempt: Attempt) -> str:
    """An action version upgrade must use a new namespace after review."""
    if not attempt.idempotency_key.strip():
        raise Denied("IDEMPOTENCY_REQUIRED")
    return digest(
        [
            identity.installation_id,
            identity.organization_id,
            identity.actor_id,
            attempt.action,
            "private-plugin-v1",
            attempt.idempotency_key,
        ]
    )


@dataclass(frozen=True)
class MutationRecord:
    """Loaded from canonical owner after reauthentication and reauthorization.

    Owner reserves key with UNIQUE constraint before dispatch, commits result
    and minimal audit atomically with domain state (or durable outbox), and
    reloads postcondition on retries. A crash/unknown effect is reconciled,
    never automatically dispatched again. This protocol is NOT wired to DB.
    """

    reservation: str
    binding: str
    state: str  # pending, verified, failed/unknown
    receipt_id: str


def verified_replay(
    record: MutationRecord, identity: Identity, attempt: Attempt
) -> str:
    """Return only an exact, verified prior receipt; never authorize an effect."""
    if record.reservation != reservation_key(
        identity, attempt
    ) or record.binding != confirmation_binding(identity, attempt):
        raise Denied("IDEMPOTENCY_CONFLICT")
    if record.state != "verified" or not record.receipt_id:
        raise Denied("RECONCILIATION_REQUIRED")
    return record.receipt_id
