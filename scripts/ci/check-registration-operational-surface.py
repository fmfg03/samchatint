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
REGS09_SOURCE = REGS08_SOURCE
REGS10_SOURCE = REGS08_SOURCE
REGS11_SOURCE = REGS08_SOURCE


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


def regs09_retirement_reasons(root: Path) -> list[str]:
    """Verify back pages can only enter the governed REG-S04 append route."""
    path = root / REGS09_SOURCE
    if not path.is_file():
        return ["REGS09_CANONICAL_SOURCE_MISSING"]
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ["REGS09_CANONICAL_SOURCE_UNREADABLE"]

    operations = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "OperationsModule"
        ),
        None,
    )
    if operations is None:
        return ["REGS09_OPERATIONS_MODULE_MISSING"]

    methods = {
        node.name: node
        for node in operations.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reasons: list[str] = []
    all_calls = _method_calls(operations)
    if (
        "_append_players_to_team" in methods
        or "_append_players_to_team" in all_calls
    ):
        reasons.append("REGS09_DIRECT_BACKPAGE_FINALIZER_PRESENT")

    back_page = methods.get("_process_back_photo")
    if back_page is None:
        reasons.append("REGS09_BACKPAGE_HANDLER_MISSING")
    elif "_append_back_photo_to_review_session" not in _method_calls(back_page):
        reasons.append("REGS09_REGS04_APPEND_ROUTE_MISSING")

    if "REGS09_REVIEW_SESSION_REQUIRED" not in source:
        reasons.append("REGS09_REVIEW_SESSION_FAIL_CLOSED_MISSING")

    return sorted(set(reasons))


def regs10_retirement_reasons(root: Path) -> list[str]:
    """Verify the legacy single-player finalizer is retired fail-closed."""
    path = root / REGS10_SOURCE
    if not path.is_file():
        return ["REGS10_CANONICAL_SOURCE_MISSING"]
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ["REGS10_CANONICAL_SOURCE_UNREADABLE"]

    operations = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "OperationsModule"
        ),
        None,
    )
    if operations is None:
        return ["REGS10_OPERATIONS_MODULE_MISSING"]

    methods = {
        node.name: node
        for node in operations.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    all_calls = _method_calls(operations)
    reasons: list[str] = []
    finalizers = {"_save_to_database", "_send_final_confirmation"}
    if finalizers.intersection(methods) or finalizers.intersection(all_calls):
        reasons.append("REGS10_LEGACY_FINALIZER_PRESENT")

    legacy_handlers = {
        "_legacy_single_player_ocr",
        "_call_claude_vision",
        "_request_human_verification",
    }
    if legacy_handlers.intersection(methods) or legacy_handlers.intersection(all_calls):
        reasons.append("REGS10_LEGACY_OCR_HANDLER_PRESENT")

    process = methods.get("process_ocr_registration")
    process_source = ast.get_source_segment(source, process) if process else None
    if process is None:
        reasons.append("REGS10_PROCESS_HANDLER_MISSING")
    elif (
        "claude_vision" not in (process_source or "")
        or "REGS10_LEGACY_SINGLE_PLAYER_RETIRED" not in (process_source or "")
    ):
        reasons.append("REGS10_PROVIDER_FAIL_CLOSED_MISSING")

    callback = methods.get("handle_callback_query")
    callback_source = ast.get_source_segment(source, callback) if callback else None
    callback_markers = (
        "confirm_",
        "use_detected_",
        "write_manually",
        "REGS10_LEGACY_SINGLE_PLAYER_RETIRED",
    )
    if callback is None:
        reasons.append("REGS10_CALLBACK_HANDLER_MISSING")
    elif any(marker not in (callback_source or "") for marker in callback_markers):
        reasons.append("REGS10_LEGACY_CALLBACK_FAIL_CLOSED_MISSING")

    return sorted(set(reasons))


def regs11_retirement_reasons(root: Path) -> list[str]:
    """Verify conversational manual-player creation is retired fail-closed."""
    path = root / REGS11_SOURCE
    if not path.is_file():
        return ["REGS11_CANONICAL_SOURCE_MISSING"]
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ["REGS11_CANONICAL_SOURCE_UNREADABLE"]

    operations = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "OperationsModule"
        ),
        None,
    )
    if operations is None:
        return ["REGS11_OPERATIONS_MODULE_MISSING"]

    methods = {
        node.name: node
        for node in operations.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    all_calls = _method_calls(operations)
    reasons: list[str] = []
    retired = {
        "_create_manual_player",
        "_continue_player_onboarding",
        "_parse_manual_player_payload",
    }
    if retired.intersection(methods) or retired.intersection(all_calls):
        reasons.append("REGS11_MANUAL_PLAYER_WRITER_PRESENT")
    if "pending_player_onboarding" in source:
        reasons.append("REGS11_PENDING_ONBOARDING_STATE_PRESENT")

    handler = methods.get("_handle_conversational_actions")
    handler_source = ast.get_source_segment(source, handler) if handler else None
    required_markers = (
        "dar de alta",
        "agregar jugador",
        "registrar jugador",
        "REGS11_MANUAL_PLAYER_CREATION_RETIRED",
    )
    if handler is None:
        reasons.append("REGS11_CONVERSATIONAL_INTERCEPTOR_MISSING")
    elif any(marker not in (handler_source or "") for marker in required_markers):
        reasons.append("REGS11_MANUAL_PLAYER_FAIL_CLOSED_MISSING")
    elif {"create_player", "append_players_to_team_v2"}.intersection(
        _method_calls(handler)
    ):
        reasons.append("REGS11_INTERCEPTOR_MUTATION_PRESENT")

    handle = methods.get("handle")
    if handle is None or "_handle_conversational_actions" not in _method_calls(handle):
        reasons.append("REGS11_INTERCEPTOR_NOT_ROUTED")

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

    regs09_reasons = regs09_retirement_reasons(root)
    if regs09_reasons:
        violations.append(
            {"path": str(REGS09_SOURCE), "reason_codes": regs09_reasons}
        )

    regs10_reasons = regs10_retirement_reasons(root)
    if regs10_reasons:
        violations.append(
            {"path": str(REGS10_SOURCE), "reason_codes": regs10_reasons}
        )

    regs11_reasons = regs11_retirement_reasons(root)
    if regs11_reasons:
        violations.append(
            {"path": str(REGS11_SOURCE), "reason_codes": regs11_reasons}
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
