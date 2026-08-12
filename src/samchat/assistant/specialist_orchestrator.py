"""Read-only orchestrator for SamChat specialist-agent workflows.

The v0 orchestrator does not add intelligence yet. Its job is to make the
composition explicit and testable: choose a route, run the approved specialist
handoff, record every step, and keep the authority boundary outside the agent
runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Tuple

from .operational_case import OperationalCase
from .samchat_task_schema import SamchatVisibleTask
from .specialist_agents import SpecialistWorkflowResult, run_specialist_workflow
from .specialist_harness import FAIL, PASS


VERIFIED_FINANCE_PREVIEW_ROUTE = "verified_finance_preview_v0"
UNSUPPORTED_ROUTE = "unsupported_specialist_route_v0"


@dataclass(frozen=True)
class OrchestratorStep:
    step_id: str
    agent_id: str
    action: str
    artifact_type: str
    status: str
    side_effects_detected: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrchestratorResult:
    task_id: str
    route: str
    status: str
    steps: Tuple[OrchestratorStep, ...]
    workflow: SpecialistWorkflowResult
    authority_boundary: str
    execution_allowed: bool = False
    side_effects_detected: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "route": self.route,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
            "workflow": self.workflow.to_dict(),
            "authority_boundary": self.authority_boundary,
            "execution_allowed": self.execution_allowed,
            "side_effects_detected": self.side_effects_detected,
        }


class UnsupportedSpecialistRouteError(ValueError):
    """Raised when the orchestrator cannot safely route a visible task."""


def select_specialist_route(task: SamchatVisibleTask) -> str:
    """Select the phase-one route for a visible specialist task.

    The first route is intentionally conservative: all current seed tasks use
    the Knowledge -> Verifier -> Finance handoff and remain proposal-only. When
    additional specialist capabilities are implemented, this function becomes
    the single place where routing decisions are made visible and testable.
    """

    expected = set(task.expected_output_artifacts or ())
    if {"precedent_pack", "verification_report", "finance_proposal"}.issubset(
        expected
    ):
        return VERIFIED_FINANCE_PREVIEW_ROUTE
    return UNSUPPORTED_ROUTE


def _step(
    *,
    step_id: str,
    agent_id: str,
    action: str,
    artifact_type: str,
    status: str,
    side_effects_detected: int,
) -> OrchestratorStep:
    return OrchestratorStep(
        step_id=step_id,
        agent_id=agent_id,
        action=action,
        artifact_type=artifact_type,
        status=status,
        side_effects_detected=side_effects_detected,
    )


def _trace_for_workflow(workflow: SpecialistWorkflowResult) -> Tuple[OrchestratorStep, ...]:
    return (
        _step(
            step_id="01-knowledge",
            agent_id="institutional_knowledge_v0",
            action="find_precedents_and_candidate_claims",
            artifact_type=workflow.knowledge.artifact_type,
            status=PASS if workflow.knowledge.side_effects_detected == 0 else FAIL,
            side_effects_detected=workflow.knowledge.side_effects_detected,
        ),
        _step(
            step_id="02-verifier",
            agent_id="evidence_verifier_v0",
            action="verify_exact_fact_value_and_evidence_binding",
            artifact_type=workflow.verification.artifact_type,
            status=PASS if not workflow.verification.unsupported_claims else FAIL,
            side_effects_detected=workflow.verification.side_effects_detected,
        ),
        _step(
            step_id="03-finance",
            agent_id="finance_v0",
            action="prepare_preview_only_finance_proposal",
            artifact_type=workflow.finance.artifact_type,
            status=(
                PASS
                if workflow.finance.side_effects_detected == 0
                and workflow.finance.content.get("execution_allowed") is False
                else FAIL
            ),
            side_effects_detected=workflow.finance.side_effects_detected,
        ),
    )


def run_specialist_orchestrator(
    *,
    task: SamchatVisibleTask,
    cases: Iterable[OperationalCase],
) -> OrchestratorResult:
    """Run a visible task through the first governed specialist route."""

    if hasattr(task, "criteria"):
        raise AssertionError("orchestrator_visible_task_must_not_expose_criteria")

    route = select_specialist_route(task)
    if route != VERIFIED_FINANCE_PREVIEW_ROUTE:
        raise UnsupportedSpecialistRouteError(f"unsupported_route:{route}")

    workflow = run_specialist_workflow(task=task, cases=cases)
    steps = _trace_for_workflow(workflow)
    side_effects = workflow.side_effects_detected
    finance_execution_allowed = bool(
        workflow.finance.content.get("execution_allowed", False)
    )
    status = (
        PASS
        if workflow.status == PASS
        and side_effects == 0
        and not finance_execution_allowed
        and all(step.status == PASS for step in steps)
        else FAIL
    )
    return OrchestratorResult(
        task_id=task.task_id,
        route=route,
        status=status,
        steps=steps,
        workflow=workflow,
        authority_boundary=str(
            workflow.finance.content.get("authority_boundary", task.authority_boundary)
        ),
        execution_allowed=finance_execution_allowed,
        side_effects_detected=side_effects,
    )
