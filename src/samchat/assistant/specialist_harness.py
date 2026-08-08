"""Deterministic harness for SamChat specialist-agent tasks.

This module intentionally avoids production writes. It creates a read-only source
workspace plus a writable output directory, runs one deterministic specialist, and
scores criteria with explicit all-pass semantics.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .operational_case import OperationalCase, load_operational_cases
from .specialist_agents import AgentArtifact, AgentRunResult, default_agents
from .specialist_task import SamchatTask, load_task


@dataclass(frozen=True)
class CriterionScore:
    criterion_id: str
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class HarnessRunReport:
    task_id: str
    agent: str
    all_pass: bool
    scores: tuple[CriterionScore, ...]
    result: AgentRunResult
    workspace_dir: str


def create_task_workspace(
    task: SamchatTask,
    cases: Sequence[OperationalCase],
    base_dir: str | Path | None = None,
) -> Path:
    """Create a Harvey-like task workspace with read-only source artifacts."""

    root = Path(base_dir) if base_dir else Path(tempfile.mkdtemp(prefix=f"samchat-{task.task_id}-"))
    source_dir = root / "source"
    output_dir = root / "output"
    source_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    task_path = source_dir / "samchat_task.json"
    cases_path = source_dir / "operational_cases.json"
    task_path.write_text(json.dumps(task.to_agent_visible().to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    cases_path.write_text(
        json.dumps([case.to_dict() for case in cases], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for path in (task_path, cases_path):
        path.chmod(0o444)
    output_dir.chmod(0o700)
    return root


def run_task(
    task: SamchatTask,
    cases: Sequence[OperationalCase],
    workspace_dir: str | Path | None = None,
) -> HarnessRunReport:
    """Run one specialist task and return an all-pass report."""

    root = create_task_workspace(task, cases, workspace_dir)

    if task.agent == "orchestrator":
        from .specialist_orchestrator import route_cross_agent_task

        plan = route_cross_agent_task(task, cases)
        case_ids: set[str] = set()
        evidence_refs: set[str] = set()
        for child in plan.child_results:
            for artifact in child.artifacts:
                case_ids.update(_case_ids_from_artifact(artifact))
                evidence_refs.update(artifact.evidence_refs)
        content = plan.to_dict()
        content["case_ids"] = sorted(case_ids)
        artifact = AgentArtifact(
            artifact_type="orchestrator_plan",
            title=f"Orchestrator plan for {task.task_id}",
            content=content,
            evidence_refs=tuple(sorted(evidence_refs)),
            authority_boundary="PROPOSE_ONLY",
        )
        result = AgentRunResult(
            agent_id="orchestrator",
            task_id=task.task_id,
            artifacts=(artifact,),
            trace=({"tool": "route_cross_agent_task", "route": list(plan.route)},),
            side_effects_detected=plan.side_effects_detected,
        )
    else:
        agents = default_agents()
        if task.agent not in agents:
            raise ValueError(f"Unsupported specialist agent: {task.agent}")
        agent = agents[task.agent]
        unauthorized_tools = set(task.allowed_tools) - set(agent.contract.tools_allowed)
        if unauthorized_tools:
            tools = ", ".join(sorted(unauthorized_tools))
            raise PermissionError(f"Task requested unauthorized tools for {task.agent}: {tools}")
        result = agent.run(task.to_agent_visible(), tuple(cases))

    scores = tuple(score_criterion(criterion, task, result) for criterion in task.criteria)
    report = HarnessRunReport(
        task_id=task.task_id,
        agent=task.agent,
        all_pass=all(score.passed for score in scores),
        scores=scores,
        result=result,
        workspace_dir=str(root),
    )
    _write_json(root / "output" / "result.json", result.to_dict())
    _write_json(root / "output" / "scores.json", report_to_dict(report))
    return report


def score_criterion(
    criterion: Any,
    task: SamchatTask,
    result: AgentRunResult,
) -> CriterionScore:
    checks: Mapping[str, Any] = criterion.checks
    failures: list[str] = []
    case_ids = _artifact_case_ids(result.artifacts)
    evidence_refs = _artifact_evidence_refs(result.artifacts)
    artifact_text = json.dumps([artifact.to_dict() for artifact in result.artifacts], ensure_ascii=False).lower()

    required_case_ids = set(checks.get("requires_case_ids", []))
    missing_cases = required_case_ids - case_ids
    if missing_cases:
        failures.append(f"missing required cases: {', '.join(sorted(missing_cases))}")

    forbidden_case_ids = set(checks.get("forbids_case_ids", []))
    present_forbidden = forbidden_case_ids & case_ids
    if present_forbidden:
        failures.append(f"included forbidden cases: {', '.join(sorted(present_forbidden))}")

    if "requires_count" in checks:
        expected_count = int(checks["requires_count"])
        actual_count = len(case_ids)
        if actual_count != expected_count:
            failures.append(f"expected {expected_count} cases, got {actual_count}")

    required_evidence = set(checks.get("requires_evidence_refs", []))
    missing_evidence = required_evidence - evidence_refs
    if missing_evidence:
        failures.append(f"missing required evidence refs: {', '.join(sorted(missing_evidence))}")

    for required_text in checks.get("requires_text", []):
        if str(required_text).lower() not in artifact_text:
            failures.append(f"missing required text: {required_text}")

    if checks.get("forbid_unsupported_facts"):
        for artifact in result.artifacts:
            unsupported = artifact.content.get("unsupported_facts", [])
            if unsupported:
                failures.append(f"unsupported facts present: {unsupported}")

    for fact in checks.get("requires_unsupported_facts", []):
        if str(fact).lower() not in artifact_text:
            failures.append(f"missing unsupported fact marker: {fact}")

    if "authority_boundary" in checks:
        expected_boundary = str(checks["authority_boundary"])
        if result.authority_boundary != expected_boundary:
            failures.append(f"expected authority {expected_boundary}, got {result.authority_boundary}")

    if checks.get("no_side_effects") and result.side_effects_detected:
        failures.append("side effects detected")

    return CriterionScore(
        criterion_id=criterion.id,
        passed=not failures,
        failures=tuple(failures),
    )


def run_regression(
    task_paths: Iterable[str | Path],
    cases_path: str | Path,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    cases = load_operational_cases(cases_path)
    root = Path(workspace_root) if workspace_root else Path(tempfile.mkdtemp(prefix="samchat-rqf052-"))
    root.mkdir(parents=True, exist_ok=True)
    reports: list[HarnessRunReport] = []
    for task_path in sorted(Path(path) for path in task_paths):
        task = load_task(task_path)
        reports.append(run_task(task, cases, root / task.task_id))

    payload = {
        "n_tasks": len(reports),
        "n_passed": sum(1 for report in reports if report.all_pass),
        "all_pass": all(report.all_pass for report in reports),
        "reports": [report_to_dict(report) for report in reports],
    }
    _write_json(root / "regression_report.json", payload)
    return payload


def report_to_dict(report: HarnessRunReport) -> dict[str, Any]:
    return {
        "task_id": report.task_id,
        "agent": report.agent,
        "all_pass": report.all_pass,
        "scores": [asdict(score) for score in report.scores],
        "result": report.result.to_dict(),
        "workspace_dir": report.workspace_dir,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _artifact_case_ids(artifacts: Sequence[AgentArtifact]) -> set[str]:
    case_ids: set[str] = set()
    for artifact in artifacts:
        case_ids.update(_case_ids_from_artifact(artifact))
    return case_ids


def _case_ids_from_artifact(artifact: AgentArtifact) -> set[str]:
    content = artifact.content
    case_ids: set[str] = set()
    for case_id in content.get("case_ids", []) or []:
        case_ids.add(str(case_id))
    for proposal in content.get("proposals", []) or []:
        if proposal.get("case_id"):
            case_ids.add(str(proposal["case_id"]))
    for selected in content.get("selected_cases", []) or []:
        if selected.get("case_id"):
            case_ids.add(str(selected["case_id"]))
    return case_ids


def _artifact_evidence_refs(artifacts: Sequence[AgentArtifact]) -> set[str]:
    refs: set[str] = set()
    for artifact in artifacts:
        refs.update(artifact.evidence_refs)
    return refs
