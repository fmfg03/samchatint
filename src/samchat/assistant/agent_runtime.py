from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from .policy import evaluate_tool_policy
from .tool_registry import AssistantToolSpec, get_tool_spec


TRUE_VALUES = {"1", "true", "yes", "on"}

SHADOW_DISABLED = "SHADOW_DISABLED"
SHADOW_ALLOWLIST_EMPTY = "SHADOW_ALLOWLIST_EMPTY"
SHADOW_SUBJECT_NOT_ALLOWED = "SHADOW_SUBJECT_NOT_ALLOWED"
SHADOW_ALLOWED_EMPLOYEE_ID = "SHADOW_ALLOWED_EMPLOYEE_ID"
SHADOW_ALLOWED_TENANT_EMPLOYEE = "SHADOW_ALLOWED_TENANT_EMPLOYEE"
SHADOW_ALLOWED_EMAIL_FALLBACK = "SHADOW_ALLOWED_EMAIL_FALLBACK"


@dataclass
class AgentRuntimeTrace:
    route: str
    surface: Optional[str]
    available_tools: List[str] = field(default_factory=list)
    proposed_tools: List[Dict[str, Any]] = field(default_factory=list)
    policy_decisions: List[Dict[str, Any]] = field(default_factory=list)
    executed_tools: List[Dict[str, Any]] = field(default_factory=list)
    blocked_tools: List[Dict[str, Any]] = field(default_factory=list)

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowActivation:
    enabled: bool
    decision: str
    employee_id_present: bool
    tenant_id_present: bool
    email_hash: Optional[str] = None

    def to_trace(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "decision": self.decision,
            "subject": {
                "employee_id_present": self.employee_id_present,
                "tenant_id_present": self.tenant_id_present,
                "email_hash": self.email_hash,
            },
            "legacy_authoritative": True,
            "side_effects_allowed": False,
        }


def is_agent_runtime_enabled() -> bool:
    return (
        (os.getenv("ASSISTANT_AGENT_RUNTIME_ENABLED") or "").strip().lower()
        in TRUE_VALUES
    )


def is_agent_shadow_enabled() -> bool:
    return (
        (os.getenv("ASSISTANT_AGENT_SHADOW_ENABLED") or "").strip().lower()
        in TRUE_VALUES
    )


def _csv_set(env_var: str) -> Set[str]:
    raw = os.getenv(env_var) or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def _email_hash(email: Optional[str]) -> Optional[str]:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def evaluate_shadow_activation(
    *,
    employee_id: Optional[Any] = None,
    email: Optional[str] = None,
    tenant_id: Optional[Any] = None,
) -> ShadowActivation:
    employee_key = str(employee_id).strip() if employee_id is not None else ""
    tenant_key = str(tenant_id).strip() if tenant_id is not None else ""
    normalized_email = _normalize_email(email)
    employee_ids = _csv_set("ASSISTANT_AGENT_SHADOW_EMPLOYEE_IDS")
    tenant_ids = _csv_set("ASSISTANT_AGENT_SHADOW_TENANT_IDS")
    subjects = _csv_set("ASSISTANT_AGENT_SHADOW_SUBJECTS")
    emails = {
        _normalize_email(value)
        for value in _csv_set("ASSISTANT_AGENT_SHADOW_EMAILS")
    }
    emails.discard("")

    employee_id_present = bool(employee_key)
    tenant_id_present = bool(tenant_key)
    email_hash = _email_hash(normalized_email)

    if not is_agent_shadow_enabled():
        return ShadowActivation(
            enabled=False,
            decision=SHADOW_DISABLED,
            employee_id_present=employee_id_present,
            tenant_id_present=tenant_id_present,
            email_hash=email_hash,
        )

    if not (employee_ids or tenant_ids or subjects or emails):
        return ShadowActivation(
            enabled=False,
            decision=SHADOW_ALLOWLIST_EMPTY,
            employee_id_present=employee_id_present,
            tenant_id_present=tenant_id_present,
            email_hash=email_hash,
        )

    if employee_key:
        if tenant_key and f"{tenant_key}:{employee_key}" in subjects:
            return ShadowActivation(
                enabled=True,
                decision=SHADOW_ALLOWED_TENANT_EMPLOYEE,
                employee_id_present=True,
                tenant_id_present=tenant_id_present,
                email_hash=email_hash,
            )
        if employee_key in employee_ids and (
            not tenant_ids or not tenant_key or tenant_key in tenant_ids
        ):
            return ShadowActivation(
                enabled=True,
                decision=SHADOW_ALLOWED_EMPLOYEE_ID,
                employee_id_present=True,
                tenant_id_present=tenant_id_present,
                email_hash=email_hash,
            )
        return ShadowActivation(
            enabled=False,
            decision=SHADOW_SUBJECT_NOT_ALLOWED,
            employee_id_present=True,
            tenant_id_present=tenant_id_present,
            email_hash=email_hash,
        )

    if normalized_email and normalized_email in emails:
        return ShadowActivation(
            enabled=True,
            decision=SHADOW_ALLOWED_EMAIL_FALLBACK,
            employee_id_present=False,
            tenant_id_present=tenant_id_present,
            email_hash=email_hash,
        )

    return ShadowActivation(
        enabled=False,
        decision=SHADOW_SUBJECT_NOT_ALLOWED,
        employee_id_present=False,
        tenant_id_present=tenant_id_present,
        email_hash=email_hash,
    )


def build_agent_shadow_trace(
    *,
    activation: ShadowActivation,
    route_info: Mapping[str, Any],
    tool_defs: Iterable[Mapping[str, Any]],
    registry: Mapping[str, AssistantToolSpec],
) -> Dict[str, Any]:
    trace = activation.to_trace()
    trace["route"] = str(route_info.get("route") or "")
    trace["surface"] = str(route_info.get("domain") or "") or None
    trace["available_tools"] = []
    if activation.enabled:
        trace["available_tools"] = build_agent_runtime_trace(
            route_info=route_info,
            tool_defs=tool_defs,
            registry=registry,
        )["assistant_agent_runtime"]["available_tools"]
    return {"assistant_agent_shadow": trace}


def _tool_name(tool_def: Mapping[str, Any]) -> str:
    return str(((tool_def.get("function") or {}).get("name")) or "").strip()


def build_agent_runtime_trace(
    *,
    route_info: Mapping[str, Any],
    tool_defs: Iterable[Mapping[str, Any]],
    registry: Mapping[str, AssistantToolSpec],
) -> Dict[str, Any]:
    available_tools = []
    for tool_def in tool_defs:
        name = _tool_name(tool_def)
        if name and name in registry:
            available_tools.append(name)
    trace = AgentRuntimeTrace(
        route=str(route_info.get("route") or ""),
        surface=str(route_info.get("domain") or "") or None,
        available_tools=sorted(available_tools),
    )
    return {"assistant_agent_runtime": trace.to_trace()}


def evaluate_runtime_tool_call(
    *,
    tool_name: str,
    args: Mapping[str, Any],
    role: Optional[str],
    registry: Mapping[str, AssistantToolSpec],
    confirmed: bool = False,
) -> Dict[str, Any]:
    spec = get_tool_spec(registry, tool_name)
    decision = evaluate_tool_policy(spec, role=role, confirmed=confirmed)
    trace = decision.to_trace()
    if not trace.get("tool_name"):
        trace["tool_name"] = (tool_name or "").strip()
    trace["args_keys"] = sorted(str(key) for key in args.keys())
    return trace


def evaluate_shadow_tool_call(
    *,
    tool_name: str,
    args: Mapping[str, Any],
    role: Optional[str],
    registry: Mapping[str, AssistantToolSpec],
) -> Dict[str, Any]:
    policy = evaluate_runtime_tool_call(
        tool_name=tool_name,
        args=args,
        role=role,
        registry=registry,
    )
    if policy.get("decision") == "confirm":
        outcome = "PENDING"
    elif policy.get("decision") == "allow":
        outcome = "ALLOW"
    else:
        outcome = "BLOCK"
    return {
        "assistant_agent_shadow_policy": {
            "tool_name": policy.get("tool_name") or (tool_name or "").strip(),
            "outcome": outcome,
            "policy": policy,
            "legacy_authoritative": True,
            "side_effects_allowed": False,
            "handler_invoked": False,
        }
    }
