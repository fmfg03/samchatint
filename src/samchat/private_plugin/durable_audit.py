"""Explicit local SQLite audit evidence; no production store or runtime wiring.

The caller owns a dedicated file-backed SQLite connection. Initialization is a
separate local migration, never performed by the adapter constructor or imports.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from uuid import uuid4

from .catalog import operations

ACTIONS = frozenset(op.action for op in operations()) | {
    "get_profile",
    "direction_read_summary",
    "mcp.tools.list",
    "mcp.tools.call",
    "unregistered",
}
OUTCOMES = frozenset(
    {
        "READ_CATALOG",
        "READ_REQUESTED",
        "READ_VERIFIED",
        "ACTION_NOT_REGISTERED",
        "UNAUTHENTICATED",
        "TARGET_REQUIRED",
        "INVALID_BINDING",
        "INVALID_AMOUNT",
        "FORBIDDEN",
        "IDEMPOTENCY_REQUIRED",
        "CONFIRMATION_REQUIRED",
        "CONFIRMATION_MISMATCH",
        "DEPENDENCY_UNAVAILABLE",
        "CANONICAL_SCOPE_UNPROVEN",
        "CAPABILITIES_DISABLED",
        "CAPABILITY_DISABLED",
        "INVALID_ARGUMENTS",
        "AUDIT_UNAVAILABLE",
        "SCOPE_UNPROVEN",
        "CANONICAL_RESULT_INVALID",
        "ORGANIZATION_UNPROVEN",
        "SCOPE_CHANGED",
        "SOURCE_UNAVAILABLE",
    }
)
SCHEMA_VERSION = 1
COLUMNS = (
    "receipt_id",
    "actor_id",
    "action",
    "outcome",
    "evidence_digest",
    "scope_digest",
    "created_at_ns",
    "record_digest",
)
SCHEMA = (
    "CREATE TABLE local_audit_version (version INTEGER PRIMARY KEY CHECK(version=1))",
    "INSERT INTO local_audit_version(version) VALUES (1)",
    """CREATE TABLE local_audit_receipts (
        receipt_id TEXT PRIMARY KEY NOT NULL,
        actor_id TEXT,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL,
        evidence_digest TEXT,
        scope_digest TEXT,
        created_at_ns INTEGER NOT NULL CHECK(created_at_ns>0),
        record_digest TEXT NOT NULL CHECK(length(record_digest)=64),
        CHECK((evidence_digest IS NULL AND scope_digest IS NULL)
           OR (length(evidence_digest)=64 AND length(scope_digest)=64))
    )""",
    "CREATE INDEX local_audit_actor ON local_audit_receipts(actor_id,receipt_id)",
    """CREATE TRIGGER local_audit_no_update BEFORE UPDATE ON local_audit_receipts
        BEGIN SELECT RAISE(ABORT, 'immutable audit'); END""",
    """CREATE TRIGGER local_audit_no_delete BEFORE DELETE ON local_audit_receipts
        BEGIN SELECT RAISE(ABORT, 'immutable audit'); END""",
)


class AuditStorageError(RuntimeError):
    """Stable error with no SQL, filesystem paths, payloads or credentials."""


@dataclass(frozen=True)
class AuditReceipt:
    receipt_id: str
    actor_id: str | None
    action: str
    outcome: str
    evidence_digest: str | None
    scope_digest: str | None
    created_at_ns: int
    record_digest: str


def _record_digest(values: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _actor(value: str | None, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise AuditStorageError("AUDIT_INVALID_RECORD")


def _file_connection(connection: sqlite3.Connection) -> None:
    # Attached stores and in-memory databases cannot prove isolated durability.
    if connection.in_transaction:
        raise AuditStorageError("AUDIT_TRANSACTION_ACTIVE")
    databases = connection.execute("PRAGMA database_list").fetchall()
    if len(databases) != 1 or databases[0][1] != "main" or not databases[0][2]:
        raise AuditStorageError("AUDIT_LOCAL_FILE_REQUIRED")


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        pass


def initialize_local_audit(connection: sqlite3.Connection) -> None:
    """Explicit migration of a dedicated, empty, caller-selected LOCAL file.

    Idempotent for this schema. Rejects databases containing unrelated tables.
    No production connection, file opening or application startup calls belong here.
    """
    started = False
    try:
        _file_connection(connection)
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] in {"off", "memory"}:
            raise AuditStorageError("AUDIT_DURABILITY_REQUIRED")
        connection.execute("BEGIN IMMEDIATE")
        started = True
        tables = {
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if tables:
            if tables != {"local_audit_version", "local_audit_receipts"}:
                raise AuditStorageError("AUDIT_DEDICATED_STORE_REQUIRED")
            if connection.execute(
                "SELECT version FROM local_audit_version"
            ).fetchall() != [(SCHEMA_VERSION,)]:
                raise AuditStorageError("AUDIT_SCHEMA_INVALID")
        else:
            for statement in SCHEMA:
                connection.execute(statement)
        connection.commit()
    except Exception as exc:
        if started:
            _rollback(connection)
        if isinstance(exc, AuditStorageError):
            raise exc from None
        raise AuditStorageError("AUDIT_UNAVAILABLE") from None


class SQLiteAudit:
    """Implements AuditSink and ReadAuditSink using an existing local schema.

    Use one connection per worker. A shared connection requires caller-selected
    check_same_thread=False; this instance serializes its operations only.
    Ownership excludes sharing the connection with unrelated transactions.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._lock = threading.RLock()
        try:
            self._validate_store()
        except AuditStorageError:
            raise
        except Exception:
            raise AuditStorageError("AUDIT_UNAVAILABLE") from None

    def _validate_store(self) -> None:
        _file_connection(self._connection)
        if self._connection.execute("PRAGMA synchronous").fetchone()[0] not in {2, 3}:
            raise AuditStorageError("AUDIT_DURABILITY_REQUIRED")
        if self._connection.execute("PRAGMA journal_mode").fetchone()[0] in {
            "off",
            "memory",
        }:
            raise AuditStorageError("AUDIT_DURABILITY_REQUIRED")
        version = self._connection.execute(
            "SELECT version FROM local_audit_version"
        ).fetchall()
        if version != [(SCHEMA_VERSION,)]:
            raise AuditStorageError("AUDIT_SCHEMA_INVALID")
        triggers = {
            r[0]
            for r in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='local_audit_receipts'"
            )
        }
        if not {"local_audit_no_update", "local_audit_no_delete"} <= triggers:
            raise AuditStorageError("AUDIT_SCHEMA_INVALID")

    def append(self, *, actor_id: str | None, action: str, outcome: str) -> str:
        """Commit a minimal event before returning a receipt ID."""
        if outcome == "READ_VERIFIED":
            raise AuditStorageError("AUDIT_INVALID_RECORD")
        return self._append(actor_id, action, outcome, None, None)

    def append_read(
        self, *, actor_id: str, action: str, evidence_digest: str, scope_digest: str
    ) -> str:
        """Commit only exact result/scope digests, never the report or payload."""
        _actor(actor_id)
        for value in (evidence_digest, scope_digest):
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise AuditStorageError("AUDIT_INVALID_RECORD")
        return self._append(
            actor_id, action, "READ_VERIFIED", evidence_digest, scope_digest
        )

    def _append(self, actor, action, outcome, evidence, scope) -> str:
        _actor(actor, optional=True)
        if (
            not isinstance(action, str)
            or action not in ACTIONS
            or not isinstance(outcome, str)
            or outcome not in OUTCOMES
        ):
            raise AuditStorageError("AUDIT_INVALID_RECORD")
        fields = dict(
            zip(
                COLUMNS[:-1],
                (uuid4().hex, actor, action, outcome, evidence, scope, time.time_ns()),
            )
        )
        fields["record_digest"] = _record_digest(fields)
        with self._lock:
            started = False
            try:
                self._validate_store()
                self._connection.execute("BEGIN IMMEDIATE")
                started = True
                self._connection.execute(
                    "INSERT INTO local_audit_receipts VALUES (?,?,?,?,?,?,?,?)",
                    tuple(fields[c] for c in COLUMNS),
                )
                self._connection.commit()
                return fields["receipt_id"]
            except Exception as exc:
                if started:
                    _rollback(self._connection)
                if isinstance(exc, AuditStorageError):
                    raise exc from None
                raise AuditStorageError("AUDIT_UNAVAILABLE") from None

    def get_for_actor(self, receipt_id: str, *, actor_id: str) -> AuditReceipt | None:
        """Trusted caller supplies current actor; other actors see no receipt.

        This is a storage filter, not an authentication endpoint. Anonymous
        audit events are intentionally unavailable through this method.
        """
        _actor(actor_id)
        if not isinstance(receipt_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", receipt_id
        ):
            raise AuditStorageError("AUDIT_INVALID_RECORD")
        with self._lock:
            try:
                self._validate_store()
                row = self._connection.execute(
                    "SELECT"
                    " receipt_id,actor_id,action,outcome,evidence_digest,"
                    "scope_digest,created_at_ns,record_digest"
                    " FROM local_audit_receipts WHERE receipt_id=? AND actor_id=?",
                    (receipt_id, actor_id),
                ).fetchone()
                if row is None:
                    return None
                receipt = AuditReceipt(*row)
                fields = asdict(receipt)
                stored = fields.pop("record_digest")
                if not isinstance(stored, str) or not hmac.compare_digest(
                    stored, _record_digest(fields)
                ):
                    raise AuditStorageError("AUDIT_INTEGRITY_FAILED")
                return receipt
            except AuditStorageError:
                raise
            except Exception:
                raise AuditStorageError("AUDIT_UNAVAILABLE") from None
