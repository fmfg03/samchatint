#!/usr/bin/env python3
"""Guard basic packaging/runtime documentation consistency.

This check is intentionally lightweight:
- no network
- no dependency installation
- no imports from project runtime modules
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import re

import tomllib
from setuptools.build_meta import prepare_metadata_for_build_wheel


ROOT = Path(__file__).resolve().parents[1]
DOC_PATTERN_CHECK_DIRS = [
    ROOT / "docs" / "api",
    ROOT / "docs" / "deployment",
    ROOT / "docs" / "security",
    ROOT / "docs" / "operations",
]
LEGACY_DOC_PATTERNS = [
    re.compile(r"api\.devnous\.example\.com"),
    re.compile(r"\bdevnous\.example\.com\b"),
    re.compile(r"postgresql://[^\s`\"]*devnous", re.IGNORECASE),
    re.compile(r"/var/log/devnous"),
    re.compile(r"\bdevnous\s+(?:server|health|logs|config|db)\b"),
]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _iter_docs_with_legacy_patterns() -> list[Path]:
    matched: list[Path] = []
    for base in DOC_PATTERN_CHECK_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            if any(pattern.search(text) for pattern in LEGACY_DOC_PATTERNS):
                matched.append(path)
    return matched


def main() -> int:
    errors: list[str] = []

    required_files = [
        "README.md",
        "AGENTS.md",
        "pyproject.toml",
        "requirements.txt",
        "requirements-runtime.txt",
        "requirements-test.txt",
        "requirements-docs.txt",
        "requirements-dev.txt",
        "docs/install_matrix.md",
        "docs/runtime_map.md",
    ]
    for rel in required_files:
        _require((ROOT / rel).exists(), f"Missing required file: {rel}", errors)

    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1

    pyproject = tomllib.loads(_read("pyproject.toml"))
    project = pyproject.get("project", {})
    setuptools_cfg = pyproject.get("tool", {}).get("setuptools", {})
    dynamic_cfg = setuptools_cfg.get("dynamic", {})

    _require(
        project.get("dynamic") == ["dependencies"],
        "pyproject.toml must declare project.dynamic = ['dependencies']",
        errors,
    )
    _require(
        dynamic_cfg.get("dependencies") == {"file": ["requirements-runtime.txt"]},
        "pyproject.toml must source runtime dependencies from requirements-runtime.txt",
        errors,
    )

    readme = _read("README.md")
    agents = _read("AGENTS.md")
    install_matrix = _read("docs/install_matrix.md")
    runtime_map = _read("docs/runtime_map.md")
    requirements_test = _read("requirements-test.txt")
    requirements_docs = _read("requirements-docs.txt")
    requirements_dev = _read("requirements-dev.txt")

    _require("## Runtime Status" in readme, "README.md missing runtime status section", errors)
    _require("docs/install_matrix.md" in readme, "README.md must link docs/install_matrix.md", errors)
    _require("requirements-docs.txt" in agents, "AGENTS.md must mention docs install profile", errors)
    _require("samchat-gastos.service" in install_matrix, "docs/install_matrix.md must mention production service", errors)
    _require("copa_telmex_dashboard.py" in runtime_map, "docs/runtime_map.md must mention copa_telmex_dashboard.py", errors)
    _require(
        requirements_test.splitlines()[0].strip() == "-r requirements-runtime.txt",
        "requirements-test.txt must extend requirements-runtime.txt",
        errors,
    )
    _require(
        requirements_docs.splitlines()[0].strip() == "-r requirements-runtime.txt",
        "requirements-docs.txt must extend requirements-runtime.txt",
        errors,
    )
    dev_lines = [line.strip() for line in requirements_dev.splitlines() if line.strip()]
    _require(
        "-r requirements-test.txt" in dev_lines and "-r requirements-docs.txt" in dev_lines,
        "requirements-dev.txt must extend both requirements-test.txt and requirements-docs.txt",
        errors,
    )

    with tempfile.TemporaryDirectory() as td:
        dist_dir = prepare_metadata_for_build_wheel(td)
        metadata = (Path(td) / dist_dir / "METADATA").read_text(encoding="utf-8")

    _require("Runtime Status" in metadata, "Package metadata did not absorb updated README", errors)
    _require("docs/install_matrix.md" in metadata, "Package metadata missing install matrix reference", errors)
    _require(
        "Requires-Dist: sphinx >=" not in metadata,
        "Docs-only dependencies leaked into runtime package metadata",
        errors,
    )
    _require(
        "Requires-Dist: fastapi >=" in metadata,
        "Runtime package metadata missing expected runtime dependency",
        errors,
    )

    for path in _iter_docs_with_legacy_patterns():
        text = path.read_text(encoding="utf-8")
        _require(
            "docs/install_matrix.md" in text,
            f"{path.relative_to(ROOT)} contains legacy DevNous/runtime patterns but does not reference docs/install_matrix.md",
            errors,
        )

    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1

    print("[OK] runtime/package/docs consistency checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
