"""
Utilities to invoke the local OCR pipeline from the production bot process.

The Telegram bot currently runs under the system Python interpreter, while the
local OCR stack lives in the repo virtualenv (`.venv`) because it requires
torch/transformers. This runner bridges both worlds via a bounded subprocess.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


logger = logging.getLogger(__name__)


class LocalOCRRunner:
    """Launch the local registration OCR script in a Python environment with ML deps."""

    def __init__(
        self,
        *,
        repo_root: Path,
        python_executable: Optional[str] = None,
        script_path: Optional[Path] = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.python_executable = python_executable or self._resolve_python_executable()
        self.script_path = (
            Path(script_path).resolve()
            if script_path
            else (self.repo_root / "scripts" / "local_registration_ocr.py").resolve()
        )
        self.timeout_seconds = max(5.0, float(timeout_seconds))

    def _resolve_python_executable(self) -> str:
        env_override = (os.getenv("LOCAL_OCR_PYTHON") or "").strip()
        candidates = [
            Path(env_override) if env_override else None,
            self.repo_root / ".venv" / "bin" / "python",
            self.repo_root / "ocr_env" / "bin" / "python",
            Path(sys.executable),
            Path("/usr/bin/python3"),
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                return str(candidate)
        return "python3"

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        py_path_parts = [
            str(self.repo_root),
            str(self.repo_root / "src"),
        ]
        existing = env.get("PYTHONPATH")
        if existing:
            py_path_parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(py_path_parts)
        return env

    def extract_registration_form_from_bytes(
        self,
        optimized_bytes: bytes,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Execute local OCR and return `(extraction_dict, raw_payload)`.

        Returns `(None, error_payload)` when the subprocess fails or the local
        stack is unavailable.
        """
        if not self.script_path.exists():
            return None, {
                "error": "local_ocr_script_missing",
                "script_path": str(self.script_path),
            }

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(optimized_bytes)
            tmp_path = Path(tmp.name)

        cmd = [
            self.python_executable,
            str(self.script_path),
            "--image",
            str(tmp_path),
        ]

        try:
            completed = subprocess.run(
                cmd,
                cwd=str(self.repo_root),
                env=self._build_env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Local OCR timed out after %.1fs", self.timeout_seconds)
            return None, {
                "error": "local_ocr_timeout",
                "timeout_seconds": self.timeout_seconds,
            }
        except Exception as exc:
            logger.warning("Local OCR subprocess failed: %s", exc, exc_info=True)
            return None, {
                "error": "local_ocr_subprocess_failed",
                "message": str(exc),
            }
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Could not delete local OCR temp file %s", tmp_path, exc_info=True)

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0:
            logger.warning(
                "Local OCR returned non-zero exit status %s: %s",
                completed.returncode,
                stderr or stdout,
            )
            return None, {
                "error": "local_ocr_failed",
                "returncode": completed.returncode,
                "stderr": stderr,
                "stdout": stdout[:1000],
            }

        try:
            payload = json.loads(stdout)
        except Exception as exc:
            logger.warning("Local OCR produced invalid JSON: %s", exc)
            return None, {
                "error": "local_ocr_invalid_json",
                "message": str(exc),
                "stdout": stdout[:1000],
                "stderr": stderr[:1000],
            }

        if not isinstance(payload, dict):
            return None, {
                "error": "local_ocr_invalid_payload",
                "stdout": stdout[:1000],
            }

        extraction = payload.get("extraction")
        raw_payload = payload.get("raw") if isinstance(payload.get("raw"), dict) else payload
        if not isinstance(extraction, dict):
            return None, raw_payload or {
                "error": "local_ocr_missing_extraction",
                "payload": payload,
            }

        return extraction, raw_payload

    async def extract_registration_form_from_bytes_async(
        self,
        optimized_bytes: bytes,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not self.script_path.exists():
            return None, {
                "error": "local_ocr_script_missing",
                "script_path": str(self.script_path),
            }

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(optimized_bytes)
            tmp_path = Path(tmp.name)

        cmd = [
            self.python_executable,
            str(self.script_path),
            "--image",
            str(tmp_path),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.repo_root),
                env=self._build_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                logger.warning("Local OCR timed out after %.1fs", self.timeout_seconds)
                return None, {
                    "error": "local_ocr_timeout",
                    "timeout_seconds": self.timeout_seconds,
                }
        except Exception as exc:
            logger.warning("Local OCR async subprocess failed: %s", exc, exc_info=True)
            return None, {
                "error": "local_ocr_subprocess_failed",
                "message": str(exc),
            }
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Could not delete local OCR temp file %s", tmp_path, exc_info=True)

        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            logger.warning(
                "Local OCR returned non-zero exit status %s: %s",
                process.returncode,
                stderr or stdout,
            )
            return None, {
                "error": "local_ocr_failed",
                "returncode": process.returncode,
                "stderr": stderr,
                "stdout": stdout[:1000],
            }

        try:
            payload = json.loads(stdout)
        except Exception as exc:
            logger.warning("Local OCR produced invalid JSON: %s", exc)
            return None, {
                "error": "local_ocr_invalid_json",
                "message": str(exc),
                "stdout": stdout[:1000],
                "stderr": stderr[:1000],
            }

        if not isinstance(payload, dict):
            return None, {
                "error": "local_ocr_invalid_payload",
                "stdout": stdout[:1000],
            }

        extraction = payload.get("extraction")
        raw_payload = payload.get("raw") if isinstance(payload.get("raw"), dict) else payload
        if not isinstance(extraction, dict):
            return None, raw_payload or {
                "error": "local_ocr_missing_extraction",
                "payload": payload,
            }

        return extraction, raw_payload
