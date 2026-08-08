"""Read-only/propose-only orchestrator for SamChat specialist agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .operational_case import OperationalCase
from .specialist_agents import AgentArtifact, AgentRunResult, default_agent_contracts, default_agents
from .specialist_task import AgentVisibleTask, SamchatTask


@dataclass(frozen=True)
class OrchestratorPlan:
    task_id: str
    route: tuple[str, ...]
    authority_boundary: str
    child_results: tuple[AgentRunResult, ...]
    side_effects_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "route": list(self.route),
            "authority_boundary": self.authority_boundary,
            "child_results": [result.to_dict() for result in self.child_results],
            "side_effects_detected": self.side_effects_detected,
        }


def route_cross_agent_task(task: SamchatTask, cases: Sequence[OperationalCase]) -> OrchestratorPlan:
    """Compose Knowledge → Verifier → Finance with explicit artifact handoff."""

    route = ("institutional_knowledge", "evidence_verifier", "finance")
    contracts = default_agent_contracts()
    agents = default_agents()
    child_results: list[AgentRunResult] = []
    handoff_artifacts: tuple[AgentArtifact, ...] = ()

    for agent_id in route:
        contract = contracts[agent_id]
        child_task = AgentVisibleTask(
            task_id=f"{task.task_id}:{agent_id}",
            title=f"{task.title} / {agent_id}",
            agent=agent_id,
            case_type=task.case_type,
            instructions=task.instructions,
            authority_boundary=contract.authority_boundary,
            allowed_tools=contract.tools_allowed,
            tags=task.tags,
        ).validate()
        result = agents[agent_id].run(child_task, tuple(cases), handoff_artifacts)
        child_results.append(result)
        handoff_artifacts = result.artifacts

    return OrchestratorPlan(
        task_id=task.task_id,
        route=route,
        authority_boundary="PROPOSE_ONLY",
        child_results=tuple(child_results),
        side_effects_detected=any(result.side_effects_detected for result in child_results),
    )
