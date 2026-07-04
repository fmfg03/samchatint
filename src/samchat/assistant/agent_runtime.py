from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .policy import evaluate_tool_policy
from .tool_registry import AssistantToolSpec, get_tool_spec


TRUE_VALUES = {"1", "true", "yes", "on"}


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


def is_agent_runtime_enabled() -> bool:
    return (
        (os.getenv("ASSISTANT_AGENT_RUNTIME_ENABLED") or "").strip().lower()
        in TRUE_VALUES
    )


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
