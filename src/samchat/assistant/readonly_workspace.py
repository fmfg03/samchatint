"""Fail-closed, text-only workspace reads for the assistant canary."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_ROOT = Path("/srv/samchat/workspaces/assistant-readonly")
MAX_FILE_BYTES = 131_072
MAX_LIST_RESULTS = 200
MAX_SEARCH_RESULTS = 100
MAX_TASK_FILE_BYTES = 65_536
TEXT_EXTENSIONS = {
    ".csv",
    ".json",
    ".md",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SENSITIVE_NAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "secrets",
    "secrets.json",
}
SENSITIVE_NAME_FRAGMENTS = ("credential", "password", "private", "secret", "token")


def readonly_workspace_enabled() -> bool:
    return (
        os.getenv("ASSISTANT_READONLY_WORKSPACE_ENABLED", "").strip().lower()
        in TRUE_VALUES
    )


def readonly_workspace_allowed(employee_id: Any) -> bool:
    if not readonly_workspace_enabled():
        return False
    subject = str(employee_id or "").strip().casefold()
    cohort = {
        value.strip().casefold()
        for value in os.getenv("ASSISTANT_READONLY_WORKSPACE_EMPLOYEE_IDS", "").split(
            ","
        )
        if value.strip()
    }
    return bool(subject and cohort and subject in cohort)


def workspace_task_mutations_enabled() -> bool:
    return (
        os.getenv("ASSISTANT_TASK_WORKSPACE_MUTATIONS_ENABLED", "").strip().lower()
        in TRUE_VALUES
    )


def workspace_task_mutation_allowed(employee_id: Any) -> bool:
    if not workspace_task_mutations_enabled():
        return False
    subject = str(employee_id or "").strip().casefold()
    cohort = {
        value.strip().casefold()
        for value in os.getenv("ASSISTANT_TASK_WORKSPACE_EMPLOYEE_IDS", "").split(",")
        if value.strip()
    }
    return bool(subject and cohort and subject in cohort)


def workspace_root() -> Path:
    configured = os.getenv("ASSISTANT_READONLY_WORKSPACE_ROOT", "").strip()
    root = Path(configured) if configured else DEFAULT_ROOT
    if not root.is_absolute():
        raise ValueError("Workspace root must be absolute")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Workspace root is unavailable")
    return resolved


def _validate_relative_path(raw_path: str) -> Path:
    value = str(raw_path or ".").strip() or "."
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Workspace path must remain relative")
    if any(part.startswith(".") and part != "." for part in relative.parts):
        raise ValueError("Hidden workspace paths are blocked")
    if any(_is_sensitive_name(part) for part in relative.parts):
        raise ValueError("Sensitive workspace paths are blocked")
    return relative


def _is_sensitive_name(name: str) -> bool:
    normalized = str(name or "").strip().casefold()
    return normalized in SENSITIVE_NAMES or any(
        fragment in normalized for fragment in SENSITIVE_NAME_FRAGMENTS
    )


def resolve_workspace_path(raw_path: str, *, require_file: bool = False) -> Path:
    root = workspace_root()
    relative = _validate_relative_path(raw_path)
    candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Workspace symlinks are blocked")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Workspace path escaped its root")
    if require_file and not resolved.is_file():
        raise ValueError("Workspace file not found")
    return resolved


def _validate_text_file(path: Path) -> None:
    if path.suffix.casefold() not in TEXT_EXTENSIONS:
        raise ValueError("Unsupported workspace file type")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Workspace file exceeds the read limit")


def _relative(path: Path) -> str:
    return path.relative_to(workspace_root()).as_posix()


def _task_base_root() -> Path:
    configured = os.getenv("ASSISTANT_TASK_WORKSPACE_ROOT", "").strip()
    if not configured:
        raise ValueError("Task workspace root is not configured")
    root = Path(configured)
    if not root.is_absolute():
        raise ValueError("Task workspace root must be absolute")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Task workspace root is unavailable")
    return resolved


def _task_scope(employee_id: Any, conversation_id: Any, *, create: bool) -> Path:
    subject = str(employee_id or "").strip().casefold()
    conversation = str(conversation_id or "").strip().casefold()
    if not subject or not conversation:
        raise ValueError("Task workspace identity is incomplete")
    digest = hashlib.sha256(f"{subject}:{conversation}".encode()).hexdigest()
    scope = _task_base_root() / digest
    if create:
        scope.mkdir(mode=0o700, parents=False, exist_ok=True)
    if scope.is_symlink():
        raise ValueError("Task workspace symlinks are blocked")
    resolved = scope.resolve(strict=True)
    if not resolved.is_relative_to(_task_base_root()):
        raise ValueError("Task workspace scope escaped its root")
    return resolved


def _task_path(
    employee_id: Any,
    conversation_id: Any,
    raw_path: str,
    *,
    create_scope: bool,
) -> Path:
    scope = _task_scope(employee_id, conversation_id, create=create_scope)
    relative = _validate_relative_path(raw_path)
    if relative == Path("."):
        raise ValueError("Task workspace requires a file path")
    candidate = scope / relative
    current = scope
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("Task workspace symlinks are blocked")
    return candidate


async def workspace_task_file_create(
    *, employee_id: Any, conversation_id: Any, path: str, content: str
) -> dict[str, Any]:
    if not workspace_task_mutation_allowed(employee_id):
        raise PermissionError("Task workspace mutation is not enabled for this subject")
    raw = str(content or "")
    encoded = raw.encode("utf-8")
    if not encoded or len(encoded) > MAX_TASK_FILE_BYTES:
        raise ValueError("Task workspace content size is invalid")
    target = _task_path(employee_id, conversation_id, path, create_scope=True)
    _validate_text_file_name(target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = target.parent.resolve(strict=True)
    scope = _task_scope(employee_id, conversation_id, create=False)
    if not parent.is_relative_to(scope) or any(
        item.is_symlink() for item in [parent, target] if item.exists()
    ):
        raise ValueError("Task workspace path escaped its scope")
    with target.open("x", encoding="utf-8") as handle:
        handle.write(raw)
    os.chmod(target, 0o600)
    return {
        "path": Path(path).as_posix(),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "created": True,
        "overwritten": False,
    }


def _validate_text_file_name(path: Path) -> None:
    if _is_sensitive_name(path.name) or path.suffix.casefold() not in TEXT_EXTENSIONS:
        raise ValueError("Unsupported task workspace file")


async def workspace_task_file_read(
    *, employee_id: Any, conversation_id: Any, path: str, max_chars: int = 20_000
) -> dict[str, Any]:
    if not workspace_task_mutation_allowed(employee_id):
        raise PermissionError("Task workspace is not enabled for this subject")
    target = _task_path(employee_id, conversation_id, path, create_scope=False)
    if target.is_symlink():
        raise ValueError("Task workspace symlinks are blocked")
    resolved = target.resolve(strict=True)
    scope = _task_scope(employee_id, conversation_id, create=False)
    if not resolved.is_relative_to(scope) or not resolved.is_file():
        raise ValueError("Task workspace file not found")
    _validate_text_file_name(resolved)
    if resolved.stat().st_size > MAX_TASK_FILE_BYTES:
        raise ValueError("Task workspace file exceeds the read limit")
    limit = max(100, min(int(max_chars or 20_000), 50_000))
    return {
        "path": Path(path).as_posix(),
        "content": resolved.read_text(encoding="utf-8", errors="replace")[:limit],
    }


async def workspace_list(*, path: str = ".", limit: int = 100) -> dict[str, Any]:
    target = resolve_workspace_path(path)
    if not target.is_dir():
        raise ValueError("Workspace path is not a directory")
    bounded = max(1, min(int(limit or 100), MAX_LIST_RESULTS))
    entries: list[dict[str, Any]] = []
    for item in sorted(target.iterdir(), key=lambda value: value.name.casefold()):
        if len(entries) >= bounded:
            break
        if item.is_symlink() or item.name.startswith("."):
            continue
        if _is_sensitive_name(item.name):
            continue
        if item.is_file() and item.suffix.casefold() not in TEXT_EXTENSIONS:
            continue
        entries.append(
            {
                "path": _relative(item),
                "kind": "directory" if item.is_dir() else "file",
                "bytes": item.stat().st_size if item.is_file() else None,
            }
        )
    return {
        "path": _relative(target) if target != workspace_root() else ".",
        "entries": entries,
    }


async def workspace_file_read(
    *, path: str, start_line: int = 1, end_line: int = 200, max_chars: int = 20_000
) -> dict[str, Any]:
    target = resolve_workspace_path(path, require_file=True)
    _validate_text_file(target)
    start = max(1, int(start_line or 1))
    end = max(start, min(int(end_line or 200), start + 1_000))
    char_limit = max(100, min(int(max_chars or 20_000), 50_000))
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    content = "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(lines[start - 1 : end], start=start)
    )[:char_limit]
    return {
        "path": _relative(target),
        "start_line": start,
        "end_line": end,
        "content": content,
    }


async def workspace_search(
    *, query: str, path: str = ".", max_results: int = 50
) -> dict[str, Any]:
    needle = str(query or "").strip().casefold()
    if not needle:
        raise ValueError("Workspace search query is required")
    target = resolve_workspace_path(path)
    bounded = max(1, min(int(max_results or 50), MAX_SEARCH_RESULTS))
    candidates = [target] if target.is_file() else sorted(target.rglob("*"))
    matches: list[dict[str, Any]] = []
    for item in candidates:
        if len(matches) >= bounded:
            break
        if (
            not item.is_file()
            or item.is_symlink()
            or any(
                part.startswith(".")
                for part in item.relative_to(workspace_root()).parts
            )
        ):
            continue
        try:
            _validate_text_file(item)
        except ValueError:
            continue
        for number, line in enumerate(
            item.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            if needle in line.casefold():
                matches.append(
                    {"path": _relative(item), "line": number, "text": line[:500]}
                )
                if len(matches) >= bounded:
                    break
    return {
        "query": str(query),
        "path": _relative(target) if target != workspace_root() else ".",
        "matches": matches,
    }
