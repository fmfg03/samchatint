from __future__ import annotations

import importlib.util
import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "check-pr-quality-gate.py"


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("check_pr_quality_gate", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pr_quality_gate_contract_is_enforced() -> None:
    module = _load_gate_module()

    assert module.main() == 0


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda document: document["jobs"].pop("integration-tests"),
            "missing mandatory jobs: integration-tests",
        ),
        (
            lambda document: document["jobs"]["unit-tests"].update(
                {"continue-on-error": True}
            ),
            "unit-tests is marked continue-on-error",
        ),
        (
            lambda document: document["jobs"]["security-scan"]["steps"][-1].update(
                {"run": "bandit -r src --skip B324"}
            ),
            "security-scan does not enforce the Bandit finding baseline",
        ),
        (
            lambda document: document["jobs"]["security-scan"]["steps"][-1].update(
                {"run": "bandit -r src"}
            ),
            "security-scan does not create the Bandit report directory",
        ),
    ],
)
def test_pr_quality_gate_rejects_permissive_mutations(
    mutation, expected_error: str
) -> None:
    module = _load_gate_module()
    workflow_text = module.WORKFLOW.read_text(encoding="utf-8")
    document = deepcopy(yaml.safe_load(workflow_text))
    mutation(document)

    assert expected_error in module.validate(document, workflow_text)


def test_pr_quality_gate_rejects_missing_runner_reference() -> None:
    module = _load_gate_module()
    workflow_text = module.WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(workflow_text)

    errors = module.validate(document, workflow_text + "\nscripts/test_runner.py\n")

    assert "workflow references the removed scripts/test_runner.py" in errors


def test_pr_quality_gate_rejects_a_rule_wide_bandit_skip() -> None:
    module = _load_gate_module()
    workflow_text = module.WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(workflow_text)

    errors = module.validate(document, workflow_text + "\n--skip B324\n")

    assert "workflow skips Bandit B324 instead of enforcing its baseline" in errors


@pytest.mark.parametrize(
    "job,label", [("unit-tests", "unit"), ("integration-tests", "integration")]
)
def test_private_plugin_tests_cannot_be_excluded_without_the_isolated_runner(
    job, label
):
    module = _load_gate_module()
    workflow_text = module.WORKFLOW.read_text(encoding="utf-8")
    document = deepcopy(yaml.safe_load(workflow_text))
    steps = document["jobs"][job]["steps"]
    document["jobs"][job]["steps"] = [
        step
        for step in steps
        if "scripts/private_plugin/run_" not in step.get("run", "")
    ]
    assert f"{label} tests omit the isolated private-plugin runner" in module.validate(
        document, workflow_text
    )


@pytest.mark.parametrize(
    "job,label", [("unit-tests", "unit"), ("integration-tests", "integration")]
)
def test_private_plugin_coverage_must_be_combined(job, label):
    module = _load_gate_module()
    workflow_text = module.WORKFLOW.read_text(encoding="utf-8")
    document = deepcopy(yaml.safe_load(workflow_text))
    for step in document["jobs"][job]["steps"]:
        if "coverage combine" in step.get("run", ""):
            step["run"] = "python -m coverage combine .coverage.repository"
    assert f"{label} coverage omits isolated private-plugin results" in module.validate(
        document, workflow_text
    )


@pytest.mark.parametrize("job", ["unit-tests", "integration-tests"])
def test_private_coverage_survives_pytest_cov_parallel_cleanup(tmp_path, job):
    import coverage

    module = _load_gate_module()
    document = yaml.safe_load(module.WORKFLOW.read_text(encoding="utf-8"))
    private_step = next(
        step["run"]
        for step in document["jobs"][job]["steps"]
        if "scripts/private_plugin/run_" in step.get("run", "")
    )
    relative = re.search(r"COVERAGE_FILE=([^\s]+)", private_step).group(1)
    private = tmp_path / relative
    private.parent.mkdir(parents=True, exist_ok=True)
    data = coverage.CoverageData(basename=str(private))
    data.add_lines({__file__: {1}})
    data.write()
    # pytest-cov starts with data_suffix=True and erases parallel siblings.
    coverage.Coverage(data_file=str(tmp_path / ".coverage"), data_suffix=True).erase()
    restored = coverage.CoverageData(basename=str(private))
    restored.read()
    assert private.is_file()
    assert restored.lines(__file__) == [1]
