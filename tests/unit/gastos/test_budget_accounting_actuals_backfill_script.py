"""Safety checks for the explicit budget-accounting backfill command."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "backfill_budget_accounting_actuals.py"


def test_budget_accounting_backfill_requires_an_explicit_selector() -> None:
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "at least one --refs or --document-refs value is required" in result.stderr


def test_budget_accounting_backfill_exposes_document_reference_selector() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--document-refs" in result.stdout
