"""Run plugin integration tests on an ephemeral Unix-socket-only PostgreSQL.

No external DSN or environment configuration is accepted. pgserver supplies
binaries only: this runner creates and deletes its own isolated cluster, never
uses pgserver's auto-managed/shared server or a production connection.
"""

import importlib.util
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from run_offline_tests import ROOT, guard


def main():
    spec = importlib.util.find_spec("pgserver")
    if spec is None:
        raise SystemExit("Install optional requirements-private-plugin-postgres.txt")
    binaries = Path(spec.origin).parent / "pginstall/bin"
    approved_commands = set()

    def isolated_guard(event, args):
        if event == "subprocess.Popen" and tuple(args[1]) in approved_commands:
            return
        guard(event, args)

    sys.addaudithook(isolated_guard)
    package = types.ModuleType("samchat")
    package.__path__ = [str(ROOT / "src/samchat")]
    sys.modules["samchat"] = package
    sys.path.insert(0, str(ROOT / "tests/unit/private_plugin"))
    from sqlalchemy import URL, create_engine

    with tempfile.TemporaryDirectory(prefix="samchat-plugin-pg-") as directory:
        root = Path(directory)
        data, sockets = root / "data", root / "socket"
        sockets.mkdir(mode=0o700)
        passfile = root / "empty-passfile"
        passfile.touch(mode=0o600)
        log = (root / "commands.log").open("w")
        engine = None
        started = False

        def command(name, *arguments):
            argv = (str(binaries / name), *map(str, arguments))
            approved_commands.add(argv)
            try:
                subprocess.run(
                    argv,
                    check=True,
                    stdout=log,
                    stderr=log,
                    timeout=30,
                    env={"PATH": str(binaries), "LANG": "C.UTF-8"},
                )
            finally:
                approved_commands.remove(argv)

        try:
            command(
                "initdb",
                "-D",
                data,
                "-U",
                "fixture",
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            )
            try:
                command(
                    "pg_ctl",
                    "-D",
                    data,
                    "-l",
                    root / "server.log",
                    "-o",
                    f"-h '' -k {sockets}",
                    "-w",
                    "start",
                )
            except subprocess.CalledProcessError:
                # No tables/tokens exist at startup: this is server setup only.
                print((root / "server.log").read_text())
                raise RuntimeError("ISOLATED_POSTGRES_START_FAILED") from None
            started = True
            engine = create_engine(
                URL.create(
                    "postgresql+psycopg",
                    username="fixture",
                    database="postgres",
                    query={"host": str(sockets), "passfile": str(passfile)},
                ),
                echo=False,
                hide_parameters=True,
            )
            with engine.connect() as connection:
                assert (
                    connection.exec_driver_sql("SHOW listen_addresses").scalar_one()
                    == ""
                )
                print(
                    "Isolated PostgreSQL",
                    connection.exec_driver_sql("SHOW server_version").scalar_one(),
                    "; TCP disabled",
                )
            context = types.ModuleType("samchat_private_pg_fixture")
            context.engine = engine
            sys.modules[context.__name__] = context
            suite = unittest.defaultTestLoader.discover(
                str(ROOT / "tests/integration/private_plugin"), pattern="test_*.py"
            )
            if suite.countTestCases() == 0:
                raise RuntimeError("No PostgreSQL tests discovered")
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            if engine is not None:
                engine.dispose()
            if started:
                command("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop")
            log.close()
            sys.modules.pop("samchat_private_pg_fixture", None)


if __name__ == "__main__":
    raise SystemExit(main())
