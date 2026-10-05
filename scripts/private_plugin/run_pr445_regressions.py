"""Run existing pure PR445 claims/context regressions without runtime bootstrap.

This is a focused regression suite, not all PR445 integration/provider tests.
Requires pytest in the test interpreter; never reads .env or calls a model.
"""

import os
import sys
import types

from run_offline_tests import ROOT, guard


def main():
    sys.addaudithook(guard)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for name, relative in (
        ("samchat", "src/samchat"),
        ("samchat.assistant", "src/samchat/assistant"),
    ):
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT / relative)]
        sys.modules[name] = package
    import pytest

    return pytest.main(
        [
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(ROOT / "tests/unit"),
            str(ROOT / "tests/unit/test_assistant_financial_claims.py"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
