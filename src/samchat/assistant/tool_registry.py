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
    if name.startswith("workspace_"):
        return "workspace"
    if name == "assistant_finance_read":
        return "finance"
    if name.startswith("assistant_") and name not in {
        "assistant_canonical_action",
        "assistant_canonical_query",
    }:
        return "assistant"
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
    if name in {
        "tournament_goal_shadow",
        "tournament_proposal_review",
    } or name.startswith("tournament_draft_"):
        return ("admin", "superadmin")
    if surface == "workspace":
        return (
            "empleado",
            "user",
            "coordinador",
            "finanzas",
            "admin",
            "superadmin",
        )
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


@dataclass(frozen=True)
class SemanticAssistantToolSpec:
    """Stable semantic metadata used by the WorkFrame adjudicator.

    The provider-facing registry says whether a tool is read/write and who can
    use it. This semantic registry says what business job a read-only tool can
    actually answer, so the runtime can reject safe-but-wrong tools.
    """

    name: str
    domains: tuple[str, ...]
    task_kinds: tuple[str, ...]
    evidence_outputs: tuple[str, ...]
    rejected_interpretations: tuple[str, ...] = ()
    read_only: bool = True

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


SEMANTIC_TOOL_REGISTRY: Dict[str, SemanticAssistantToolSpec] = {
    "assistant_owner_pack_readiness": SemanticAssistantToolSpec(
        name="assistant_owner_pack_readiness",
        domains=("owner",),
        task_kinds=("readiness", "evidence"),
        evidence_outputs=("owner_pack_readiness", "missing_owner_pack_fields"),
    ),
    "assistant_owner_pack_readiness_dashboard": SemanticAssistantToolSpec(
        name="assistant_owner_pack_readiness_dashboard",
        domains=("owner",),
        task_kinds=("readiness", "evidence"),
        evidence_outputs=("owner_pack_readiness", "coverage", "missing_items"),
    ),
    "assistant_owner_entity_folder_workspace": SemanticAssistantToolSpec(
        name="assistant_owner_entity_folder_workspace",
        domains=("owner",),
        task_kinds=("readiness", "evidence"),
        evidence_outputs=("entity_folder", "operations_evidence", "finance_evidence"),
    ),
    "assistant_owner_variable_query": SemanticAssistantToolSpec(
        name="assistant_owner_variable_query",
        domains=("owner", "mixed"),
        task_kinds=("evidence", "status"),
        evidence_outputs=("owner_variable_source", "live_evidence_or_missing_reason"),
        rejected_interpretations=("pending_payment_queue",),
    ),
    "assistant_finance_accounting_qa": SemanticAssistantToolSpec(
        name="assistant_finance_accounting_qa",
        domains=("finance", "mixed"),
        task_kinds=("status", "diagnostic", "evidence"),
        evidence_outputs=("finance_accounting_snapshot", "blocking_items", "module_routes"),
        rejected_interpretations=("owner_pack_readiness",),
    ),
    "finance.read_only_comparison": SemanticAssistantToolSpec(
        name="finance.read_only_comparison",
        domains=("finance",),
        task_kinds=("diagnostic", "status"),
        evidence_outputs=("finance_comparison_rows",),
    ),
    "receipts.cfdi_matching_overview": SemanticAssistantToolSpec(
        name="receipts.cfdi_matching_overview",
        domains=("finance",),
        task_kinds=("evidence", "diagnostic", "status"),
        evidence_outputs=("cfdi_matching_status", "unlinked_cfdis"),
    ),
    "receipts.pending_payment_overview": SemanticAssistantToolSpec(
        name="receipts.pending_payment_overview",
        domains=("finance",),
        task_kinds=("status",),
        evidence_outputs=("pending_payment_queue",),
        rejected_interpretations=("historical_payment_evidence",),
    ),
}


def get_semantic_tool_spec(name: str | None) -> Optional[SemanticAssistantToolSpec]:
    return SEMANTIC_TOOL_REGISTRY.get(str(name or "").strip())


def semantic_tool_matches_work_frame(
    *,
    name: str | None,
    domain: str,
    task_kind: str,
) -> bool:
    spec = get_semantic_tool_spec(name)
    if spec is None:
        return False
    domain_matches = domain in spec.domains or "mixed" in spec.domains
    task_matches = task_kind in spec.task_kinds or "unknown" in spec.task_kinds
    return domain_matches and task_matches


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
