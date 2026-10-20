"""Real SQL execution against synthetic versioned projections, with SELECT ACL.

No production connection factory is provided. PostgreSQL projection installation
and canonical scoped owner adapters require a separate exact activation receipt.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from .contracts import Principal, ReadError

TABLES = frozenset(
    {
        "document_status",
        "document_audit",
        "document_route",
        "budget_availability",
        "expense_summary",
    }
)


class Repository:
    """Owns a read-only SQLite connection to synthetic projected fixtures."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.cancelled = False
        self.query_count = 0
        self.connection.execute("PRAGMA query_only=ON")
        self.schema_fingerprint = self._fingerprint()
        self.connection.set_authorizer(self._authorize)

    def _fingerprint(self) -> str:
        schema = {
            table: [
                tuple(row)
                for row in self.connection.execute(f"PRAGMA table_info({table})")
            ]
            for table in sorted(TABLES)
        }
        return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        del database, trigger
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and first in TABLES:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and second in {
            "count",
            "sum",
            "substr",
            "coalesce",
        }:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def select(
        self, sql: str, parameters: dict[str, Any], *, cap: int = 1001
    ) -> list[dict[str, Any]]:
        """Internal static SQL only; intentionally not exposed as a tool."""
        deadline = time.monotonic() + 3
        self.query_count += 1
        self.connection.set_progress_handler(
            lambda: int(self.cancelled or time.monotonic() > deadline), 100
        )
        try:
            rows = self.connection.execute(sql, parameters).fetchmany(cap + 1)
            if len(rows) > cap:
                raise ReadError("OUTPUT_LIMIT")
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            if self.cancelled:
                raise ReadError("CANCELLED") from exc
            if time.monotonic() > deadline:
                raise ReadError("TIMEOUT") from exc
            raise ReadError("SOURCE_UNAVAILABLE") from exc
        finally:
            self.connection.set_progress_handler(None, 0)

    def interrupt(self) -> None:
        self.connection.interrupt()

    @staticmethod
    def scope(principal: Principal) -> tuple[str, dict[str, Any]]:
        names = [f"t{i}" for i in range(len(principal.tournaments))]
        parameters: dict[str, Any] = {
            "installation": principal.installation,
            "reader": principal.actor_binding,
        }
        parameters.update(zip(names, sorted(principal.tournaments)))
        predicate = (
            "installation_id=:installation AND reader_binding=:reader "
            "AND tournament_id IN (" + ",".join(":" + n for n in names) + ")"
        )
        return predicate, parameters

    def document(self, principal: Principal, document_id: str) -> dict[str, Any]:
        scope, args = self.scope(principal)
        args["id"] = document_id
        rows = self.select(
            "SELECT document_id,tournament_id,kind,state,reference_operations,"
            "reference_samchat,reference_base,created_at,submitted_at,currency,"
            "amount,active_expenses,classification_present,authority_coverage "
            f"FROM document_status WHERE {scope} AND document_id=:id",
            args,
            cap=2,
        )
        if len(rows) != 1:
            raise ReadError("NOT_FOUND_OR_NOT_VISIBLE")
        return rows[0]

    def authority(self, principal: Principal, document_id: str) -> dict[str, Any]:
        scope, args = self.scope(principal)
        args["id"] = document_id
        rows = self.select(
            "SELECT approver_alias,actor_can_decide,resolution,policy_version "
            f"FROM document_route WHERE {scope} AND document_id=:id",
            args,
            cap=20,
        )
        if not rows:
            return {
                "resolution": "unknown",
                "actor_can_decide": None,
                "approver_aliases": [],
                "policy_versions": [],
            }
        if any(row["resolution"] != "resolved" for row in rows):
            return {
                "resolution": "pending_assignment",
                "actor_can_decide": False,
                "approver_aliases": [],
                "policy_versions": sorted({row["policy_version"] for row in rows}),
            }
        return {
            "resolution": "resolved",
            "actor_can_decide": any(bool(row["actor_can_decide"]) for row in rows),
            "approver_aliases": sorted({row["approver_alias"] for row in rows}),
            "policy_versions": sorted({row["policy_version"] for row in rows}),
        }
