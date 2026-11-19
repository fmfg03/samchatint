-- ISOLATED transport-store candidate only. SQLite, NOT PostgreSQL business DDL.
-- Explicit owner-run on a new private test store; never startup/provision accounts.
BEGIN IMMEDIATE;
CREATE TABLE transport_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 actor_hash TEXT, action TEXT NOT NULL, outcome TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE TRIGGER transport_audit_no_update BEFORE UPDATE ON transport_audit
BEGIN SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
CREATE TRIGGER transport_audit_no_delete BEFORE DELETE ON transport_audit
BEGIN SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
COMMIT;
