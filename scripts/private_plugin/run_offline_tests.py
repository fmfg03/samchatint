"""Run isolated synthetic tests; never bootstrap SamChat runtime/configuration.

Usage: python scripts/private_plugin/run_offline_tests.py
This runner intentionally bypasses samchat/__init__.py. Runtime integration is
not covered. MCP tests use the installed SDK with memory streams, no sockets.
"""

import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def guard(event, args):
    if event == "subprocess.Popen":
        if (
            tuple(args[1])
            in {
                ("git", "ls-files", "--", "*.py"),
                ("git", "rev-parse", "HEAD"),
            }
            and Path(args[2]) == ROOT
        ):
            return  # Read-only tracked-source inventory, never a network command.
        raise RuntimeError("Offline tests prohibit subprocess effects")
    if event in {"socket.connect", "socket.bind", "os.system"}:
        raise RuntimeError("Offline tests prohibit external effects")
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0]))
        if path.name.startswith(".env") or any(
            part in {".aws", ".ssh", ".codex", "credentials"} for part in path.parts
        ):
            raise RuntimeError("Offline tests prohibit credential/config reads")


def main():
    sys.addaudithook(guard)
    package = types.ModuleType("samchat")
    package.__path__ = [str(ROOT / "src/samchat")]
    sys.modules["samchat"] = package
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests/unit/private_plugin"), pattern="test_*.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
