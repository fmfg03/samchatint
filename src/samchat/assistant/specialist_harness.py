"""Minimal read-only harness for SamChat specialist-agent benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Protocol, Tuple

from .operational_case import OperationalCase
from .samchat_task_schema import RubricCriterion, SamchatTask, SamchatVisibleTask
from .specialist_contract import AgentContract


PASS = "PASS"
FAIL = "FAIL"


@dataclass(frozen=True)
class SpecialistArtifact:
    artifact_type: str
    content: Mapping[str, Any]
    provenance: Tuple[str, ...] = ()
    unsupported_claims: Tuple[str, ...] = ()
    side_effects_detected: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SpecialistAgent(Protocol):
    contract: AgentContract

    def run(
        self,
        task: SamchatVisibleTask,
        cases: Tuple[OperationalCase, ...],
    ) -> SpecialistArtifact:
        ...


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    status: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkResult:
    task_id: str
    status: str
    artifact: SpecialistArtifact
    criteria: Tuple[CriterionResult, ...]
    side_effects_detected: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "artifact": self.artifact.to_dict(),
            "criteria": [item.to_dict() for item in self.criteria],
            "side_effects_detected": self.side_effects_detected,
        }


CriterionCheck = Callable[[SpecialistArtifact, RubricCriterion], CriterionResult]


def default_criterion_check(
    artifact: SpecialistArtifact,
    criterion: RubricCriterion,
) -> CriterionResult:
    checks = dict(criterion.checks or {})
    required_keys = tuple(checks.get("requires_artifact_keys") or ())
    missing = [key for key in required_keys if key not in artifact.content]
    if missing:
        return CriterionResult(
            criterion_id=criterion.criterion_id,
            status=FAIL,
            reason=f"missing_artifact_keys:{','.join(missing)}",
        )

    forbidden_side_effects = bool(checks.get("forbid_side_effects", True))
    if forbidden_side_effects and artifact.side_effects_detected:
        return CriterionResult(
            criterion_id=criterion.criterion_id,
            status=FAIL,
            reason="side_effects_detected",
        )

    return CriterionResult(
        criterion_id=criterion.criterion_id,
        status=PASS,
        reason="criterion_satisfied",
    )


def run_readonly_benchmark(
    *,
    task: SamchatTask,
    agent: SpecialistAgent,
    cases: Iterable[OperationalCase],
    criterion_check: CriterionCheck = default_criterion_check,
) -> BenchmarkResult:
    """Run one task through an agent with only the visible task payload.

    The private rubric remains available only to this evaluator. The agent sees
    SamchatVisibleTask, which intentionally has no ``criteria`` attribute.
    """

    task.validate()
    agent.contract.validate()
    case_tuple = tuple(case.validate() for case in cases)
    visible_task = task.agent_visible()
    if hasattr(visible_task, "criteria"):
        raise AssertionError("agent_visible_task_must_not_expose_criteria")

    artifact = agent.run(visible_task, case_tuple)
    criterion_results = tuple(
        criterion_check(artifact, criterion) for criterion in task.criteria
    )
    side_effects_detected = artifact.side_effects_detected
    status = (
        PASS
        if side_effects_detected == 0
        and all(result.status == PASS for result in criterion_results)
        else FAIL
    )
    return BenchmarkResult(
        task_id=task.task_id,
        status=status,
        artifact=artifact,
        criteria=criterion_results,
        side_effects_detected=side_effects_detected,
    )
