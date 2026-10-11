"""Explicitly synthetic database and trusted principal for offline acceptance.

There are no names, production identifiers, secrets, or database credentials here.
The fixture reference is deliberately unrelated to the real Operations 332 case.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Callable

from .contracts import Admission, MemoryAudit, Principal, utc_now
from .gateway import Gateway, Sources
from .repository import Repository

BASE_COMMIT = "2cb5397769fa1f7532ec495a68a19cbc4a6778c7"
TOURNAMENT = "10000000-0000-4000-8000-000000000001"
OTHER_TOURNAMENT = "10000000-0000-4000-8000-000000000002"
DOCUMENT = "20000000-0000-4000-8000-000000000001"
OTHER_DOCUMENT = "20000000-0000-4000-8000-000000000002"
POLICY = "synthetic-route-v1"


def fixture(
    configure: Callable[[sqlite3.Connection], None] | None = None,
) -> tuple[Gateway, Principal, sqlite3.Connection]:
    """Create an in-memory fixture; no live URL, environment or filesystem IO."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript(
        """
        CREATE TABLE document_status (
            installation_id TEXT NOT NULL,reader_binding TEXT NOT NULL,
            tournament_id TEXT NOT NULL,document_id TEXT NOT NULL,
            kind TEXT NOT NULL,state TEXT NOT NULL,reference_operations TEXT,
            reference_samchat TEXT,reference_base TEXT,created_at TEXT NOT NULL,
            submitted_at TEXT,currency TEXT NOT NULL,amount NUMERIC,
            active_expenses INTEGER,classification_present INTEGER,
            authority_coverage TEXT,
            PRIMARY KEY(installation_id,reader_binding,document_id)
        );
        CREATE TABLE document_route (
            installation_id TEXT,reader_binding TEXT,tournament_id TEXT,
            document_id TEXT,approver_alias TEXT,actor_can_decide INTEGER,
            resolution TEXT,policy_version TEXT
        );
        CREATE TABLE document_audit (
            installation_id TEXT,reader_binding TEXT,tournament_id TEXT,
            document_id TEXT,event_id TEXT,occurred_at TEXT,
            action TEXT,actor_alias TEXT
        );
        CREATE TABLE forbidden (password_hash TEXT);
        CREATE TABLE expense_summary (
            installation_id TEXT,reader_binding TEXT,tournament_id TEXT,
            document_id TEXT,expense_count INTEGER
        );
        CREATE TABLE budget_availability (
            installation_id TEXT,reader_binding TEXT,tournament_id TEXT,
            version_id TEXT,period_start TEXT,period_end TEXT,currency TEXT,
            approved_amount NUMERIC,consumed_amount NUMERIC,
            committed_amount NUMERIC,available_amount NUMERIC,coverage TEXT
        );
        INSERT INTO forbidden VALUES ('synthetic-sentinel-not-a-secret');
        """
    )
    for tournament, document, installation, state in (
        (TOURNAMENT, DOCUMENT, "fixture-installation", "control_presupuestal"),
        (OTHER_TOURNAMENT, OTHER_DOCUMENT, "other-installation", "enviado"),
    ):
        db.execute(
            "INSERT INTO document_status VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                installation,
                "fixture-reader",
                tournament,
                document,
                "INFORME",
                state,
                "SYNTH-001",
                "I-SYNTH-001",
                "C-SYNTH-001",
                "2026-01-02T00:00:00Z",
                None,
                "MXN",
                "120.50",
                2,
                0,
                "complete",
            ),
        )
        db.execute(
            "INSERT INTO document_route VALUES (?,?,?,?,?,?,?,?)",
            (
                installation,
                "fixture-reader",
                tournament,
                document,
                "approver-A",
                0,
                "resolved",
                POLICY,
            ),
        )
        for number in range(3):
            db.execute(
                "INSERT INTO document_audit VALUES (?,?,?,?,?,?,?,?)",
                (
                    installation,
                    "fixture-reader",
                    tournament,
                    document,
                    f"event-{number}",
                    f"2026-01-0{number + 2}T00:00:00Z",
                    "submit" if number else "create",
                    "actor-A",
                ),
            )
    if configure:
        configure(db)
    db.commit()
    repository = Repository(db)
    sources = Sources(
        {
            "fixture/state_guard.py": b"# Synthetic state guard example only\n"
            b"def allowed(state):\n    return state == 'enviado'\n"
        },
        BASE_COMMIT,
    )
    admission = Admission(
        "synthetic",
        "fixture-installation",
        repository.schema_fingerprint,
        BASE_COMMIT,
        POLICY,
    )
    actor = Principal(
        "fixture-reader",
        "fixture-installation",
        frozenset({TOURNAMENT}),
        utc_now() + timedelta(hours=1),
        "fixture-generation-1",
    )
    return Gateway(admission, repository, MemoryAudit(), sources), actor, db
