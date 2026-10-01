"""Local file evidence only: fixtures contain no real accounts or report data."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

from samchat.private_plugin.durable_audit import (
    AuditStorageError,
    SQLiteAudit,
    initialize_local_audit,
)


class DurableAuditTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "synthetic-audit.sqlite"
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        initialize_local_audit(self.connection)
        self.audit = SQLiteAudit(self.connection)

    def read_receipt(self, audit=None):
        return (audit or self.audit).append_read(
            actor_id="fixture-actor",
            action="direction_read_summary",
            evidence_digest="a" * 64,
            scope_digest="b" * 64,
        )

    def test_reopen_verified_receipt_is_stable_and_actor_isolated(self):
        receipt_id = self.read_receipt()
        receipt = self.audit.get_for_actor(receipt_id, actor_id="fixture-actor")
        with sqlite3.connect(self.path) as reopened:
            store = SQLiteAudit(reopened)
            self.assertEqual(
                receipt, store.get_for_actor(receipt_id, actor_id="fixture-actor")
            )
            self.assertIsNone(store.get_for_actor(receipt_id, actor_id="another-actor"))
        self.assertEqual(receipt.outcome, "READ_VERIFIED")
        self.assertEqual(receipt.evidence_digest, "a" * 64)
        self.assertEqual(receipt.scope_digest, "b" * 64)
        with self.assertRaises(FrozenInstanceError):
            receipt.outcome = "invented"

    def test_schema_minimized_and_immutable(self):
        receipt_id = self.read_receipt()
        columns = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info(local_audit_receipts)"
            )
        }
        self.assertEqual(
            columns,
            {
                "receipt_id",
                "actor_id",
                "action",
                "outcome",
                "evidence_digest",
                "scope_digest",
                "created_at_ns",
                "record_digest",
            },
        )
        for statement in (
            "UPDATE local_audit_receipts SET outcome='FORBIDDEN' WHERE receipt_id=?",
            "DELETE FROM local_audit_receipts WHERE receipt_id=?",
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(statement, (receipt_id,))
            self.connection.rollback()
        self.assertEqual(
            self.audit.get_for_actor(receipt_id, actor_id="fixture-actor").outcome,
            "READ_VERIFIED",
        )

    def test_digest_detects_changed_record_after_privileged_bypass(self):
        receipt_id = self.read_receipt()
        trigger_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='local_audit_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER local_audit_no_update")
        self.connection.execute(
            "UPDATE local_audit_receipts SET evidence_digest=? WHERE receipt_id=?",
            ("c" * 64, receipt_id),
        )
        self.connection.commit()
        self.connection.execute(trigger_sql)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_INTEGRITY_FAILED$"):
            self.audit.get_for_actor(receipt_id, actor_id="fixture-actor")

    def test_concurrent_connections_commit_unique_receipts(self):
        def write(index):
            connection = sqlite3.connect(self.path, timeout=10)
            try:
                store = SQLiteAudit(connection)
                receipt = store.append(
                    actor_id="fixture-actor",
                    action="get_profile",
                    outcome="READ_REQUESTED",
                )
                return receipt
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(write, range(24)))
        self.assertEqual(len(set(ids)), 24)
        for receipt in ids:
            self.assertIsNotNone(
                self.audit.get_for_actor(receipt, actor_id="fixture-actor")
            )

    def test_failed_insert_returns_no_receipt_and_rolls_back(self):
        self.connection.execute(
            """CREATE TRIGGER simulate_full BEFORE INSERT ON local_audit_receipts
            BEGIN SELECT RAISE(ABORT, 'secret simulated storage failure'); END"""
        )
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            self.read_receipt()
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM local_audit_receipts"
            ).fetchone()[0],
            0,
        )

    def test_silently_ignored_insert_never_acknowledges_a_receipt(self):
        self.connection.execute(
            """CREATE TRIGGER silently_ignore BEFORE INSERT ON local_audit_receipts
            BEGIN SELECT RAISE(IGNORE); END"""
        )
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            self.read_receipt()
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM local_audit_receipts"
            ).fetchone()[0],
            0,
        )

    def test_after_insert_delete_or_change_never_acknowledges_a_receipt(self):
        # A privileged schema change can retain required trigger names while
        # bypassing their behavior. Verify actual row content, not just names.
        for effect in ("delete", "change"):
            with self.subTest(effect=effect):
                guard = (
                    "local_audit_no_delete"
                    if effect == "delete"
                    else "local_audit_no_update"
                )
                guard_sql = self.connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (guard,)
                ).fetchone()[0]
                self.connection.execute("DROP TRIGGER " + guard)
                event = "DELETE" if effect == "delete" else "UPDATE"
                self.connection.execute(
                    "CREATE TRIGGER "
                    + guard
                    + " BEFORE "
                    + event
                    + " ON local_audit_receipts BEGIN SELECT 1; END"
                )
                statement = (
                    "DELETE FROM local_audit_receipts WHERE receipt_id=NEW.receipt_id;"
                    if effect == "delete"
                    else "UPDATE local_audit_receipts SET scope_digest='"
                    + "c" * 64
                    + "' WHERE receipt_id=NEW.receipt_id;"
                )
                self.connection.execute(
                    "CREATE TRIGGER corrupt_receipt AFTER INSERT "
                    "ON local_audit_receipts "
                    "BEGIN " + statement + " END"
                )
                with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
                    self.read_receipt()
                self.assertFalse(self.connection.in_transaction)
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM local_audit_receipts"
                    ).fetchone()[0],
                    0,
                )
                self.connection.execute("DROP TRIGGER corrupt_receipt")
                self.connection.execute("DROP TRIGGER " + guard)
                self.connection.execute(guard_sql)

    def test_commit_failure_does_not_acknowledge_or_leave_partial_receipt(self):
        class FailCommit(sqlite3.Connection):
            fail = False

            def commit(self):
                if self.fail:
                    raise sqlite3.OperationalError("sensitive simulated commit failure")
                super().commit()

        connection = sqlite3.connect(
            Path(self.directory.name) / "commit-failure.sqlite", factory=FailCommit
        )
        self.addCleanup(connection.close)
        initialize_local_audit(connection)
        store = SQLiteAudit(connection)
        connection.fail = True
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            self.read_receipt(store)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM local_audit_receipts").fetchone()[
                0
            ],
            0,
        )

    def test_attached_store_invalid_version_and_memory_journal_rejected(self):
        self.connection.execute("ATTACH DATABASE ':memory:' AS other_store")
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_LOCAL_FILE_REQUIRED$"):
            self.read_receipt()
        self.connection.execute("DETACH DATABASE other_store")
        self.connection.execute("DELETE FROM local_audit_version")
        self.connection.commit()
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_SCHEMA_INVALID$"):
            SQLiteAudit(self.connection)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_SCHEMA_INVALID$"):
            initialize_local_audit(self.connection)
        self.connection.execute("INSERT INTO local_audit_version VALUES(1)")
        self.connection.commit()
        self.connection.execute("PRAGMA journal_mode=MEMORY")
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_DURABILITY_REQUIRED$"):
            initialize_local_audit(self.connection)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_DURABILITY_REQUIRED$"):
            self.read_receipt()

    def test_busy_or_readonly_database_fails_closed(self):
        locked = sqlite3.connect(self.path)
        self.addCleanup(locked.close)
        self.connection.execute("PRAGMA busy_timeout=1")
        locked.execute("BEGIN IMMEDIATE")
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            self.read_receipt()
        locked.rollback()
        readonly = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.addCleanup(readonly.close)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            self.read_receipt(SQLiteAudit(readonly))

    def test_no_ambient_transaction_commit_or_rollback(self):
        self.connection.execute("BEGIN")
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_TRANSACTION_ACTIVE$"):
            self.read_receipt()
        self.assertTrue(self.connection.in_transaction)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_TRANSACTION_ACTIVE$"):
            initialize_local_audit(self.connection)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

    def test_memory_uninitialized_unrelated_and_nondurable_stores_rejected(self):
        memory = sqlite3.connect(":memory:")
        self.addCleanup(memory.close)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_LOCAL_FILE_REQUIRED$"):
            initialize_local_audit(memory)
        fresh = sqlite3.connect(Path(self.directory.name) / "uninitialized.sqlite")
        self.addCleanup(fresh.close)
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            SQLiteAudit(fresh)
        self.assertEqual(fresh.execute("SELECT name FROM sqlite_master").fetchall(), [])
        fresh.execute("CREATE TABLE business_data(id INTEGER)")
        with self.assertRaisesRegex(
            AuditStorageError, "^AUDIT_DEDICATED_STORE_REQUIRED$"
        ):
            initialize_local_audit(fresh)
        self.connection.execute("PRAGMA synchronous=OFF")
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_DURABILITY_REQUIRED$"):
            self.read_receipt()

    def test_schema_removed_or_connection_closed_sanitized(self):
        self.connection.execute("DROP TRIGGER local_audit_no_delete")
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_SCHEMA_INVALID$"):
            self.read_receipt()
        self.connection.close()
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_UNAVAILABLE$"):
            self.read_receipt()

    def test_validation_does_not_persist_arbitrary_text_or_unbound_success(self):
        for values in (
            {
                "actor_id": "email@example.invalid",
                "action": "get_profile",
                "outcome": "FORBIDDEN",
            },
            {
                "actor_id": "fixture-actor",
                "action": "private payload",
                "outcome": "FORBIDDEN",
            },
            {
                "actor_id": "fixture-actor",
                "action": "get_profile",
                "outcome": "private error",
            },
            {
                "actor_id": "fixture-actor",
                "action": "get_profile",
                "outcome": "READ_VERIFIED",
            },
        ):
            with self.assertRaisesRegex(AuditStorageError, "^AUDIT_INVALID_RECORD$"):
                self.audit.append(**values)
        for value in ("report contents", "F" * 64, None, {}, "a" * 63):
            with self.assertRaisesRegex(AuditStorageError, "^AUDIT_INVALID_RECORD$"):
                self.audit.append_read(
                    actor_id="fixture-actor",
                    action="get_profile",
                    evidence_digest=value,
                    scope_digest="a" * 64,
                )
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_INVALID_RECORD$"):
            self.audit.get_for_actor("' OR 1=1--", actor_id="fixture-actor")
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM local_audit_receipts"
            ).fetchone()[0],
            0,
        )

    def test_explicit_initialization_is_repeatable_and_anonymous_audit_not_exposed(
        self,
    ):
        initialize_local_audit(self.connection)
        receipt = self.audit.append(
            actor_id=None, action="mcp.tools.call", outcome="UNAUTHENTICATED"
        )
        self.assertIsNone(self.audit.get_for_actor(receipt, actor_id="fixture-actor"))
        with self.assertRaisesRegex(AuditStorageError, "^AUDIT_INVALID_RECORD$"):
            self.audit.get_for_actor(receipt, actor_id=None)


if __name__ == "__main__":
    unittest.main()
