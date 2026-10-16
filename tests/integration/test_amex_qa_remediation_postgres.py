"""Collect the approved PostgreSQL acceptance regressions in the CI test lane.

The workflow discovers integration tests. Re-export the original test callables,
including their parametrization marks, rather than copying their assertions.
"""

import importlib.util
import sys
from pathlib import Path

_ACCEPTANCE = Path(__file__).resolve().parents[1] / "acceptance"


def _load(stem):
    if stem in sys.modules:
        return sys.modules[stem]
    spec = importlib.util.spec_from_file_location(stem, _ACCEPTANCE / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    return module


owner = _load("test_amex_sat_budget_direct_postgres")
recovery = _load("test_amex_direct_period_recovery_postgres")
authority = _load("test_amex_direct_selected_cfdi_authority")
consumers = _load("test_amex_effective_consumers_postgres")
direct_pg = owner.direct_pg

for _module in (recovery, authority, consumers):
    for _name, _test in vars(_module).items():
        if _name.startswith("test_") and callable(_test):
            assert _name not in globals(), f"Duplicate acceptance test: {_name}"
            globals()[_name] = _test
