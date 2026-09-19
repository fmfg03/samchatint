"""Typed, transport-neutral contracts for the disabled v0.1 action catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
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
class InputFieldSchema:
    """One typed field accepted by a canonical action contract."""

    name: str
    value_type: str
    required: bool = True
    sensitive: bool = False

    def __post_init__(self) -> None:
        if self.value_type not in {"string", "integer", "decimal"}:
            raise ValueError("unsupported action input type")


@dataclass(frozen=True)
class ActionInputSchema:
    """Typed, transport-neutral input schema for a registered action."""

    fields: Tuple[InputFieldSchema, ...]
    allow_additional_fields: bool = True

    def __post_init__(self) -> None:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("action input schema contains duplicate fields")


def input_schema_is_valid(
    schema: ActionInputSchema, payload: Mapping[str, Any]
) -> bool:
    """Validate declared shape and scalar types, never domain semantics."""

    fields_by_name = {field.name: field for field in schema.fields}
    if not schema.allow_additional_fields:
        if any(str(name) not in fields_by_name for name in payload):
            return False

    for field in schema.fields:
        value = payload.get(field.name)
        missing = field.name not in payload or value is None
        if isinstance(value, str) and field.required and not value.strip():
            missing = True
        if missing:
            if field.required:
                return False
            continue
        if not _input_value_matches_type(value, field.value_type):
            return False
    return True


def _input_value_matches_type(value: Any, value_type: str) -> bool:
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "decimal":
        if isinstance(value, bool) or not isinstance(
            value, (Decimal, int, str, float)
        ):
            return False
        try:
            return Decimal(str(value)).is_finite()
        except (InvalidOperation, ValueError):
            return False
    raise ValueError("unsupported action input type")


@dataclass(frozen=True)
class ActionPolicy:
    """Identifiable policy evaluated by the action perimeter."""

    policy_id: str
    policy_version: str


@dataclass(frozen=True)
class ActionPrecondition:
    """A machine-readable condition required before any dispatch."""

    code: str
    description: str
    enforced_by: str


@dataclass(frozen=True)
class PolicyEnvelope:
    """The trusted identity and policy decision bound to one action attempt."""

    policy_id: str
    policy_version: str
    actor_id: Optional[str]
    tenant_id: Optional[str]
    roles: Tuple[str, ...]
    effective_capabilities: Tuple[str, ...]
    decision: str

    @classmethod
    def denied(
        cls,
        principal: Optional[ResolvedPrincipal],
        policy: Optional[ActionPolicy] = None,
    ) -> "PolicyEnvelope":
        return cls(
            policy_id=policy.policy_id if policy else "unregistered-action",
            policy_version=(
                policy.policy_version if policy else "agent-action-policy-v0.1"
            ),
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
    input_schema: ActionInputSchema
    policy: ActionPolicy
    preconditions: Tuple[ActionPrecondition, ...]
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
        if not self.preconditions:
            raise ValueError("actions require structured preconditions")


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
    error_code: Optional[str]
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
        policy: Optional[ActionPolicy] = None,
        error_code: Optional[str] = None,
    ) -> "ActionReceipt":
        policy_envelope = PolicyEnvelope.denied(principal, policy)
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
            policy_version=policy_envelope.policy_version,
            policy_envelope=policy_envelope.to_dict(),
            error_code=error_code,
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
    sensitive_terms = ("secret", "token", "password", "credential")
    if any(token in lowered for token in sensitive_terms):
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


def find_untrusted_identity_paths(
    value: Any, *, path: Tuple[str, ...] = ()
) -> Tuple[str, ...]:
    """Find caller-supplied identity fields at every payload depth."""

    found = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.lower().replace("-", "_")
            item_path = path + (key,)
            if normalized_key in UNTRUSTED_IDENTITY_FIELDS:
                found.append(".".join(item_path))
            found.extend(find_untrusted_identity_paths(item, path=item_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(
                find_untrusted_identity_paths(
                    item, path=path + (str(index),)
                )
            )
    return tuple(found)
