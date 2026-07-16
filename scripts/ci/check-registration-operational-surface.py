#!/usr/bin/env python3
"""Deny unadmitted operational scripts that can mutate registration authority."""

from __future__ import annotations

import argparse
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
