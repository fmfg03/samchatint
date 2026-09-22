"""Typed, transport-neutral contracts for the disabled v0.1 action catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Tuple
from uuid import uuid4


ACTION_TYPES = frozenset(
    {"query", "diagnostic", "preview", "draft", "submit_for_approval"}
)
MUTATING_ACTION_TYPES = frozenset({"draft", "submit_for_approval"})
UNTRUSTED_IDENTITY_FIELDS = frozenset(
    {
        "actor",
        "actor_id",
        "empleado_id",
        "responsible_user_id",
        "tenant",
        "tenant_id",
        "roles",
        "permissions",
        "capabilities",
        "facultades",
    }
)
REDACTED_INPUT_MARKER = "[REDACTED]"


@dataclass(frozen=True)
class ResolvedPrincipal:
    """Identity resolved only by trusted SamChat server-side authentication."""

    actor_id: str
    tenant_id: str
    roles: Tuple[str, ...]
    effective_capabilities: Tuple[str, ...]

    def is_complete(self) -> bool:
        return bool(self.actor_id.strip() and self.tenant_id.strip())


@dataclass(frozen=True)
class PolicyEnvelope:
    """The trusted identity and policy decision bound to one action attempt."""

    policy_version: str
    actor_id: Optional[str]
    tenant_id: Optional[str]
    roles: Tuple[str, ...]
    effective_capabilities: Tuple[str, ...]
    decision: str

    @classmethod
    def denied(
        cls, principal: Optional[ResolvedPrincipal]
    ) -> "PolicyEnvelope":
        return cls(
            policy_version="agent-action-policy-v0.1",
            actor_id=principal.actor_id if principal else None,
            tenant_id=principal.tenant_id if principal else None,
            roles=principal.roles if principal else (),
            effective_capabilities=(
                principal.effective_capabilities if principal else ()
            ),
            decision="deny",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    action_version: str
    action_type: str
    domain: str
    canonical_handler: str
    enabled: bool
    disabled_reason: Optional[str]
    required_inputs: Tuple[str, ...]
    required_scope_binding: str
    verifier: str

    @property
    def requires_idempotency(self) -> bool:
        return self.action_type in MUTATING_ACTION_TYPES

    def __post_init__(self) -> None:
        if self.action_type not in ACTION_TYPES:
            raise ValueError("unsupported action type")
        if self.enabled and self.disabled_reason:
            raise ValueError("enabled actions cannot have a disabled reason")
        if not self.enabled and not self.disabled_reason:
            raise ValueError("disabled actions require an explicit reason")


@dataclass(frozen=True)
class ActionRequest:
    """A trusted-adapter request that deliberately excludes identity."""

    action_id: str
    correlation_id: str
    payload: Mapping[str, Any]
    idempotency_key: Optional[str] = None


@dataclass(frozen=True)
class ActionReceipt:
    """Minimum evidence envelope that never claims an external effect."""

    receipt_id: str
    action_id: str
    action_version: str
    actor_id: Optional[str]
    tenant_id: Optional[str]
    correlation_id: Optional[str]
    normalized_redacted_inputs: Mapping[str, Any]
    policy_version: str
    policy_envelope: Mapping[str, Any]
    evaluated_preconditions: Tuple[str, ...]
    decision: str
    invoked_domain: Optional[str]
    result: str
    verifier: str
    timestamp: str
    idempotency_key: Optional[str] = None

    @classmethod
    def blocked(
        cls,
        *,
        action_id: str,
        action_version: str,
        principal: Optional[ResolvedPrincipal],
        request: Optional[ActionRequest],
        reason: str,
        invoked_domain: Optional[str],
        verifier: str = "not_run",
    ) -> "ActionReceipt":
        policy = PolicyEnvelope.denied(principal)
        return cls(
            receipt_id=str(uuid4()),
            action_id=action_id,
            action_version=action_version,
            actor_id=principal.actor_id if principal else None,
            tenant_id=principal.tenant_id if principal else None,
            correlation_id=request.correlation_id if request else None,
            normalized_redacted_inputs=normalize_and_redact(
                request.payload if request else {}
            ),
            policy_version=policy.policy_version,
            policy_envelope=policy.to_dict(),
            evaluated_preconditions=(reason,),
            decision="deny",
            invoked_domain=invoked_domain,
            result="not_invoked",
            verifier=verifier,
            timestamp=datetime.now(timezone.utc).isoformat(),
            idempotency_key=request.idempotency_key if request else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_and_redact(value: Any, *, field_name: str = "") -> Any:
    """Normalize input without retaining secret or raw-binary values."""

    lowered = field_name.lower()
    compact_name = "".join(character for character in lowered if character.isalnum())
    sensitive_terms = (
        "secret",
        "token",
        "password",
        "credential",
        "apikey",
        "authorization",
        "cookie",
        "privatekey",
    )
    if any(
        term in lowered or term in compact_name for term in sensitive_terms
    ):
        return REDACTED_INPUT_MARKER
    binary_fields = {
        "archivo_data",
        "file_data",
        "content",
        "bytes",
        "pdf_bytes",
    }
    if lowered in binary_fields:
        return REDACTED_INPUT_MARKER
    if isinstance(value, Mapping):
        return {
            str(key): normalize_and_redact(item, field_name=str(key))
            for key, item in sorted(
                value.items(), key=lambda item: str(item[0])
            )
        }
    if isinstance(value, (list, tuple)):
        return [
            normalize_and_redact(item, field_name=field_name) for item in value
        ]
    if isinstance(value, bytes):
        return REDACTED_INPUT_MARKER
    return value
