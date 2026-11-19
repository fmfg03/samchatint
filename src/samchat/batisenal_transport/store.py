"""Explicitly provisioned transport-only SQLite store; never business data."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ACTIONS = {
    "case_v1",
    "samchat_batisenal_get_case_context",
    "samchat_batisenal_submit_case_draft",
    "samchat_batisenal_get_case_operation",
    "initialize",
    "ping",
    "tools/list",
    "server/discover",
    "profile",
    "samchat_batisenal_list_pending",
    "samchat_batisenal_read_case",
    "samchat_batisenal_record_activity",
    "samchat_batisenal_prepare_reply",
    "samchat_batisenal_submit_draft",
}
OUTCOMES = {
    "operation_complete",
    "OK",
    "authorized_attempt",
    "read_complete",
    "scoped_attempt",
    "UNAUTHENTICATED",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Store:
    """Opening never creates schema. Provision SQL explicitly in a private store."""

    def __init__(self, path, clock):
        self.path, self.clock = Path(path).resolve(), clock

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _audit(self, db, actor, action, outcome):
        cur = db.execute(
            "INSERT INTO transport_audit(actor_hash,action,outcome,created_at)"
            " VALUES(?,?,?,?)",
            (
                digest(actor) if actor else None,
                action if action in ACTIONS else "unknown",
                outcome if outcome in OUTCOMES else "DENIED",
                int(self.clock()),
            ),
        )
        return str(cur.lastrowid)

    def append(self, *, actor_id, action, outcome):
        with self.transaction() as db:
            return self._audit(db, actor_id, action, outcome)
