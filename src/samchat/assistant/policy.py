from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, FrozenSet, Mapping, Optional

from .tool_registry import AssistantToolSpec, WRITE


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str
    decision: str
    tool_name: str
    surface: Optional[str] = None
    operation_type: Optional[str] = None
    risk_level: Optional[str] = None

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_role(role: Optional[str]) -> str:
    normalized = (role or "").strip().lower()
    if normalized == "super_admin":
        return "superadmin"
    return normalized or "user"


READONLY_ROLE_CAPABILITIES: Mapping[str, FrozenSet[str]] = {
    "empleado": frozenset({"finance.self.read", "assistant.read"}),
    "user": frozenset({"finance.self.read", "assistant.read"}),
    "coordinador": frozenset(
        {"finance.team.read", "finance.self.read", "assistant.read"}
    ),
    "finanzas": frozenset(
        {
            "finance.ops.read",
            "finance.alerts.read",
            "finance.team.read",
            "finance.self.read",
            "assistant.read",
        }
    ),
    "admin": frozenset(
        {
            "finance.ops.read",
            "finance.alerts.read",
            "finance.team.read",
            "finance.self.read",
            "database.read",
            "assistant.read",
        }
    ),
    "superadmin": frozenset(
        {
            "finance.ops.read",
            "finance.alerts.read",
            "finance.team.read",
            "finance.self.read",
            "database.read",
            "dev.read",
            "assistant.read",
        }
    ),
}


def readonly_capabilities_for_role(role: Optional[str]) -> FrozenSet[str]:
    return READONLY_ROLE_CAPABILITIES.get(normalize_role(role), frozenset())


def evaluate_readonly_capability(
    *,
    role: Optional[str],
    capability: str,
    writes_enabled: bool = False,
) -> Dict[str, Any]:
    normalized_role = normalize_role(role)
    requested = (capability or "").strip()
    if not requested:
        return {
            "decision": "deny",
            "reason": "capability_missing",
            "role": normalized_role,
            "capability": requested,
            "writes_enabled": writes_enabled,
        }
    if requested.endswith(".write") or ".write." in requested:
        if writes_enabled:
            reason = "write_capability_denied"
        else:
            reason = "writes_disabled"
        return {
            "decision": "deny",
            "reason": reason,
            "role": normalized_role,
            "capability": requested,
            "writes_enabled": writes_enabled,
        }
    capabilities = readonly_capabilities_for_role(normalized_role)
    if requested not in capabilities:
        reason = (
            f"unknown_role:{normalized_role}"
            if normalized_role not in READONLY_ROLE_CAPABILITIES
            else f"capability_not_allowed:{requested}"
        )
        return {
            "decision": "deny",
            "reason": reason,
            "role": normalized_role,
            "capability": requested,
            "writes_enabled": writes_enabled,
        }
    return {
        "decision": "allow",
        "reason": "readonly_capability_allowed",
        "role": normalized_role,
        "capability": requested,
        "writes_enabled": writes_enabled,
    }


def evaluate_tool_policy(
    tool_spec: Optional[AssistantToolSpec],
    *,
    role: Optional[str],
    confirmed: bool = False,
) -> PolicyDecision:
    if tool_spec is None:
        return PolicyDecision(
            allowed=False,
            requires_confirmation=False,
            reason="unknown_tool",
            decision="deny",
            tool_name="",
        )

    normalized_role = normalize_role(role)
    if normalized_role not in tool_spec.allowed_roles:
        return PolicyDecision(
            allowed=False,
            requires_confirmation=tool_spec.requires_confirmation,
            reason=f"role_not_allowed:{normalized_role}",
            decision="deny",
            tool_name=tool_spec.name,
            surface=tool_spec.surface,
            operation_type=tool_spec.operation_type,
            risk_level=tool_spec.risk_level,
        )

    if (
        tool_spec.operation_type == WRITE
        and tool_spec.requires_confirmation
        and not confirmed
    ):
        return PolicyDecision(
            allowed=False,
            requires_confirmation=True,
            reason="write_requires_confirmation",
            decision="confirm",
            tool_name=tool_spec.name,
            surface=tool_spec.surface,
            operation_type=tool_spec.operation_type,
            risk_level=tool_spec.risk_level,
        )

    return PolicyDecision(
        allowed=True,
        requires_confirmation=False,
        reason="policy_allowed",
        decision="allow",
        tool_name=tool_spec.name,
        surface=tool_spec.surface,
        operation_type=tool_spec.operation_type,
        risk_level=tool_spec.risk_level,
    )
