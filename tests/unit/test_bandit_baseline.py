from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "check-bandit-baseline.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_bandit_baseline", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compare_accepts_an_exact_match() -> None:
    module = _load_module()

    assert module.compare({"src/example.py:1:B324"}, {"src/example.py:1:B324"}) == []


def test_compare_rejects_a_new_finding() -> None:
    module = _load_module()

    assert "New high-confidence Bandit findings" in module.compare(
        {"src/example.py:1:B324", "src/new.py:2:B324"},
        {"src/example.py:1:B324"},
    )[0]


def test_compare_requires_resolved_finding_cleanup() -> None:
    module = _load_module()

    assert "no longer reproduced" in module.compare(set(), {"src/example.py:1:B324"})[0]
