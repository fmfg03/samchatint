"""Optional MinerU subprocess adapter."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass
class MinerUParseResult:
    enabled: bool
    text: str = ""
    markdown: str = ""
    content_list: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def has_text(self) -> bool:
        return bool((self.text or "").strip())


def _command_base() -> List[str]:
    configured = (os.getenv("MINERU_COMMAND") or "mineru").strip()
    return shlex.split(configured) if configured else ["mineru"]


def _extra_args() -> List[str]:
    configured = (os.getenv("MINERU_EXTRA_ARGS") or "").strip()
    return shlex.split(configured) if configured else []


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("MINERU_TIMEOUT_SECONDS", "180")))
    except ValueError:
        return 180.0


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_json_text(item) for item in value)
    if isinstance(value, dict):
        parts: List[str] = []
        for key in (
            "text",
            "content",
            "table_body",
            "table_caption",
            "image_caption",
            "image_footnote",
        ):
            if key in value:
                parts.append(_json_text(value.get(key)))
        return " ".join(part for part in parts if part)
    return str(value)


def _collect_content_list(output_dir: Path) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    for path in sorted(output_dir.rglob("*content_list*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("Could not read MinerU content list %s", path, exc_info=True)
            continue
        if isinstance(payload, list):
            content.extend(item for item in payload if isinstance(item, dict))
    return content


def _collect_markdown(output_dir: Path) -> str:
    parts: List[str] = []
    for path in sorted(output_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.debug("Could not read MinerU markdown %s", path, exc_info=True)
            continue
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _collect_text(
    markdown: str,
    content_list: List[Dict[str, Any]],
    stdout: str,
) -> str:
    parts: List[str] = []
    if markdown.strip():
        parts.append(markdown.strip())
    content_text = "\n".join(
        part for part in (_json_text(item).strip() for item in content_list) if part
    )
    if content_text:
        parts.append(content_text)
    if stdout.strip() and not parts:
        parts.append(stdout.strip())
    return "\n\n".join(parts)


def parse_document_bytes(data: bytes, *, suffix: str = ".pdf") -> MinerUParseResult:
    """Parse document bytes with MinerU when `MINERU_ENABLED` is truthy."""
    if not _env_flag("MINERU_ENABLED", False):
        return MinerUParseResult(enabled=False, error="disabled")
    if not data:
        return MinerUParseResult(enabled=True, error="empty_input")

    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    with tempfile.TemporaryDirectory(prefix="mineru_parse_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / f"input{suffix}"
        output_dir = tmp_path / "output"
        input_path.write_bytes(data)
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = (
            _command_base()
            + ["-p", str(input_path), "-o", str(output_dir)]
            + _extra_args()
        )
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_timeout_seconds(),
                check=False,
            )
        except FileNotFoundError:
            return MinerUParseResult(
                enabled=True,
                error="command_not_found",
                raw={"command": cmd},
            )
        except subprocess.TimeoutExpired:
            return MinerUParseResult(
                enabled=True,
                error="timeout",
                raw={"command": cmd, "timeout_seconds": _timeout_seconds()},
            )
        except Exception as exc:
            logger.warning("MinerU subprocess failed: %s", exc, exc_info=True)
            return MinerUParseResult(
                enabled=True,
                error="subprocess_failed",
                raw={"command": cmd, "message": str(exc)},
            )

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        raw = {
            "command": cmd,
            "returncode": completed.returncode,
            "stdout": stdout[:2000],
            "stderr": stderr[:2000],
        }
        if completed.returncode != 0:
            return MinerUParseResult(enabled=True, error="nonzero_exit", raw=raw)

        content_list = _collect_content_list(output_dir)
        markdown = _collect_markdown(output_dir)
        return MinerUParseResult(
            enabled=True,
            text=_collect_text(markdown, content_list, stdout),
            markdown=markdown,
            content_list=content_list,
            raw=raw,
        )
