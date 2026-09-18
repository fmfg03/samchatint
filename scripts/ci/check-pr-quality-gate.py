#!/usr/bin/env python3
"""Fail when the mandatory pull-request gate becomes silently permissive."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
MANDATORY_JOBS = {
    "policy",
    "unit-tests",
    "integration-tests",
    "changed-code-coverage",
    "security-scan",
    "required-pr-gate",
}


def _run_blocks(job: dict) -> list[str]:
    return [str(step["run"]) for step in job.get("steps", []) if "run" in step]


def validate(document: dict, workflow_text: str) -> list[str]:
    jobs = document.get("jobs", {})
    missing = sorted(MANDATORY_JOBS - jobs.keys())
    errors: list[str] = []

    if missing:
        errors.append(f"missing mandatory jobs: {', '.join(missing)}")

    if "scripts/test_runner.py" in workflow_text:
        errors.append("workflow references the removed scripts/test_runner.py")
    if "|| true" in workflow_text:
        errors.append("workflow contains a command that forces a false success")

    for name in MANDATORY_JOBS & jobs.keys():
        job = jobs[name]
        if job.get("continue-on-error") is True:
            errors.append(f"{name} is marked continue-on-error")
        for step in job.get("steps", []):
            if step.get("continue-on-error") is True:
                errors.append(
                    f"{name}/{step.get('name', 'unnamed step')} is marked continue-on-error"
                )

    unit = "\n".join(_run_blocks(jobs.get("unit-tests", {})))
    integration = "\n".join(_run_blocks(jobs.get("integration-tests", {})))
    coverage = "\n".join(_run_blocks(jobs.get("changed-code-coverage", {})))
    if "python -m pytest tests/unit" not in unit:
        errors.append("unit-tests does not invoke pytest directly on tests/unit")
    if "check-pytest-baseline.py" not in unit:
        errors.append("unit-tests does not enforce the accepted-failure baseline")
    if "python -m pytest tests/integration" not in integration:
        errors.append(
            "integration-tests does not invoke pytest directly on tests/integration"
        )
    if "--fail-under=85" not in coverage:
        errors.append("changed-code coverage threshold is not 85%")
    if "@master" in workflow_text or "@main" in workflow_text:
        errors.append("workflow uses a mutable action reference")

    return errors


def main() -> int:
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(workflow_text)
    errors = validate(document, workflow_text)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("PR quality gate contract is strict and complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
