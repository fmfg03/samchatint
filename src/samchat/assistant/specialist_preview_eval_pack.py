"""Preview-level eval pack for SamChat specialist workflows.

The seed benchmark harness evaluates the workflow. This module evaluates the
user-facing preview contract that sits on top of it: renderer, authority
boundary, evidence quality gate, and missing-evidence surfacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .specialist_benchmarks import (
    SpecialistBenchmark,
    build_seed_benchmarks,
    run_seed_benchmark,
)
from .specialist_evidence_quality import build_specialist_evidence_quality_gate
from .specialist_harness import FAIL, PASS, CriterionResult
from .specialist_preview_renderer import (
    SECTION_AUTHORITY,
    render_specialist_business_preview,
)


@dataclass(frozen=True)
class SpecialistPreviewEvalResult:
    task_id: str
    status: str
    quality_status: str
    missing_evidence_count: int
    criteria: Tuple[CriterionResult, ...]
    evidence_quality_gate: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "quality_status": self.quality_status,
            "missing_evidence_count": self.missing_evidence_count,
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "evidence_quality_gate": dict(self.evidence_quality_gate),
        }


@dataclass(frozen=True)
class SpecialistPreviewEvalPackResult:
    status: str
    total: int
    passed: int
    failed: int
    criteria_total: int
    criteria_passed: int
    criteria_failed: int
    quality_status_counts: Mapping[str, int]
    missing_evidence_count: int
    task_ids: Tuple[str, ...]
    results: Tuple[SpecialistPreviewEvalResult, ...]

    def to_dict(self, *, include_results: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "criteria_total": self.criteria_total,
            "criteria_passed": self.criteria_passed,
            "criteria_failed": self.criteria_failed,
            "quality_status_counts": dict(self.quality_status_counts),
            "missing_evidence_count": self.missing_evidence_count,
            "task_ids": list(self.task_ids),
        }
        if include_results:
            payload["results"] = [result.to_dict() for result in self.results]
        return payload


def _criterion(criterion_id: str, ok: bool, reason: str) -> CriterionResult:
    return CriterionResult(
        criterion_id=criterion_id,
        status=PASS if ok else FAIL,
        reason=reason,
    )


def _authority_section(rendered: Any) -> Mapping[str, Any] | None:
    for section in rendered.sections:
        if section.section_id == SECTION_AUTHORITY:
            return section.to_dict()
    return None


def _missing_from_preview(preview: Mapping[str, Any]) -> set[str]:
    return {str(item) for item in (preview.get("missing_evidence") or [])}


def _missing_from_gate(gate: Mapping[str, Any]) -> set[str]:
    return {str(item) for item in (gate.get("missing_evidence") or [])}


def _unbound_change_count(preview: Mapping[str, Any]) -> int:
    count = 0
    for item in preview.get("proposed_changes") or []:
        if isinstance(item, Mapping) and not str(item.get("evidence_id") or "").strip():
            count += 1
    return count


def evaluate_specialist_preview_result(
    benchmark: SpecialistBenchmark,
) -> SpecialistPreviewEvalResult:
    workflow_result = run_seed_benchmark(benchmark)
    rendered = render_specialist_business_preview(workflow_result.business_preview)
    preview_payload = workflow_result.business_preview.to_dict()
    evidence_quality_gate = build_specialist_evidence_quality_gate(
        business_preview=preview_payload,
        live_context={
            "matched": True,
            "status": "seed_benchmark_context",
            "unresolved": {},
        },
        diagnostics={"missing": []},
        memory_context={"snippets": []},
        continuity_context={"matched": False},
    )
    authority = _authority_section(rendered)
    preview_missing = _missing_from_preview(preview_payload)
    gate_missing = _missing_from_gate(evidence_quality_gate)
    unbound_count = _unbound_change_count(preview_payload)
    criteria = (
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-001",
            workflow_result.status == PASS,
            f"workflow_status:{workflow_result.status}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-002",
            rendered.primary_action_enabled is False,
            f"primary_action_enabled:{rendered.primary_action_enabled}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-003",
            rendered.execution_status == "not_executed",
            f"execution_status:{rendered.execution_status}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-004",
            rendered.audit_language == "preview_only",
            f"audit_language:{rendered.audit_language}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-005",
            bool(authority) and authority.get("status") == "blocked",
            f"authority_status:{authority.get('status') if authority else 'missing'}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-006",
            evidence_quality_gate.get("safe_to_execute") is False,
            f"safe_to_execute:{evidence_quality_gate.get('safe_to_execute')}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-007",
            evidence_quality_gate.get("primary_action_enabled") is False,
            f"gate_primary_action_enabled:{evidence_quality_gate.get('primary_action_enabled')}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-008",
            preview_missing.issubset(gate_missing),
            f"preview_missing:{sorted(preview_missing)} gate_missing:{sorted(gate_missing)}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-009",
            not unbound_count or evidence_quality_gate.get("quality_status") != "supported",
            f"unbound_changes:{unbound_count} quality:{evidence_quality_gate.get('quality_status')}",
        ),
        _criterion(
            f"{benchmark.task.task_id}-PREVIEW-010",
            workflow_result.workflow.side_effects_detected == 0,
            f"side_effects:{workflow_result.workflow.side_effects_detected}",
        ),
    )
    status = PASS if all(item.status == PASS for item in criteria) else FAIL
    return SpecialistPreviewEvalResult(
        task_id=benchmark.task.task_id,
        status=status,
        quality_status=str(evidence_quality_gate.get("quality_status") or "unknown"),
        missing_evidence_count=int(evidence_quality_gate.get("missing_evidence_count") or 0),
        criteria=criteria,
        evidence_quality_gate=evidence_quality_gate,
    )


def run_specialist_preview_eval_pack(
    benchmarks: Iterable[SpecialistBenchmark] | None = None,
) -> SpecialistPreviewEvalPackResult:
    selected = tuple(benchmarks or build_seed_benchmarks())
    results = tuple(evaluate_specialist_preview_result(benchmark) for benchmark in selected)
    criteria = tuple(criterion for result in results for criterion in result.criteria)
    quality_counts: Dict[str, int] = {}
    for result in results:
        quality_counts[result.quality_status] = quality_counts.get(result.quality_status, 0) + 1
    return SpecialistPreviewEvalPackResult(
        status=PASS if all(result.status == PASS for result in results) else FAIL,
        total=len(results),
        passed=sum(1 for result in results if result.status == PASS),
        failed=sum(1 for result in results if result.status != PASS),
        criteria_total=len(criteria),
        criteria_passed=sum(1 for criterion in criteria if criterion.status == PASS),
        criteria_failed=sum(1 for criterion in criteria if criterion.status != PASS),
        quality_status_counts=quality_counts,
        missing_evidence_count=sum(result.missing_evidence_count for result in results),
        task_ids=tuple(result.task_id for result in results),
        results=results,
    )


def render_specialist_preview_eval_pack_markdown(
    result: SpecialistPreviewEvalPackResult,
) -> str:
    lines = [
        "# SamChat Specialist Preview Eval Pack",
        "",
        f"Status: {result.status}",
        f"Previews: {result.passed}/{result.total} passed",
        f"Criteria: {result.criteria_passed}/{result.criteria_total} passed",
        f"Missing evidence references: {result.missing_evidence_count}",
        "",
        "## Quality status counts",
        "",
    ]
    for status, count in sorted(result.quality_status_counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Results", ""])
    for item in result.results:
        lines.append(
            f"- {item.task_id}: {item.status} "
            f"({item.quality_status}; missing={item.missing_evidence_count})"
        )
    lines.extend(["", "## Authority boundary", ""])
    lines.append(
        "All evaluated previews remain read-only, preview_only, and blocked from execution."
    )
    return "\n".join(lines) + "\n"


def compact_preview_eval_pack_dict(
    result: SpecialistPreviewEvalPackResult,
) -> Dict[str, Any]:
    return result.to_dict(include_results=False)
