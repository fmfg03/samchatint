#!/usr/bin/env python3
"""Deny unadmitted operational scripts that can mutate registration authority."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable


OPERATIONAL_DIRS = frozenset({"bin", "ops", "scripts", "tools"})
ADMITTED_ENTRYPOINTS = frozenset(
    {
        "copa_telmex_dashboard.py",
        "run_copa_telmex.py",
        "scripts/run_registration_intake_bot.py",
    }
)
METHOD_MUTATION = re.compile(
    r"\.\s*(?:create_team|create_player|update_team|update_player|"
    r"delete_team|delete_player)\s*\("
)
RAW_SQL_MUTATION = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from|truncate(?:\s+table)?)\s+"
    r"[`\"']?(?:public\.)?copa_telmex_(?:teams|players)\b",
    re.IGNORECASE | re.DOTALL,
)
SQLALCHEMY_MUTATION = re.compile(
    r"\b(?:insert|update|delete)\s*\(\s*(?:Team|Player)\b"
)
MODEL_IMPORT = re.compile(
    r"(?:from\s+devnous\.copa_telmex\.models\s+import[\s\S]*\b(?:Team|Player)\b|"
    r"import\s+devnous\.copa_telmex\.models)"
)
SESSION_MUTATION = re.compile(r"\bsession\.(?:add|add_all|delete)\s*\(")
REGS08_SOURCE = Path("src/devnous/tournaments/core/operations_module.py")


def is_operational_path(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.suffix != ".py" or relative_path in ADMITTED_ENTRYPOINTS:
        return False
    if len(path.parts) == 1:
        return True
    return path.parts[0] in OPERATIONAL_DIRS


def mutation_reasons(source: str) -> list[str]:
    reasons: list[str] = []
    if METHOD_MUTATION.search(source):
        reasons.append("REGISTRATION_PRIMITIVE_MUTATION")
    if RAW_SQL_MUTATION.search(source):
        reasons.append("RAW_REGISTRATION_TABLE_MUTATION")
    if SQLALCHEMY_MUTATION.search(source):
        reasons.append("DIRECT_SQLALCHEMY_MODEL_MUTATION")
    if MODEL_IMPORT.search(source) and SESSION_MUTATION.search(source):
        reasons.append("DIRECT_ORM_SESSION_MUTATION")
    return sorted(set(reasons))


def _method_calls(node: ast.AST) -> set[str]:
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def regs08_retirement_reasons(root: Path) -> list[str]:
    """Verify Telegram can only stage a governed review, never finalize rows."""
    path = root / REGS08_SOURCE
    if not path.is_file():
        return ["REGS08_CANONICAL_SOURCE_MISSING"]
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ["REGS08_CANONICAL_SOURCE_UNREADABLE"]

    operations = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "OperationsModule"
        ),
        None,
    )
    if operations is None:
        return ["REGS08_OPERATIONS_MODULE_MISSING"]

    methods = {
        node.name: node
        for node in operations.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reasons: list[str] = []
    all_calls = _method_calls(operations)
    if (
        "_save_registration_form_to_database" in methods
        or "_save_registration_form_to_database" in all_calls
    ):
        reasons.append("REGS08_DIRECT_FINALIZER_PRESENT")

    staging = methods.get("_stage_pending_registration_review")
    if staging is None:
        reasons.append("REGS08_GOVERNED_STAGING_MISSING")
    elif "_create_web_review_session_from_pending" not in _method_calls(staging):
        reasons.append("REGS08_STAGING_NOT_BOUND_TO_REVIEW_DRAFT")

    if "stage_ocr:" not in source:
        reasons.append("REGS08_PRECAPTURE_CALLBACK_MISSING")
    if re.search(r'"callback_data"\s*:\s*f?"save_ocr:', source):
        reasons.append("REGS08_DIRECT_SAVE_CALLBACK_GENERATED")

    return sorted(set(reasons))


def _git_paths(root: Path) -> Iterable[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root}",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    for raw_path in result.stdout.split(b"\0"):
        if raw_path:
            yield raw_path.decode("utf-8", errors="surrogateescape")


def assess(root: Path) -> dict[str, object]:
    violations = []
    scanned = 0
    for relative_path in sorted(_git_paths(root)):
        if not is_operational_path(relative_path):
            continue
        path = root / relative_path
        if not path.is_file():
            continue
        scanned += 1
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = path.read_text(encoding="utf-8", errors="replace")
        reasons = mutation_reasons(source)
        if reasons:
            violations.append({"path": relative_path, "reason_codes": reasons})

    regs08_reasons = regs08_retirement_reasons(root)
    if regs08_reasons:
        violations.append(
            {"path": str(REGS08_SOURCE), "reason_codes": regs08_reasons}
        )

    return {
        "schema_version": "samchat.registration_operational_surface.v1",
        "valid": not violations,
        "scanned_operational_python_files": scanned,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--write-report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    result = assess(root)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.write_report:
        args.write_report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
