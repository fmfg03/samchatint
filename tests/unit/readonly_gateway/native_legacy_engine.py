"""Run the unchanged nine legacy SQL acceptance cases on disposable PostgreSQL.

Reuses the synthetic PGlite fixture verbatim. Never accepts a TCP/production DSN.
"""

import re
import subprocess
from pathlib import Path

from samchat.readonly_gateway.asyncpg_driver import (
    AsyncpgConnectionFactory,
    positional_sql,
)


class NativeLegacyEngine:
    def __init__(self, socket_path: str) -> None:
        path = Path(socket_path)
        if not str(path).startswith("/tmp/samchat-ro-native-") or not path.is_dir():
            raise RuntimeError("Only disposable native PostgreSQL is permitted")
        self.path = path
        subprocess.run(
            [
                "psql",
                "-Xq",
                "-h",
                str(path),
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "CREATE DATABASE fixture_legacy",
            ],
            check=True,
            capture_output=True,
        )

        async def connect():
            import asyncpg

            return await asyncpg.connect(
                host=str(path), database="fixture_legacy", user="postgres"
            )

        self.connection = AsyncpgConnectionFactory(connect)()
        fixture = re.search(
            r"await db\.exec\(`(.*?)`\);",
            Path(__file__).with_name("pglite_bridge.mjs").read_text(),
            re.S,
        )[1]
        self.connection._run(self.connection.raw.execute(fixture))
        self.connection._run(
            self.connection.raw.execute(
                "RESET ROLE; SET SESSION AUTHORIZATION fixture_reader"
            )
        )
        tables = [
            "documentos",
            "aprobaciones",
            "expense_reports",
            "anticipos",
            "reembolsos",
            "empleados",
            "authorization_positions",
            "authorization_position_assignments",
            "documento_authorization_routes",
        ]
        schema = self.query(
            "SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%(tables)s::text[]) ORDER BY table_name,column_name",
            {"tables": tables},
        )["rows"]
        identity = self.query("SELECT current_database() AS db,current_user AS role")[
            "rows"
        ][0]
        self.ready = {"schema": schema, "identity": identity}

    def query(self, sql, args=None):
        sql, values = positional_sql(sql, args)
        try:
            if sql.lstrip().upper().startswith("SELECT"):
                records = self.connection._run(self.connection.raw.fetch(sql, *values))
                rows = [dict(record) for record in records]
                return {"rows": rows, "fields": list(rows[0]) if rows else []}
            self.connection._run(self.connection.raw.execute(sql, *values))
            return {"rows": [], "fields": []}
        except Exception as error:
            raise RuntimeError("Synthetic PostgreSQL query rejected") from error

    def close(self):
        self.connection.close()
        subprocess.run(
            [
                "psql",
                "-Xq",
                "-h",
                str(self.path),
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "DROP DATABASE fixture_legacy",
                "-c",
                "DROP ROLE fixture_reader",
            ],
            check=True,
            capture_output=True,
        )
