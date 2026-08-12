"""Task schema for specialist-agent benchmarks.

The central safety property is that agents receive a visible task without the
private rubric/ground truth. Evaluators may read criteria; solvers may not.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .specialist_contract import AGENT_TYPES, CASE_TYPES, PHASE_ONE_AUTHORITY_BOUNDARY


@dataclass(frozen=True)
class RubricCriterion:
    criterion_id: str
    description: str
    checks: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True

    def errors(self) -> List[str]:
        errors: List[str] = []
        if not self.criterion_id.strip():
            errors.append("criterion_id_required")
        if not self.description.strip():
            errors.append("criterion_description_required")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SamchatVisibleTask:
    task_id: str
    title: str
    agent_type: str
    case_type: str
    instructions: str
    allowed_case_ids: Tuple[str, ...] = ()
    allowed_tools: Tuple[str, ...] = ()
    expected_output_artifacts: Tuple[str, ...] = ()
    authority_boundary: str = PHASE_ONE_AUTHORITY_BOUNDARY
    tags: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload.pop("criteria", None)
        return payload


@dataclass(frozen=True)
class SamchatTask:
    task_id: str
    title: str
    agent_type: str
    case_type: str
    instructions: str
    criteria: Tuple[RubricCriterion, ...]
    allowed_case_ids: Tuple[str, ...] = ()
    allowed_tools: Tuple[str, ...] = ()
    expected_output_artifacts: Tuple[str, ...] = ()
    authority_boundary: str = PHASE_ONE_AUTHORITY_BOUNDARY
    tags: Tuple[str, ...] = ()

    def agent_visible(self) -> SamchatVisibleTask:
        return SamchatVisibleTask(
            task_id=self.task_id,
            title=self.title,
            agent_type=self.agent_type,
            case_type=self.case_type,
            instructions=self.instructions,
            allowed_case_ids=self.allowed_case_ids,
            allowed_tools=self.allowed_tools,
            expected_output_artifacts=self.expected_output_artifacts,
            authority_boundary=self.authority_boundary,
            tags=self.tags,
        )

    def errors(self) -> List[str]:
        errors: List[str] = []
        if not self.task_id.strip():
            errors.append("task_id_required")
        if not self.title.strip():
            errors.append("task_title_required")
        if self.agent_type not in AGENT_TYPES:
            errors.append(f"unsupported_agent_type:{self.agent_type}")
        if self.case_type not in CASE_TYPES:
            errors.append(f"unsupported_case_type:{self.case_type}")
        if not self.instructions.strip():
            errors.append("task_instructions_required")
        if self.authority_boundary != PHASE_ONE_AUTHORITY_BOUNDARY:
            errors.append("phase_one_requires_human_authority_boundary")
        if not self.criteria:
            errors.append("private_rubric_required")
        for criterion in self.criteria:
            errors.extend(criterion.errors())
        return errors

    def validate(self) -> "SamchatTask":
        errors = self.errors()
        if errors:
            raise ValueError(", ".join(errors))
        return self

    def to_dict(self, *, include_private_rubric: bool = False) -> Dict[str, Any]:
        payload = asdict(self)
        if include_private_rubric:
            payload["criteria"] = [criterion.to_dict() for criterion in self.criteria]
        else:
            payload.pop("criteria", None)
        return payload


def _tuple(value: Optional[Sequence[Any]]) -> Tuple[str, ...]:
    return tuple(str(item) for item in (value or ()))


def task_from_mapping(payload: Mapping[str, Any]) -> SamchatTask:
    criteria = tuple(
        RubricCriterion(
            criterion_id=str(item.get("criterion_id", "")),
            description=str(item.get("description", "")),
            checks=dict(item.get("checks") or {}),
            required=bool(item.get("required", True)),
        )
        for item in payload.get("criteria", ())
    )
    return SamchatTask(
        task_id=str(payload.get("task_id", "")),
        title=str(payload.get("title", "")),
        agent_type=str(payload.get("agent_type", "")),
        case_type=str(payload.get("case_type", "")),
        instructions=str(payload.get("instructions", "")),
        criteria=criteria,
        allowed_case_ids=_tuple(payload.get("allowed_case_ids")),
        allowed_tools=_tuple(payload.get("allowed_tools")),
        expected_output_artifacts=_tuple(payload.get("expected_output_artifacts")),
        authority_boundary=str(
            payload.get("authority_boundary", PHASE_ONE_AUTHORITY_BOUNDARY)
        ),
        tags=_tuple(payload.get("tags")),
    ).validate()


def validate_samchat_task(task: SamchatTask) -> List[str]:
    return task.errors()
