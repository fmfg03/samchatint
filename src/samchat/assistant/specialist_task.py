"""Harvey-style task schema adapted for SamChat specialist agents."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .operational_case import SUPPORTED_CASE_TYPES

SUPPORTED_AUTHORITY_BOUNDARIES = {"READ_ONLY", "PROPOSE_ONLY"}
SUPPORTED_AGENTS = {
    "institutional_knowledge",
    "evidence_verifier",
    "finance",
    "orchestrator",
}


@dataclass(frozen=True)
class RubricCriterion:
    id: str
    title: str
    match_criteria: str
    checks: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RubricCriterion":
        return cls(
            id=str(payload.get("id") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            match_criteria=str(payload.get("match_criteria") or "").strip(),
            checks=dict(payload.get("checks") or {}),
        ).validate()

    def validate(self) -> "RubricCriterion":
        if not self.id:
            raise ValueError("criterion id is required")
        if not self.title:
            raise ValueError(f"criterion title is required for {self.id}")
        if not self.match_criteria:
            raise ValueError(f"match_criteria is required for {self.id}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentVisibleTask:
    """Task envelope passed to specialist agents.

    Deliberately excludes rubric/criteria. The solver sees instructions and
    allowed tools; the evaluator keeps ground truth private.
    """

    task_id: str
    title: str
    agent: str
    case_type: str
    instructions: str
    input_artifacts: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    expected_output_artifacts: tuple[str, ...] = ("response.md",)
    authority_boundary: str = "PROPOSE_ONLY"
    tags: tuple[str, ...] = ()

    def validate(self) -> "AgentVisibleTask":
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.title:
            raise ValueError(f"title is required for {self.task_id}")
        if self.agent not in SUPPORTED_AGENTS:
            raise ValueError(f"unsupported agent: {self.agent}")
        if self.case_type not in SUPPORTED_CASE_TYPES:
            raise ValueError(f"unsupported case_type: {self.case_type}")
        if not self.instructions:
            raise ValueError(f"instructions are required for {self.task_id}")
        if self.authority_boundary not in SUPPORTED_AUTHORITY_BOUNDARIES:
            raise ValueError(f"unsupported authority_boundary: {self.authority_boundary}")
        if self.agent != "orchestrator" and not self.allowed_tools:
            raise ValueError(f"allowed_tools are required for {self.task_id}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SamchatTask:
    task_id: str
    title: str
    agent: str
    case_type: str
    instructions: str
    input_artifacts: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    expected_output_artifacts: tuple[str, ...] = ("response.md",)
    authority_boundary: str = "PROPOSE_ONLY"
    criteria: tuple[RubricCriterion, ...] = ()
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SamchatTask":
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            agent=str(payload.get("agent") or "").strip(),
            case_type=str(payload.get("case_type") or "").strip(),
            instructions=str(payload.get("instructions") or "").strip(),
            input_artifacts=tuple(
                str(item).strip() for item in payload.get("input_artifacts", []) if str(item).strip()
            ),
            allowed_tools=tuple(
                str(item).strip() for item in payload.get("allowed_tools", []) if str(item).strip()
            ),
            expected_output_artifacts=tuple(
                str(item).strip()
                for item in payload.get("expected_output_artifacts", ["response.md"])
                if str(item).strip()
            ),
            authority_boundary=str(payload.get("authority_boundary") or "PROPOSE_ONLY").strip(),
            criteria=tuple(RubricCriterion.from_dict(item) for item in payload.get("criteria", [])),
            tags=tuple(str(item).strip() for item in payload.get("tags", []) if str(item).strip()),
        ).validate()

    def validate(self) -> "SamchatTask":
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.title:
            raise ValueError(f"title is required for {self.task_id}")
        if self.agent not in SUPPORTED_AGENTS:
            raise ValueError(f"unsupported agent: {self.agent}")
        if self.case_type not in SUPPORTED_CASE_TYPES:
            raise ValueError(f"unsupported case_type: {self.case_type}")
        if not self.instructions:
            raise ValueError(f"instructions are required for {self.task_id}")
        if self.authority_boundary not in SUPPORTED_AUTHORITY_BOUNDARIES:
            raise ValueError(f"unsupported authority_boundary: {self.authority_boundary}")
        if not self.criteria:
            raise ValueError(f"at least one criterion is required for {self.task_id}")
        if self.agent != "orchestrator" and not self.allowed_tools:
            raise ValueError(f"allowed_tools are required for {self.task_id}")
        return self

    def to_agent_visible(self) -> AgentVisibleTask:
        return AgentVisibleTask(
            task_id=self.task_id,
            title=self.title,
            agent=self.agent,
            case_type=self.case_type,
            instructions=self.instructions,
            input_artifacts=self.input_artifacts,
            allowed_tools=self.allowed_tools,
            expected_output_artifacts=self.expected_output_artifacts,
            authority_boundary=self.authority_boundary,
            tags=self.tags,
        ).validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "agent": self.agent,
            "case_type": self.case_type,
            "instructions": self.instructions,
            "input_artifacts": list(self.input_artifacts),
            "allowed_tools": list(self.allowed_tools),
            "expected_output_artifacts": list(self.expected_output_artifacts),
            "authority_boundary": self.authority_boundary,
            "criteria": [item.to_dict() for item in self.criteria],
            "tags": list(self.tags),
        }


def load_task(path: str | Path) -> SamchatTask:
    return SamchatTask.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
