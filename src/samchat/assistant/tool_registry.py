from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set


READ = "read"
WRITE = "write"


@dataclass(frozen=True)
class AssistantToolSpec:
    name: str
    surface: str
    operation_type: str
    risk_level: str
    requires_confirmation: bool
    allowed_roles: tuple[str, ...]
    handler_kind: str

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def _tool_name(tool_def: Mapping[str, Any]) -> str:
    return str(((tool_def.get("function") or {}).get("name")) or "").strip()


def _surface_for_tool(
    name: str,
    *,
    finance_tools: Set[str],
    tournament_tools: Set[str],
    dev_tools: Set[str],
) -> str:
    if name in dev_tools:
        return "dev"
    if name.startswith("db_"):
        return "database"
    if name in finance_tools and name in tournament_tools:
        return "cross_domain"
    if name in finance_tools:
        return "finance"
    if name in tournament_tools:
        return "tournament"
    if name.startswith("assistant_"):
        return "assistant"
    return "general"


def _risk_for_tool(name: str, operation_type: str, surface: str) -> str:
    if operation_type == WRITE:
        if surface in {"dev", "database"}:
            return "critical"
        if surface in {"finance", "tournament", "cross_domain"}:
            return "high"
        return "medium"
    if surface in {"database", "dev"}:
        return "medium"
    return "low"


def _allowed_roles_for_tool(
    name: str, operation_type: str, surface: str
) -> tuple[str, ...]:
    if operation_type == READ:
        if surface in {"database", "dev"}:
            return ("admin", "superadmin")
        return ("user", "admin", "superadmin")
    if surface in {"database", "dev"} or name.startswith("dev_"):
        return ("superadmin",)
    return ("admin", "superadmin")


def _handler_kind_for_tool(name: str) -> str:
    if name in {"assistant_canonical_action", "assistant_canonical_query"}:
        return "canonical_action"
    return "existing_tool"


def build_tool_registry(
    *,
    tool_defs: Iterable[Mapping[str, Any]],
    read_tools: Set[str],
    write_tools: Set[str],
    finance_tools: Set[str],
    tournament_tools: Set[str],
    dev_tools: Set[str],
) -> Dict[str, AssistantToolSpec]:
    registry: Dict[str, AssistantToolSpec] = {}
    known_tools = set(read_tools) | set(write_tools)

    for tool_def in tool_defs:
        name = _tool_name(tool_def)
        if not name or name not in known_tools:
            continue
        operation_type = WRITE if name in write_tools else READ
        surface = _surface_for_tool(
            name,
            finance_tools=finance_tools,
            tournament_tools=tournament_tools,
            dev_tools=dev_tools,
        )
        registry[name] = AssistantToolSpec(
            name=name,
            surface=surface,
            operation_type=operation_type,
            risk_level=_risk_for_tool(name, operation_type, surface),
            requires_confirmation=operation_type == WRITE,
            allowed_roles=_allowed_roles_for_tool(name, operation_type, surface),
            handler_kind=_handler_kind_for_tool(name),
        )
    return registry


def get_tool_spec(
    registry: Mapping[str, AssistantToolSpec],
    name: str,
) -> Optional[AssistantToolSpec]:
    return registry.get((name or "").strip())


def filter_tool_defs_by_policy(
    tool_defs: Iterable[Mapping[str, Any]],
    registry: Mapping[str, AssistantToolSpec],
    *,
    allowed_surfaces: Optional[Set[str]] = None,
    include_writes: bool = True,
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for tool_def in tool_defs:
        name = _tool_name(tool_def)
        spec = get_tool_spec(registry, name)
        if spec is None:
            continue
        if allowed_surfaces and spec.surface not in allowed_surfaces:
            continue
        if not include_writes and spec.operation_type == WRITE:
            continue
        filtered.append(dict(tool_def))
    return filtered
