"""Existing local runtime only; prohibit all socket network operations."""
import os
from pathlib import Path
import sys
import json
import unittest

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
OUT = ROOT.parent / "c/artifacts/pilot/ci-results/modern"
OUT.mkdir(parents=True, exist_ok=True)


def guard(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("ISOLATED_TRANSPORT_TEST_NETWORK_FORBIDDEN")


sys.addaudithook(guard)
import coverage
cov = coverage.Coverage(data_file=str(OUT / ".coverage-modern"), source=[str(ROOT.parent / "c/src/samchat/batisenal_transport")])
cov.start()
status = 1
try:
    suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_*.py")
    with (OUT / "test-results.log").open("w", encoding="utf-8") as output:
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    summary = {"run": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": len(result.skipped),
               "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
               "network_guard": "socket connect/bind/DNS/sendto forbidden",
               "scope": "ASGI transport contract with explicit spies + presentation; no real OAuth/DB integration"}
    (OUT / "test-results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print((OUT / "test-results.log").read_text(encoding="utf-8"))
    status = 0 if result.wasSuccessful() else 1
finally:
    cov.stop()
    cov.save()
    cov.json_report(outfile=str(OUT / "coverage-modern.json"))
    cov.report(show_missing=True)
raise SystemExit(status)
