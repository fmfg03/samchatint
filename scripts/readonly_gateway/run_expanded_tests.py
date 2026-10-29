"""Offline candidate tests: bypass application initializers and all env loaders."""

from __future__ import annotations

import argparse
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
for name in (
    "samchat",
    "samchat.budgets",
    "samchat.ar",
    "devnous",
    "devnous.gastos",
    "devnous.gastos.services",
):
    module = types.ModuleType(name)
    module.__path__ = [str(ROOT / "src" / name.replace(".", "/"))]
    sys.modules[name] = module

parser = argparse.ArgumentParser()
parser.add_argument("--legacy", action="store_true")
parser.add_argument("--pattern", default=None)
args = parser.parse_args()
directory = str(ROOT / "tests" / "unit" / "readonly_gateway")
suite = unittest.defaultTestLoader.discover(
    directory,
    pattern=args.pattern or ("test_*.py" if args.legacy else "test_expanded*.py"),
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
