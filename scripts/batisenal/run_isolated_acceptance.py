"""Run integrated Batiseñal checks against a disposable Unix-only PostgreSQL.

Technical evidence only: this command cannot certify real Chatty acceptance.
It never accepts a database URL and must run as an unprivileged test user.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def child(browser: bool) -> int:
    """Constrain test network and secret-file access before importing the app."""
    socket = str(Path(os.environ["BATISENAL_TEST_SOCKET"]) / ".s.PGSQL.5432")

    def guard(event: str, args: tuple) -> None:
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0]))
            if path.name.startswith(".env") or any(
                part in {".aws", ".ssh", ".codex"} for part in path.parts
            ):
                raise RuntimeError("PILOT_TEST_SECRET_READ_FORBIDDEN")
        if event in {"socket.connect", "socket.bind"}:
            address = args[1]
            if address == socket:
                return
            if browser and isinstance(address, tuple):
                if address[0] in {"127.0.0.1", "::1"} and address[1] != 5432:
                    return
            raise RuntimeError("PILOT_TEST_EXTERNAL_NETWORK_FORBIDDEN")
        if event == "socket.getaddrinfo":
            if not browser or args[0] not in {"127.0.0.1", "::1", "localhost"}:
                raise RuntimeError("PILOT_TEST_EXTERNAL_DNS_FORBIDDEN")

    sys.addaudithook(guard)
    import pytest

    paths = (
        ["tests/browser/test_batisenal_real_pilot.py"]
        if browser
        else [
            "tests/unit/batisenal",
            "tests/integration/batisenal",
            "tests/unit/private_plugin/test_oauth_flow.py",
            "tests/unit/private_plugin/test_oauth_adapters.py",
            "tests/unit/private_plugin/test_oauth_identity.py",
            "tests/unit/private_plugin/test_authorization.py",
            "tests/unit/private_plugin/test_identity_records.py",
        ]
    )
    report = Path(os.environ["BATISENAL_ACCEPTANCE_OUTPUT"])
    junit = report / ("browser.xml" if browser else "technical.xml")
    return int(
        pytest.main(
            [
                "-p",
                "pytest_asyncio.plugin",
                "-p",
                "pytest_cov.plugin",
                "-o",
                "addopts=",
                "-o",
                "asyncio_mode=auto",
                "-q",
                "--tb=short",
                "--cov=src/samchat",
                "--cov=src/devnous/gastos",
                "--cov=scripts",
                "--cov-append",
                "--cov-report=",
                "--cov-fail-under=0",
                f"--junitxml={junit}",
                *paths,
            ]
        )
    )


def check_coverage(output: Path) -> int:
    """Enforce the approved per-module threshold; missing coverage is a failure."""
    report = output / "coverage.json"
    if not report.is_file():
        return 1
    files = json.loads(report.read_text())["files"]
    modules = set(ROOT.glob("src/samchat/batisenal*.py"))
    modules.add(ROOT / "scripts/batisenal_real_pilot.py")
    modules.update(ROOT.glob("src/samchat/batisenal_transport/*.py"))
    modules.update(ROOT.glob("src/devnous/gastos/services/batisenal*.py"))
    modules.update(ROOT.glob("src/devnous/gastos/routes/batisenal*.py"))
    modules.update(
        ROOT / path
        for path in (
            "src/samchat/private_plugin/oauth_flow.py",
            "src/samchat/private_plugin/oauth_store.py",
            "src/devnous/gastos/services/support_ticket_service.py",
            "src/devnous/gastos/services/customer_success_audit.py",
            "src/devnous/gastos/routes/support_routes.py",
        )
    )
    rows = []
    for module in sorted(modules):
        name = str(module.relative_to(ROOT))
        row = files.get(name, files.get(str(module), {})).get("summary", {})
        percent = row.get("percent_covered", 0.0)
        rows.append({"module": name, "percent": percent, "passed": percent >= 85})
    (output / "module-coverage.json").write_text(json.dumps(rows, indent=2) + "\n")
    return int(not rows or any(not row["passed"] for row in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=["technical"], default="technical")
    parser.add_argument("--browser", choices=["firefox", "chromium"], default="firefox")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/batisenal-real"
    )
    parser.add_argument(
        "--child", choices=["technical", "browser"], help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.child:
        return child(args.child == "browser")
    if os.geteuid() == 0:
        parser.error("Run as an unprivileged user; PostgreSQL must never run as root")
    if any(
        os.environ.get(k)
        for k in ("DATABASE_URL", "POSTGRESQL_URL", "PGHOST", "PGSERVICE", "PGDATABASE")
    ):
        parser.error("External database configuration is not accepted")
    binaries = Path("/usr/lib/postgresql/16/bin")
    if not (binaries / "initdb").is_file():
        parser.error("PostgreSQL 16 test binaries are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    for stale in (".coverage", "coverage.json", "receipt.json"):
        (output / stale).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="bs-accept-") as directory:
        temp = Path(directory)
        data, sockets = temp / "data", temp / "socket"
        sockets.mkdir(mode=0o700)
        env = {
            "PATH": os.defpath,
            "HOME": str(temp),
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(ROOT / "src"),
            "SAMCHAT_ENV": "test",
            "SAMCHAT_ENV_FILE": str(temp / "absent-config"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "BATISENAL_BROWSER_ENGINE": args.browser,
            "BATISENAL_TEST_SOCKET": str(sockets),
            "BATISENAL_ACCEPTANCE_OUTPUT": str(output),
            "SAMCHAT_BROWSER_ARTIFACT_DIR": str(output / "browser"),
            "COVERAGE_FILE": str(output / ".coverage"),
            "EXPENSE_REPORT_RECEIPT_BLOB_COLUMN": "archivo_data",
        }
        # An installed test browser is a dependency, not a credential or target.
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        started = False
        with (output / "postgres-setup.log").open("w") as log:

            def pg(name: str, *arguments: str | Path) -> None:
                subprocess.run(
                    [str(binaries / name), *map(str, arguments)],
                    env=env,
                    check=True,
                    stdout=log,
                    stderr=log,
                    timeout=45,
                )

            try:
                pg(
                    "initdb",
                    "-D",
                    data,
                    "-U",
                    "fixture",
                    "--auth-local=trust",
                    "--auth-host=reject",
                    "--encoding=UTF8",
                    "--locale=C",
                )
                pg(
                    "pg_ctl",
                    "-D",
                    data,
                    "-l",
                    temp / "postgres.log",
                    "-o",
                    (
                        "-c listen_addresses='' "
                        f"-c unix_socket_directories='{sockets}' "
                        "-c unix_socket_permissions=0700"
                    ),
                    "-w",
                    "start",
                )
                started = True
                for tier in ("technical", "browser"):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--child",
                            tier,
                        ],
                        cwd=ROOT,
                        env=env,
                        check=False,
                    )
                    results[tier] = result.returncode
                coverage_result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "coverage",
                        "json",
                        "-o",
                        str(output / "coverage.json"),
                    ],
                    cwd=ROOT,
                    env=env,
                    check=False,
                )
                results["coverage_report"] = coverage_result.returncode
            finally:
                if started:
                    pg("pg_ctl", "-D", data, "-m", "fast", "-w", "stop")
                if (temp / "postgres.log").exists():
                    shutil.copyfile(temp / "postgres.log", output / "postgres.log")
    results["module_coverage"] = check_coverage(output)
    receipt = {
        "technical_exit_codes": results,
        "browser_engine": args.browser,
        "real_chatty_e2e": "NOT_RUN",
        "real_login_consent": "PENDING_OPERATIONAL_PLAN",
        "database": "disposable Unix-only PostgreSQL; removed after checks",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return int(any(results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
