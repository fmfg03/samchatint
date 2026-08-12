"""Common contracts for SamChat specialist agents.

This module is intentionally deterministic and side-effect free. It describes
what an agent may know, what tools it may request, and where human authority
must remain outside the agent runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


AGENT_TYPES: Tuple[str, ...] = (
    "institutional_knowledge",
    "evidence_verifier",
    "finance",
    "tournament_operations",
    "player_eligibility",
    "team_registration",
    "budget",
    "supplier_procurement",
    "executive_reporting",
)

CASE_TYPES: Tuple[str, ...] = (
    "tournament",
    "team",
    "player_validation",
    "document_incident",
    "money_request",
    "expense_report",
    "budget",
    "supplier",
)

TOOL_MODES: Tuple[str, ...] = ("read", "propose", "write")

AUTHORITY_BOUNDARIES: Tuple[str, ...] = (
    "read_only",
    "proposal_only",
    "human_approval_required",
)

PHASE_ONE_AUTHORITY_BOUNDARY = "human_approval_required"


@dataclass(frozen=True)
class ToolPermission:
    """A tool an agent may use or propose.

    Phase 1 allows read/propose permissions. A write tool may be described as a
    future capability, but it must be marked as needing human approval and must
    not execute in the benchmark harness.
    """

    name: str
    mode: str = "read"
    requires_human_approval: bool = True
    side_effects_allowed: bool = False

    def errors(self) -> List[str]:
        errors: List[str] = []
        if not self.name.strip():
            errors.append("tool_name_required")
        if self.mode not in TOOL_MODES:
            errors.append(f"unsupported_tool_mode:{self.mode}")
        if self.side_effects_allowed:
            errors.append("tool_side_effects_forbidden_in_phase_one")
        if self.mode == "write" and not self.requires_human_approval:
            errors.append("write_tool_requires_human_approval")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentContract:
    """Explicit contract for one SamChat specialist agent."""

    agent_id: str
    agent_type: str
    description: str
    allowed_case_types: Tuple[str, ...]
    allowed_tools: Tuple[ToolPermission, ...] = ()
    authority_boundary: str = PHASE_ONE_AUTHORITY_BOUNDARY
    may_execute_external_side_effects: bool = False
    output_artifacts: Tuple[str, ...] = ()

    def errors(self) -> List[str]:
        errors: List[str] = []
        if not self.agent_id.strip():
            errors.append("agent_id_required")
        if self.agent_type not in AGENT_TYPES:
            errors.append(f"unsupported_agent_type:{self.agent_type}")
        if not self.description.strip():
            errors.append("description_required")
        for case_type in self.allowed_case_types:
            if case_type not in CASE_TYPES:
                errors.append(f"unsupported_case_type:{case_type}")
        if self.authority_boundary not in AUTHORITY_BOUNDARIES:
            errors.append(
                f"unsupported_authority_boundary:{self.authority_boundary}"
            )
        if self.may_execute_external_side_effects:
            errors.append("agent_side_effects_forbidden_in_phase_one")
        for permission in self.allowed_tools:
            errors.extend(permission.errors())
        return errors

    def validate(self) -> "AgentContract":
        errors = self.errors()
        if errors:
            raise ValueError(", ".join(errors))
        return self

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = [
            permission.to_dict() for permission in self.allowed_tools
        ]
        return payload


def validate_agent_contract(contract: AgentContract) -> List[str]:
    """Return validation errors without raising."""

    return contract.errors()


def build_initial_specialist_contracts() -> Tuple[AgentContract, ...]:
    """Return the three initial contracts named in RQF-ASSISTANT-052."""

    return (
        AgentContract(
            agent_id="institutional_knowledge_v0",
            agent_type="institutional_knowledge",
            description=(
                "Finds relevant operational precedents and marks missing "
                "evidence without creating authority."
            ),
            allowed_case_types=("tournament", "team", "expense_report", "supplier"),
            allowed_tools=(ToolPermission(name="case_search", mode="read"),),
            output_artifacts=("precedent_pack",),
        ).validate(),
        AgentContract(
            agent_id="evidence_verifier_v0",
            agent_type="evidence_verifier",
            description=(
                "Checks that every claim is bound to the exact operational "
                "case fact and evidence source."
            ),
            allowed_case_types=CASE_TYPES,
            allowed_tools=(ToolPermission(name="evidence_lookup", mode="read"),),
            output_artifacts=("verification_report",),
        ).validate(),
        AgentContract(
            agent_id="finance_v0",
            agent_type="finance",
            description=(
                "Prepares accounting and finance proposals only from verified "
                "facts; it never posts real entries."
            ),
            allowed_case_types=(
                "money_request",
                "expense_report",
                "budget",
                "supplier",
            ),
            allowed_tools=(ToolPermission(name="finance_preview", mode="propose"),),
            output_artifacts=("finance_proposal",),
        ).validate(),
    )


def contracts_by_agent_id(
    contracts: Iterable[AgentContract],
) -> Mapping[str, AgentContract]:
    return {contract.agent_id: contract for contract in contracts}
