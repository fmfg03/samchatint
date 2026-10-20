"""Dependency-free structural checks and executable-line coverage evidence.

This supplements, and does not impersonate, flake8/mypy/Black or repository CI.
No package installation or application bootstrap is performed.
"""

from __future__ import annotations

import ast
import json
import runpy
import sys
import trace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "samchat" / "readonly_gateway"
issues: list[str] = []
for path in sorted(PACKAGE.glob("*.py")):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    compile(tree, str(path), "exec")
    for number, line in enumerate(source.splitlines(), 1):
        if line != line.rstrip() or "\t" in line:
            issues.append(f"{path.name}:{number}: whitespace")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is None:
                issues.append(f"{path.name}:{node.lineno}: missing return annotation")
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                if arg.arg not in {"self", "cls"} and arg.annotation is None:
                    issues.append(f"{path.name}:{node.lineno}: unannotated {arg.arg}")
        if isinstance(node, ast.ImportFrom) and any(n.name == "*" for n in node.names):
            issues.append(f"{path.name}:{node.lineno}: wildcard import")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec"}
        ):
            issues.append(f"{path.name}:{node.lineno}: executable input")

# Verify the base's state guard using AST rather than importing a live workflow.
workflow = (
    ROOT / "src" / "devnous" / "gastos" / "services" / "documento_workflow_service.py"
)
base = ast.parse(workflow.read_text(encoding="utf-8"))
guard_found = False
approve_branches = [
    node
    for node in ast.walk(base)
    if isinstance(node, ast.If)
    and isinstance(node.test, ast.Compare)
    and isinstance(node.test.left, ast.Name)
    and node.test.left.id == "normalized_action"
    and len(node.test.ops) == 1
    and isinstance(node.test.ops[0], ast.Eq)
    and any(
        isinstance(value, ast.Constant) and value.value == "approve"
        for value in node.test.comparators
    )
]
for node in (child for branch in approve_branches for child in branch.body):
    if not isinstance(node, ast.If):
        continue
    test = node.test
    if (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Attribute)
        and test.left.attr == "estado"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "enviado"
        and isinstance(test.ops[0], ast.NotEq)
        and "invalid_estado" in ast.unparse(node)
    ):
        guard_found = True
if not guard_found:
    issues.append("base state guard differs; diagnostic needs owner review")

sys.path.insert(0, str(ROOT / "src"))
tracer = trace.Trace(count=True, trace=False, ignoredirs=[sys.prefix])
try:
    tracer.runfunc(
        runpy.run_path,
        str(ROOT / "scripts" / "readonly_gateway" / "run_offline_tests.py"),
        run_name="__main__",
    )
except SystemExit as result:
    if result.code:
        issues.append("offline tests failed")
counts = tracer.results().counts
details: dict[str, dict[str, int]] = {}
for path in PACKAGE.glob("*.py"):
    executable = trace._find_executable_linenos(str(path))
    covered = sum(bool(counts.get((str(path), line), 0)) for line in executable)
    details[path.name] = {"executable_lines": len(executable), "covered_lines": covered}
total = sum(d["executable_lines"] for d in details.values())
covered = sum(d["covered_lines"] for d in details.values())
if not total or covered / total < 0.85:
    issues.append("local executable-line coverage below 85 percent")
report = {
    "structural_checks_passed": not issues,
    "issues": issues,
    "state_guard_contract_verified": guard_found,
    "coverage_method": "stdlib trace executable lines, not pytest-cov statements or branches",
    "coverage_percent": round(100 * covered / total, 2) if total else 0,
    "files": details,
    "external_quality_gates": "not executed by this script; see the exact receipt for separate formatter/type/lint/security/CI results; trace is not their substitute",
}
print(json.dumps(report, indent=2))
raise SystemExit(1 if issues else 0)
