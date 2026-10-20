"""Offline synthetic fixtures only. No application imports, env or network."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from samchat.private_plugin.catalog import advertised_tools, operations
from samchat.private_plugin.contracts import Approval, Attempt, Denied, Identity, Target
from samchat.private_plugin.perimeter import Perimeter, confirmation_binding, digest
from samchat.private_plugin.registration import review_summary, validate_file_envelope
from samchat.private_plugin.retries import (
    MutationRecord,
    reservation_key,
    verified_replay,
)


class Identities:
    def __init__(self, identity):
        self.identity = identity
        self.calls = 0

    def resolve_current(self, reference):
        self.calls += 1
        if reference != "fixture-connection":
            raise Denied("UNAUTHENTICATED")
        return self.identity


class Policy:
    """Fixture of canonical access decision, not SamChat authorization logic."""

    def __init__(self):
        self.allowed = True
        self.targets = []

    def authorize_current(self, identity, attempt):
        self.targets.append(attempt.target)
        return self.allowed and attempt.target.object_id == "fixture-document"


class Approvals:
    def __init__(self):
        self.record = None

    def read_for_actor(self, reference, identity):
        return self.record if reference == "fixture-human-approval" else None


class LocalAudit:
    """TEST ONLY SQLite proves local durability, NOT productive persistence."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS attempts "
            "(id INTEGER PRIMARY KEY, actor TEXT, action TEXT, outcome TEXT)"
        )

    def append(self, *, actor_id, action, outcome):
        with self.db:
            cur = self.db.execute(
                "INSERT INTO attempts(actor,action,outcome) VALUES (?,?,?)",
                (actor_id, action, outcome),
            )
        return str(cur.lastrowid)


class PerimeterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "fixture.sqlite"
        self.audit = LocalAudit(self.path)
        self.identity = Identity(
            "fixture-actor",
            "fixture-installation",
            "fixture-org",
            "fixture-grant",
            200,
            "https://issuer.invalid",
            "https://mcp.invalid",
            frozenset(o.scope for o in operations()),
            True,
            False,
        )
        self.ids, self.policy, self.approvals = (
            Identities(self.identity),
            Policy(),
            Approvals(),
        )
        self.gate = Perimeter(
            self.ids,
            self.policy,
            self.approvals,
            self.audit,
            issuer=self.identity.issuer,
            audience=self.identity.audience,
            installation_id=self.identity.installation_id,
        )
        self.attempt = Attempt(
            "receipts.send_document",
            Target(
                "fixture-document",
                "fixture-org",
                "legal-A",
                "portfolio-A",
                "tournament-A",
                "v1",
            ),
            digest({"fixture": "synthetic"}),
            "draft-A",
            10000,
            "MXN",
            "destination-A",
            "retry-A",
            "fixture-human-approval",
        )
        self.approvals.record = Approval(
            self.identity.actor_id,
            self.identity.grant_id,
            confirmation_binding(self.identity, self.attempt),
            200,
        )

    def tearDown(self):
        self.audit.db.close()
        self.tmp.cleanup()

    def evaluate(self, attempt=None):
        result = self.gate.evaluate(
            "fixture-connection", attempt or self.attempt, now=100
        )
        self.assertFalse(result.invoked)
        return result.code

    def test_valid_request_still_disabled(self):
        self.assertEqual(self.evaluate(), "CANONICAL_SCOPE_UNPROVEN")
        self.assertEqual(advertised_tools(), [])

    def test_expired_revoked_inactive_wrong_issuer_audience_installation(self):
        for fields in (
            {"expires_at": 100},
            {"revoked": True},
            {"active": False},
            {"issuer": "https://wrong.invalid"},
            {"audience": "https://wrong.invalid"},
            {"installation_id": "other"},
            {"actor_id": ""},
            {"grant_id": ""},
            {"active": "false"},
            {"revoked": 0},
            {"expires_at": 200.0},
        ):
            with self.subTest(fields=fields):
                self.ids.identity = replace(self.identity, **fields)
                self.assertEqual(self.evaluate(), "UNAUTHENTICATED")

    def test_current_permission_revocation_is_rechecked(self):
        self.assertEqual(self.evaluate(), "CANONICAL_SCOPE_UNPROVEN")
        self.policy.allowed = False
        self.assertEqual(self.evaluate(), "FORBIDDEN")
        self.assertEqual(self.ids.calls, 2)

    def test_malformed_policy_result_does_not_grant_access(self):
        for value in ("true", 1, {"allowed": True}, None):
            self.policy.authorize_current = lambda *_: value
            self.assertEqual(self.evaluate(), "FORBIDDEN")

    def test_company_object_and_token_scopes_deny(self):
        self.assertEqual(
            self.evaluate(
                replace(
                    self.attempt,
                    target=replace(self.attempt.target, organization_id="other"),
                )
            ),
            "FORBIDDEN",
        )
        self.assertEqual(
            self.evaluate(
                replace(
                    self.attempt, target=replace(self.attempt.target, object_id="other")
                )
            ),
            "FORBIDDEN",
        )
        self.ids.identity = replace(self.identity, oauth_scopes=frozenset())
        self.assertEqual(self.evaluate(), "FORBIDDEN")

    def test_every_scope_dimension_reaches_canonical_policy(self):
        self.evaluate()
        self.assertEqual(self.policy.targets, [self.attempt.target])
        self.policy.allowed = (
            False  # role/legal-entity/portfolio/tournament owner denial
        )
        for field in ("legal_entity_id", "portfolio_id", "tournament_id"):
            self.assertEqual(
                self.evaluate(
                    replace(
                        self.attempt,
                        target=replace(self.attempt.target, **{field: "denied"}),
                    )
                ),
                "FORBIDDEN",
            )

    def test_confirmation_binds_each_business_field(self):
        for fields in (
            {"draft_id": "draft-B"},
            {"amount_minor": 20000},
            {"currency": "USD"},
            {"destination_id": "destination-B"},
            {"payload_digest": digest({"new": True})},
            {"target": replace(self.attempt.target, version="v2")},
            {"target": replace(self.attempt.target, legal_entity_id="legal-B")},
            {"target": replace(self.attempt.target, tournament_id="tournament-B")},
            {"target": replace(self.attempt.target, portfolio_id="portfolio-B")},
            {"action": "receipts.approve_document"},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(
                    self.evaluate(replace(self.attempt, **fields)),
                    "CONFIRMATION_MISMATCH",
                )

    def test_confirmation_expired_consumed_other_actor_grant_missing(self):
        original = self.approvals.record
        for fields in (
            {"expires_at": 100},
            {"consumed": True},
            {"actor_id": "other"},
            {"grant_id": "other"},
        ):
            self.approvals.record = replace(original, **fields)
            self.assertEqual(self.evaluate(), "CONFIRMATION_MISMATCH")
        self.approvals.record = None
        self.assertEqual(self.evaluate(), "CONFIRMATION_MISMATCH")
        self.assertEqual(
            self.evaluate(replace(self.attempt, confirmation_ref="")),
            "CONFIRMATION_REQUIRED",
        )
        self.assertEqual(
            self.evaluate(replace(self.attempt, draft_id="")), "CONFIRMATION_REQUIRED"
        )
        self.assertEqual(
            self.evaluate(replace(self.attempt, idempotency_key="")),
            "IDEMPOTENCY_REQUIRED",
        )

    def test_read_has_no_business_write_or_approval(self):
        class NoApproval:
            def read_for_actor(self, *args):
                raise AssertionError("read touched approval store")

        self.gate.approvals = NoApproval()
        self.assertEqual(
            self.evaluate(
                replace(
                    self.attempt,
                    action="expense.full_workflow_snapshot",
                    confirmation_ref="",
                    idempotency_key="",
                )
            ),
            "CANONICAL_SCOPE_UNPROVEN",
        )
        # The only write is a minimal security audit, never business state.
        self.assertEqual(
            self.audit.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1
        )

    def test_cross_user_isolation(self):
        self.ids.identity = replace(self.identity, actor_id="other-actor")
        self.assertEqual(self.evaluate(), "CONFIRMATION_MISMATCH")
        self.ids.identity = replace(self.identity, grant_id="reconnected-grant")
        self.assertEqual(self.evaluate(), "CONFIRMATION_MISMATCH")

    def test_unregistered_sql_ssh_dispatch_and_legacy_are_denied(self):
        for name in (
            "sql",
            "ssh",
            "execute_canonical_action",
            "tournament_team_register_from_roster",
            "eval",
        ):
            self.assertEqual(
                self.evaluate(replace(self.attempt, action=name)),
                "ACTION_NOT_REGISTERED",
            )

    def test_local_receipt_survives_reopen_without_payload_or_pii(self):
        self.evaluate()
        self.audit.db.close()
        self.audit = LocalAudit(self.path)
        row = self.audit.db.execute("SELECT * FROM attempts").fetchone()
        self.assertEqual(
            row[2:], ("receipts.send_document", "CANONICAL_SCOPE_UNPROVEN")
        )
        raw = self.path.read_bytes()
        for excluded in (
            b"destination-A",
            b"fixture-human-approval",
            b"fixture-grant",
            b"fixture-document",
        ):
            self.assertNotIn(excluded, raw)

    def test_dependency_errors_are_sanitized(self):
        def broken(*args):
            raise RuntimeError("private content never emit")

        self.ids.resolve_current = broken
        self.assertEqual(self.evaluate(), "DEPENDENCY_UNAVAILABLE")
        self.assertNotIn(
            "private content",
            str(self.audit.db.execute("SELECT * FROM attempts").fetchall()),
        )
        self.audit.append = broken
        self.assertEqual(self.evaluate(), "AUDIT_UNAVAILABLE")

    def test_invalid_binding_and_missing_target(self):
        for fields, code in (
            ({"payload_digest": "bad"}, "INVALID_BINDING"),
            ({"amount_minor": 1.1}, "INVALID_AMOUNT"),
            ({"target": replace(self.attempt.target, version="")}, "TARGET_REQUIRED"),
        ):
            self.assertEqual(self.evaluate(replace(self.attempt, **fields)), code)
        with self.assertRaises(Denied):
            digest({"money": float("nan")})
        with self.assertRaises(Denied):
            digest({"secret": b"bytes"})

    def test_exact_replay_conflict_and_crash_contract(self):
        record = MutationRecord(
            reservation_key(self.identity, self.attempt),
            confirmation_binding(self.identity, self.attempt),
            "verified",
            "receipt-1",
        )
        for _ in range(3):
            self.assertEqual(
                verified_replay(record, self.identity, self.attempt), "receipt-1"
            )
        for attempt in (
            replace(self.attempt, amount_minor=42),
            replace(self.attempt, idempotency_key="other"),
        ):
            with self.assertRaisesRegex(Denied, "IDEMPOTENCY_CONFLICT"):
                verified_replay(record, self.identity, attempt)
        with self.assertRaisesRegex(Denied, "IDEMPOTENCY_CONFLICT"):
            verified_replay(
                record, replace(self.identity, actor_id="other"), self.attempt
            )
        for state in ("pending", "unknown", "failed"):
            with self.assertRaisesRegex(Denied, "RECONCILIATION_REQUIRED"):
                verified_replay(
                    replace(record, state=state), self.identity, self.attempt
                )
        with self.assertRaisesRegex(Denied, "IDEMPOTENCY_REQUIRED"):
            reservation_key(self.identity, replace(self.attempt, idempotency_key=""))

    def test_all_candidates_remain_disabled_and_registration_commit_never_captures(
        self,
    ):
        for operation in operations():
            attempt = replace(self.attempt, action=operation.action)
            self.approvals.record = replace(
                self.approvals.record,
                binding_digest=confirmation_binding(self.identity, attempt),
            )
            self.assertEqual(self.evaluate(attempt), "CANONICAL_SCOPE_UNPROVEN")
        self.assertEqual(
            self.audit.db.execute("SELECT count(*) FROM attempts").fetchone()[0],
            len(operations()),
        )


class RegistrationTest(unittest.TestCase):
    def test_missing_and_duplicate_canonical_issues_remain_blocking_and_minimized(self):
        data = {
            "ready_to_commit": True,
            "blockers": [
                {"code": "MISSING_BIRTH_DATE", "message": "synthetic child"},
                {"code": "DUPLICATE_PHOTO"},
            ],
            "warnings": [],
        }
        result = review_summary(data, version="v1")
        self.assertEqual(result["blocking_issues"], 2)
        self.assertFalse(result["ready_to_commit"])
        self.assertFalse(result["commit_enabled"])
        self.assertNotIn("synthetic child", json.dumps(result))
        self.assertNotIn("2012-01-01", json.dumps(result))

    def test_incomplete_review_is_not_ready(self):
        for data in ({}, {"ready_to_commit": "true", "blockers": [], "warnings": []}):
            with self.assertRaises(Denied):
                review_summary(data, version="v1")
        self.assertTrue(
            review_summary(
                {"ready_to_commit": True, "blockers": [], "warnings": []}, version="v1"
            )["ready_to_commit"]
        )

    def test_invalid_empty_oversized_mismatched_and_valid_file_envelopes(self):
        for content, mime, limit in [
            (b"", "application/pdf", 20),
            (b"x" * 21, "application/pdf", 20),
            (b"PKzip", "application/pdf", 20),
            (b"%PDF-1", "text/html", 20),
            (b"%PDF-1", "application/pdf", 0),
        ]:
            with self.assertRaises(Denied):
                validate_file_envelope(content, mime, maximum_bytes=limit)
        for content, mime in [
            (b"%PDF-1 synthetic", "application/pdf"),
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"\xff\xd8\xff", "image/jpeg"),
        ]:
            validate_file_envelope(content, mime, maximum_bytes=100)


if __name__ == "__main__":
    unittest.main()
