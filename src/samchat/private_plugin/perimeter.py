"""Fail-closed, transport-neutral gate. Intentionally contains no dispatcher."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .catalog import operations
from .contracts import (
    ApprovalStore,
    Attempt,
    AuditSink,
    Denied,
    Identity,
    IdentityProvider,
    PolicyOwner,
)


def digest(value: Any) -> str:
    """Exact canonical binding; floats/NaN and arbitrary objects are rejected."""

    def check(item):
        if item is None or type(item) in {str, bool, int}:
            return
        if isinstance(item, dict) and all(type(k) is str for k in item):
            for v in item.values():
                check(v)
            return
        if isinstance(item, list):
            for v in item:
                check(v)
            return
        raise Denied("INVALID_BINDING")

    check(value)
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def confirmation_binding(identity: Identity, attempt: Attempt) -> str:
    """Bind every business-changing field plus identity and connection grant."""
    data = asdict(attempt)
    data.pop("confirmation_ref")
    data.pop("idempotency_key")  # retry identity is independent of business diff
    return digest(
        {
            "actor": identity.actor_id,
            "grant": identity.grant_id,
            "installation": identity.installation_id,
            "attempt": data,
        }
    )


def validate_attempt(attempt: Attempt) -> None:
    if (
        not attempt.target.object_id
        or not attempt.target.version
        or not attempt.target.organization_id
    ):
        raise Denied("TARGET_REQUIRED")
    if len(attempt.payload_digest) != 64 or any(
        c not in "0123456789abcdef" for c in attempt.payload_digest
    ):
        raise Denied("INVALID_BINDING")
    if attempt.amount_minor is not None and type(attempt.amount_minor) is not int:
        raise Denied("INVALID_AMOUNT")


@dataclass(frozen=True)
class Decision:
    code: str
    receipt_id: str
    invoked: bool = False


class Perimeter:
    """Dependencies supplied by trusted composition root, never tool input.

    All candidates terminate in DISABLED even when policy and confirmation pass.
    Enabling requires code review and canonical owner integration, not a flag.
    """

    def __init__(
        self,
        identities: IdentityProvider,
        policy: PolicyOwner,
        approvals: ApprovalStore,
        audit: AuditSink,
        *,
        issuer: str,
        audience: str,
        installation_id: str,
    ):
        self.identities, self.policy = identities, policy
        self.approvals, self.audit = approvals, audit
        self.issuer, self.audience = issuer, audience
        self.installation_id = installation_id

    def evaluate(self, connection_ref: str, attempt: Attempt, *, now: int) -> Decision:
        actor = None
        action = "unregistered"
        try:
            op = next((o for o in operations() if o.action == attempt.action), None)
            if op is None:
                raise Denied("ACTION_NOT_REGISTERED")
            action = op.action
            identity = self.identities.resolve_current(connection_ref)
            if (
                identity.active is not True
                or identity.revoked is not False
                or type(identity.expires_at) is not int
                or identity.expires_at <= now
                or not identity.actor_id
                or not identity.grant_id
                or identity.issuer != self.issuer
                or identity.audience != self.audience
                or identity.installation_id != self.installation_id
            ):
                raise Denied("UNAUTHENTICATED")
            actor = identity.actor_id
            validate_attempt(attempt)
            if (
                identity.organization_id != attempt.target.organization_id
                or op.scope not in identity.oauth_scopes
                or self.policy.authorize_current(identity, attempt) is not True
            ):
                raise Denied("FORBIDDEN")
            if op.write:
                if not attempt.idempotency_key.strip():
                    raise Denied("IDEMPOTENCY_REQUIRED")
                if not attempt.draft_id or not attempt.confirmation_ref:
                    raise Denied("CONFIRMATION_REQUIRED")
                approval = self.approvals.read_for_actor(
                    attempt.confirmation_ref, identity
                )
                if (
                    approval is None
                    or approval.consumed
                    or approval.actor_id != identity.actor_id
                    or approval.grant_id != identity.grant_id
                    or approval.expires_at <= now
                    or approval.binding_digest
                    != confirmation_binding(identity, attempt)
                ):
                    raise Denied("CONFIRMATION_MISMATCH")
            # No domain action, business write, approval consumption or replay.
            code = op.disabled_reason
        except Denied as exc:
            # Only codes from this finite vocabulary may reach logs/receipts.
            allowed = {
                "ACTION_NOT_REGISTERED",
                "UNAUTHENTICATED",
                "TARGET_REQUIRED",
                "INVALID_BINDING",
                "INVALID_AMOUNT",
                "FORBIDDEN",
                "IDEMPOTENCY_REQUIRED",
                "CONFIRMATION_REQUIRED",
                "CONFIRMATION_MISMATCH",
            }
            code = str(exc) if str(exc) in allowed else "DEPENDENCY_UNAVAILABLE"
        except Exception:
            code = "DEPENDENCY_UNAVAILABLE"
        try:
            receipt = self.audit.append(actor_id=actor, action=action, outcome=code)
            if not receipt:
                raise ValueError("missing receipt")
        except Exception:
            return Decision("AUDIT_UNAVAILABLE", "")
        return Decision(code, receipt)
