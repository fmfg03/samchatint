"""Run assembly checks in a fresh interpreter, never the financial test process."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "check",
    [
        "test_injected_technical_assembly_mounts_only_native_auth_and_private_case",
        "test_real_agent_assembly_uses_registration_hash_without_claiming_real_call",
        "test_financial_import_or_configured_auth_rejected",
        "test_unfixed_canonical_audit_blocks_activation",
        "test_invalid_resources_fail_before_catalog_sql[identity]",
        "test_invalid_resources_fail_before_catalog_sql[cookie]",
        "test_invalid_resources_fail_before_catalog_sql[signer]",
        "test_native_route_contract_change_rejected",
        "test_isolated_auth_diagnostics_remove_user_and_sql_values",
    ],
)
def test_factory_in_isolated_process(check, monkeypatch):
    # The global suite imports the financial runtime during collection. Preserve
    # that process boundary rather than deleting its module or relaxing the guard.
    marker = object()
    monkeypatch.setitem(sys.modules, "copa_telmex_dashboard", marker)
    source = Path(__file__).with_name("host_factory_checks.py")
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", f"{source}::{check}"],
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert sys.modules["copa_telmex_dashboard"] is marker
    assert result.returncode == 0, result.stdout + result.stderr
