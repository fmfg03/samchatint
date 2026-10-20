"""Run only this slice without importing the application's conftest/env stack."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
directory = str(ROOT / "tests" / "unit" / "readonly_gateway")
if "--native-informes" in sys.argv:
    print(
        "Explicit suite: offline + live informes + nine unchanged legacy cases on disposable native PostgreSQL."
    )
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.discover(directory, pattern=pattern)
        for pattern in (
            "test_gateway_offline.py",
            "test_activation_components.py",
            "test_daily_audit.py",
            "test_live_informes_native.py",
            "test_postgres_disposable.py",
            "test_service.py",
        )
    )
else:
    suite = unittest.defaultTestLoader.discover(directory, pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
