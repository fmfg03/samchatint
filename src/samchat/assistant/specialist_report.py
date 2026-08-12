"""Report builder for SamChat specialist-agent benchmark runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .specialist_business_diff import summarize_specialist_business_previews
from .specialist_benchmarks import (
    SpecialistBenchmark,
    WorkflowBenchmarkResult,
    build_seed_benchmarks,
    run_seed_benchmark,
)
from .specialist_harness import PASS


@dataclass(frozen=True)
class BenchmarkGap:
    task_id: str
    gap_type: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkReport:
    status: str
    total: int
    passed: int
    failed: int
    criteria_total: int
    criteria_passed: int
    criteria_failed: int
    side_effects_detected: int
    task_ids: Tuple[str, ...]
    case_types: Tuple[str, ...]
    finance_capabilities: Tuple[str, ...]
    business_preview_types: Tuple[str, ...]
    business_preview_missing_evidence_count: int
    gaps: Tuple[BenchmarkGap, ...]
    results: Tuple[WorkflowBenchmarkResult, ...]

    def to_dict(self, *, include_results: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "criteria_total": self.criteria_total,
            "criteria_passed": self.criteria_passed,
            "criteria_failed": self.criteria_failed,
            "side_effects_detected": self.side_effects_detected,
            "task_ids": list(self.task_ids),
            "case_types": list(self.case_types),
            "finance_capabilities": list(self.finance_capabilities),
            "business_preview_types": list(self.business_preview_types),
            "business_preview_missing_evidence_count": (
                self.business_preview_missing_evidence_count
            ),
            "gaps": [gap.to_dict() for gap in self.gaps],
        }
        if include_results:
            payload["results"] = [result.to_dict() for result in self.results]
        return payload


def _missing_evidence_gaps(result: WorkflowBenchmarkResult) -> List[BenchmarkGap]:
    gaps: List[BenchmarkGap] = []
    for item in result.workflow.knowledge.content.get("missing_evidence") or []:
        gaps.append(
            BenchmarkGap(
                task_id=result.task_id,
                gap_type="missing_evidence",
                detail=f"{item.get('case_id')}:{item.get('fact_key')}",
            )
        )
    return gaps


def _unsupported_claim_gaps(result: WorkflowBenchmarkResult) -> List[BenchmarkGap]:
    return [
        BenchmarkGap(
            task_id=result.task_id,
            gap_type="unsupported_claim",
            detail=str(item),
        )
        for item in result.workflow.verification.unsupported_claims
    ]


def _failed_criteria_gaps(result: WorkflowBenchmarkResult) -> List[BenchmarkGap]:
    return [
        BenchmarkGap(
            task_id=result.task_id,
            gap_type="failed_criterion",
            detail=f"{criterion.criterion_id}:{criterion.reason}",
        )
        for criterion in result.criteria
        if criterion.status != PASS
    ]


def build_benchmark_report(
    benchmarks: Iterable[SpecialistBenchmark] | None = None,
) -> BenchmarkReport:
    selected = tuple(benchmarks or build_seed_benchmarks())
    results = tuple(run_seed_benchmark(benchmark) for benchmark in selected)
    criteria = tuple(criterion for result in results for criterion in result.criteria)
    gaps: List[BenchmarkGap] = []
    for result in results:
        gaps.extend(_missing_evidence_gaps(result))
        gaps.extend(_unsupported_claim_gaps(result))
        gaps.extend(_failed_criteria_gaps(result))
    side_effects = sum(result.workflow.side_effects_detected for result in results)
    preview_summary = summarize_specialist_business_previews(
        result.business_preview for result in results
    )
    return BenchmarkReport(
        status=PASS if all(result.status == PASS for result in results) else "FAIL",
        total=len(results),
        passed=sum(1 for result in results if result.status == PASS),
        failed=sum(1 for result in results if result.status != PASS),
        criteria_total=len(criteria),
        criteria_passed=sum(1 for criterion in criteria if criterion.status == PASS),
        criteria_failed=sum(1 for criterion in criteria if criterion.status != PASS),
        side_effects_detected=side_effects,
        task_ids=tuple(result.task_id for result in results),
        case_types=tuple(
            sorted({case.case_type for benchmark in selected for case in benchmark.cases})
        ),
        finance_capabilities=tuple(
            sorted(
                {
                    str(result.workflow.finance.content.get("finance_capability"))
                    for result in results
                    if result.workflow.finance.content.get("finance_capability")
                }
            )
        ),
        business_preview_types=tuple(preview_summary["preview_types"]),
        business_preview_missing_evidence_count=int(
            preview_summary["missing_evidence_count"]
        ),
        gaps=tuple(gaps),
        results=results,
    )


def render_benchmark_report_markdown(report: BenchmarkReport) -> str:
    lines = [
        "# SamChat Specialist Benchmark Report",
        "",
        f"Status: {report.status}",
        f"Benchmarks: {report.passed}/{report.total} passed",
        f"Criteria: {report.criteria_passed}/{report.criteria_total} passed",
        f"Side effects detected: {report.side_effects_detected}",
        "",
        "## Case types",
        "",
    ]
    lines.extend(f"- {case_type}" for case_type in report.case_types)
    lines.extend(["", "## Finance capabilities", ""])
    lines.extend(f"- {capability}" for capability in report.finance_capabilities)
    lines.extend(["", "## Business preview types", ""])
    lines.extend(f"- {preview_type}" for preview_type in report.business_preview_types)
    lines.append(
        f"Missing evidence references in previews: {report.business_preview_missing_evidence_count}"
    )
    lines.extend(["", "## Results", ""])
    for result in report.results:
        lines.append(f"- {result.task_id}: {result.status}")
        for criterion in result.criteria:
            lines.append(
                f"  - {criterion.criterion_id}: {criterion.status} ({criterion.reason})"
            )
    lines.extend(["", "## Gaps", ""])
    if report.gaps:
        lines.extend(
            f"- {gap.task_id}: {gap.gap_type} - {gap.detail}"
            for gap in report.gaps
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Authority boundary", ""])
    lines.append(
        "All benchmark outputs are read-only/proposal-only; execution remains "
        "human_approval_required."
    )
    return "\n".join(lines) + "\n"


def compact_report_dict(report: BenchmarkReport) -> Dict[str, Any]:
    return report.to_dict(include_results=False)
