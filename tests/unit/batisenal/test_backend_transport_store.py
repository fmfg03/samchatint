"""Transport audit is separate from PostgreSQL business commit semantics."""

import sqlite3
from pathlib import Path

import pytest

from samchat.batisenal_transport.contracts import ConnectionResolver
from samchat.batisenal_transport.store import Store, digest


def test_private_audit_is_minimal_and_preserves_case_operation_kind(tmp_path):
    path = tmp_path / "audit.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            Path(
                "database/migrations/20261023_batisenal_transport_sqlite.sql"
            ).read_text()
        )
    store = Store(path, lambda: 42)
    assert (
        store.append(
            actor_id="private-actor",
            action="samchat_batisenal_submit_case_draft",
            outcome="operation_complete",
        )
        == "1"
    )
    store.append(
        actor_id=None, action="arbitrary private input", outcome="private error"
    )
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT actor_hash,action,outcome,created_at FROM transport_audit ORDER"
            " BY id"
        ).fetchall()
    assert rows == [
        (
            digest("private-actor"),
            "samchat_batisenal_submit_case_draft",
            "operation_complete",
            42,
        ),
        (None, "unknown", "DENIED", 42),
    ]
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            store._audit(db, "private-actor", "ping", "OK")
            raise RuntimeError("failed write")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM transport_audit").fetchone()[0] == 2
    assert not hasattr(store, "subscribe") and not hasattr(store, "enqueue")
    assert ConnectionResolver.__name__ == "ConnectionResolver"


def test_store_never_provisions_missing_schema(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        Store(tmp_path / "absent.sqlite", lambda: 0).append(
            actor_id=None, action="ping", outcome="OK"
        )
    assert not (tmp_path / "absent.sqlite").exists()
