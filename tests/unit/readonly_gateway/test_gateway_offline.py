"""Offline acceptance: actual SQLite reads and stdio MCP, no app bootstrap."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from samchat.readonly_gateway.contracts import Admission, ReadError, utc_now
from samchat.readonly_gateway.fixtures import (
    DOCUMENT,
    OTHER_DOCUMENT,
    OTHER_TOURNAMENT,
    TOURNAMENT,
    fixture,
)
from samchat.readonly_gateway.gateway import Sources
from samchat.readonly_gateway.mcp import Connector, serve


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway, self.actor, self.db = fixture()

    def tearDown(self) -> None:
        self.db.close()

    def call(self, name: str, **args: object) -> dict:
        return self.gateway.call("samchat." + name, args, self.actor)["data"]

    def error(self, code: str, callback: object) -> None:
        with self.assertRaises(ReadError) as raised:
            callback()  # type: ignore[operator]
        self.assertEqual(raised.exception.code, code)

    def test_reference_status_diagnosis_and_audit(self) -> None:
        resolved = self.call(
            "resolve_reference", reference_kind="operations", reference="SYNTH-001"
        )
        self.assertEqual(resolved["resolution"], "unique")
        self.assertEqual(resolved["candidates"][0]["document_id"], DOCUMENT)
        status = self.call("document_status", document_id=DOCUMENT)
        self.assertEqual(status["document"]["state"], "control_presupuestal")
        self.assertFalse(status["authority"]["actor_can_decide"])
        result = self.call("document_diagnose", document_id=DOCUMENT, intent="approve")
        self.assertEqual(result["diagnosis"]["state_blocker"], "invalid_estado")
        self.assertEqual(
            result["diagnosis"]["first_workflow_blocker"], "invalid_estado"
        )
        self.assertEqual(result["diagnosis"]["approval_eligibility"], "unknown")
        audit = self.call("document_audit", document_id=DOCUMENT)
        self.assertEqual(len(audit["rows"]), 3)
        self.assertNotIn("comment", json.dumps(audit))

    def test_none_or_expired_principal_prevents_query(self) -> None:
        for actor in (
            None,
            replace(self.actor, expires_at=utc_now() - timedelta(seconds=1)),
        ):
            self.error(
                "AUTH_REQUIRED",
                lambda: self.gateway.call(
                    "samchat.document_status", {"document_id": DOCUMENT}, actor
                ),
            )
        self.assertEqual(self.gateway.repository.query_count, 0)

    def test_installation_and_tournament_are_scoped(self) -> None:
        self.error(
            "SCOPE_UNPROVEN",
            lambda: self.gateway.call(
                "samchat.document_status",
                {"document_id": DOCUMENT},
                replace(self.actor, installation="other"),
            ),
        )
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.call("document_status", document_id=OTHER_DOCUMENT),
        )
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.call(
                "resolve_reference",
                reference_kind="operations",
                reference="SYNTH-001",
                tournament_id=OTHER_TOURNAMENT,
            ),
        )

    def test_identity_in_payload_is_rejected(self) -> None:
        for key in ("actor_id", "tenant_id", "roles", "permissions", "sql"):
            self.error(
                "INVALID_ARGUMENTS",
                lambda: self.call(
                    "document_status", document_id=DOCUMENT, **{key: "forged"}
                ),
            )
        self.assertEqual(self.gateway.repository.query_count, 0)

    def test_default_and_production_admission_are_disabled(self) -> None:
        for admission in (
            Admission(),
            replace(self.gateway.admission, mode="production"),
        ):
            self.gateway.admission = admission
            self.error(
                "ACTIVATION_REQUIRED",
                lambda: self.call("document_status", document_id=DOCUMENT),
            )
        self.assertEqual(self.gateway.repository.query_count, 0)

    def test_schema_and_source_version_mismatch(self) -> None:
        original = self.gateway.admission
        self.gateway.admission = replace(original, schema_fingerprint="wrong")
        self.error(
            "SCHEMA_MISMATCH",
            lambda: self.call("document_status", document_id=DOCUMENT),
        )
        self.gateway.admission = replace(original, source_commit="a" * 40)
        self.error("SOURCE_VERSION_UNPROVEN", lambda: self.call("source_info"))

    def test_mutating_action_and_unknown_are_never_dispatched(self) -> None:
        for name in ("transfer.create_draft", "approve", "query_sql"):
            self.error(
                "ACTION_NOT_REGISTERED", lambda: self.gateway.call(name, {}, self.actor)
            )
        self.assertEqual(self.gateway.repository.query_count, 0)

    def test_sql_writer_and_forbidden_table_denied_by_database(self) -> None:
        for sql in (
            "UPDATE document_status SET state='aprobado'",
            "SELECT * FROM forbidden",
            "CREATE TABLE malicious(x)",
            "PRAGMA query_only=OFF",
            "SELECT load_extension('x')",
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                self.db.execute(sql)
        self.assertEqual(
            self.call("document_status", document_id=DOCUMENT)["document"]["state"],
            "control_presupuestal",
        )

    def test_reference_injection_is_literal(self) -> None:
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.call(
                "resolve_reference",
                reference_kind="operations",
                reference="' OR 1=1 --",
            ),
        )

    def test_audit_failure_before_query(self) -> None:
        class Broken:
            def append(self, event: object) -> None:
                raise OSError("sensitive-backend-path")

        self.gateway.audit = Broken()
        self.error(
            "AUDIT_UNAVAILABLE",
            lambda: self.call("document_status", document_id=DOCUMENT),
        )
        self.assertEqual(self.gateway.repository.query_count, 0)

    def test_audit_failure_after_query_denies_result(self) -> None:
        class BrokenAfterAttempt:
            def append(self, event: dict) -> None:
                if event["phase"] == "complete":
                    raise OSError("private")

        self.gateway.audit = BrokenAfterAttempt()
        self.error(
            "AUDIT_UNAVAILABLE",
            lambda: self.call("document_status", document_id=DOCUMENT),
        )

    def test_audit_does_not_store_response_or_selector(self) -> None:
        self.call("document_status", document_id=DOCUMENT)
        events = json.dumps(self.gateway.audit.events)  # type: ignore[attr-defined]
        self.assertNotIn(DOCUMENT, events)
        self.assertNotIn("control_presupuestal", events)
        self.assertNotIn(self.actor.actor_binding, events)

    def test_keyset_pages_and_cross_actor_cursor(self) -> None:
        first = self.call("document_audit", document_id=DOCUMENT, page_size=1)
        second = self.call(
            "document_audit",
            document_id=DOCUMENT,
            page_size=1,
            cursor=first["next_cursor"],
        )
        self.assertNotEqual(first["rows"][0]["event_id"], second["rows"][0]["event_id"])
        self.error(
            "INVALID_CURSOR",
            lambda: self.gateway.call(
                "samchat.document_audit",
                {
                    "document_id": DOCUMENT,
                    "page_size": 1,
                    "cursor": first["next_cursor"],
                },
                replace(self.actor, generation="different"),
            ),
        )

    def test_tampered_and_filter_changed_cursor(self) -> None:
        first = self.call("document_audit", document_id=DOCUMENT, page_size=1)
        self.error(
            "INVALID_CURSOR",
            lambda: self.call(
                "document_audit",
                document_id=DOCUMENT,
                page_size=2,
                cursor=first["next_cursor"],
            ),
        )
        self.error(
            "INVALID_CURSOR",
            lambda: self.call(
                "document_audit", document_id=DOCUMENT, page_size=1, cursor="broken"
            ),
        )

    def test_revocation_prevents_more_pages(self) -> None:
        first = self.call("document_audit", document_id=DOCUMENT, page_size=1)
        self.gateway.revoke(self.actor.actor_binding)
        self.error(
            "AUTH_REQUIRED",
            lambda: self.call(
                "document_audit",
                document_id=DOCUMENT,
                page_size=1,
                cursor=first["next_cursor"],
            ),
        )

    def test_grouped_and_list_study(self) -> None:
        kwargs = {"date_from": "2026-01-01", "date_to": "2026-02-01"}
        study = self.call("study", group_by="state", **kwargs)
        self.assertEqual(study["groups"][0]["documents"], 1)
        self.assertEqual(study["groups"][0]["amount_coverage"], 1)
        self.assertFalse(study["truncated"])
        rows = self.call("study", **kwargs)["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["document_id"], DOCUMENT)

    def test_invalid_dates_pages_and_uuid(self) -> None:
        for start, end in (
            ("bad", "2026-01-01"),
            ("2026-01-01", "2026-01-01"),
            ("2024-01-01", "2026-01-01"),
        ):
            self.error(
                "INVALID_ARGUMENTS",
                lambda: self.call("study", date_from=start, date_to=end),
            )
        for size in (0, 101, True):
            self.error(
                "INVALID_ARGUMENTS",
                lambda: self.call(
                    "document_audit", document_id=DOCUMENT, page_size=size
                ),
            )
        self.error(
            "INVALID_ARGUMENTS",
            lambda: self.call("document_status", document_id="../private"),
        )

    def test_source_hash_and_exact_manifest(self) -> None:
        info = self.call("source_info")
        result = self.call(
            "source_read", source_id=info["source_id"], path="fixture/state_guard.py"
        )
        self.assertEqual(result["sha256"], info["files"][result["path"]]["sha256"])
        self.error(
            "SOURCE_NOT_ADMITTED",
            lambda: self.call(
                "source_read", source_id=info["source_id"], path="../.env"
            ),
        )
        self.error(
            "SOURCE_VERSION_UNPROVEN",
            lambda: self.call(
                "source_read", source_id="wrong", path="fixture/state_guard.py"
            ),
        )

    def test_source_admission_rejects_secret_and_paths(self) -> None:
        for path, body in (
            (".env", b"x"),
            ("../x.py", b"x"),
            ("x.py", b"token = 'sensitive-synthetic-value'"),
            ("x.py", b"-----BEGIN PRIVATE KEY-----"),
            ("x.py", b"\xff"),
            ("x.py", b"x" * 65537),
        ):
            self.error(
                "SOURCE_NOT_ADMITTED",
                lambda: Sources({path: body}, self.gateway.admission.source_commit),
            )

    def test_rate_limit_and_cancel(self) -> None:
        for _ in range(20):
            self.call("source_info")
        self.error("RATE_LIMIT", lambda: self.call("source_info"))
        self.gateway.repository.cancelled = True
        self.error(
            "CANCELLED",
            lambda: self.gateway.repository.select(
                "SELECT COUNT(*) FROM document_status a,document_status b,document_status c,document_status d,document_status e,document_status f",
                {},
            ),
        )

    def test_read_has_no_database_effect(self) -> None:
        changes = self.db.total_changes
        self.call("document_diagnose", document_id=DOCUMENT, intent="approve")
        self.call("document_audit", document_id=DOCUMENT)
        self.call(
            "study", date_from="2026-01-01", date_to="2026-02-01", group_by="month"
        )
        self.assertEqual(self.db.total_changes, changes)

    def test_stdio_e2e_initialize_list_call_unknown_and_disabled(self) -> None:
        connector = Connector(self.gateway, lambda: self.actor)
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "samchat.document_status",
                    "arguments": {"document_id": DOCUMENT},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "transfer.create_draft", "arguments": {}},
            },
        ]
        output = io.StringIO()
        serve(
            connector,
            io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"),
            output,
        )
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses[1]["result"]["tools"]), 7)
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertIsNone(
            responses[2]["result"]["structuredContent"]["deployed_commit"]
        )
        self.assertEqual(responses[3]["error"]["message"], "ACTION_NOT_REGISTERED")
        disabled = Connector(None, lambda: None)
        self.assertEqual(disabled.handle(requests[1])["result"]["tools"], [])
        self.assertEqual(
            disabled.handle(requests[2])["error"]["message"], "ACTIVATION_REQUIRED"
        )

    def test_stdio_bad_json_and_oversize_have_neutral_errors(self) -> None:
        output = io.StringIO()
        serve(
            Connector(None, lambda: None),
            io.StringIO("{broken\n" + "x" * 66000 + "\n"),
            output,
        )
        messages = [
            json.loads(line)["error"]["message"]
            for line in output.getvalue().splitlines()
        ]
        self.assertEqual(messages, ["INVALID_JSON", "INPUT_LIMIT"])

    def rebuild(self, configure: object) -> None:
        self.db.close()
        self.gateway, self.actor, self.db = fixture(configure)  # type: ignore[arg-type]

    def test_enviado_never_claims_approval_eligible(self) -> None:
        self.rebuild(
            lambda db: db.execute("UPDATE document_status SET state='enviado'")
        )
        result = self.call("document_diagnose", document_id=DOCUMENT, intent="approve")[
            "diagnosis"
        ]
        self.assertIsNone(result["state_blocker"])
        self.assertEqual(result["approval_eligibility"], "unknown")

    def test_authority_pending_and_policy_mismatch(self) -> None:
        self.rebuild(
            lambda db: db.execute("UPDATE document_route SET resolution='cycle'")
        )
        result = self.call("document_status", document_id=DOCUMENT)["authority"]
        self.assertEqual(result["resolution"], "pending_assignment")
        self.assertEqual(result["approver_aliases"], [])
        self.rebuild(
            lambda db: db.execute(
                "UPDATE document_route SET policy_version='different'"
            )
        )
        self.error(
            "SOURCE_VERSION_UNPROVEN",
            lambda: self.call("document_status", document_id=DOCUMENT),
        )

    def test_ambiguous_reference_does_not_select_first(self) -> None:
        def configure(db: sqlite3.Connection) -> None:
            db.execute(
                "INSERT INTO document_status SELECT installation_id,reader_binding,tournament_id,?,kind,state,reference_operations,reference_samchat,reference_base,created_at,submitted_at,currency,amount,active_expenses,classification_present,authority_coverage FROM document_status WHERE document_id=?",
                (OTHER_DOCUMENT, DOCUMENT),
            )

        self.rebuild(configure)
        result = self.call(
            "resolve_reference", reference_kind="operations", reference="SYNTH-001"
        )
        self.assertEqual(result["resolution"], "ambiguous")
        self.assertTrue(result["selector_required"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_null_amount_and_separate_currency(self) -> None:
        def configure(db: sqlite3.Connection) -> None:
            db.execute(
                "UPDATE document_status SET amount=NULL WHERE document_id=?",
                (DOCUMENT,),
            )
            db.execute(
                "INSERT INTO document_status SELECT installation_id,reader_binding,tournament_id,?,kind,state,reference_operations,reference_samchat,reference_base,created_at,submitted_at,'USD',10,active_expenses,classification_present,authority_coverage FROM document_status WHERE document_id=?",
                (OTHER_DOCUMENT, DOCUMENT),
            )

        self.rebuild(configure)
        groups = self.call(
            "study", date_from="2026-01-01", date_to="2026-02-01", group_by="state"
        )["groups"]
        self.assertEqual(len(groups), 2)
        self.assertIsNone(groups[0]["known_amount"])
        self.assertEqual(groups[0]["amount_coverage"], 0)

    def test_cursor_expired(self) -> None:
        first = self.call("document_audit", document_id=DOCUMENT, page_size=1)
        now = utc_now()
        self.gateway.clock = lambda: now + timedelta(minutes=11)
        self.error(
            "INVALID_CURSOR",
            lambda: self.call(
                "document_audit",
                document_id=DOCUMENT,
                page_size=1,
                cursor=first["next_cursor"],
            ),
        )

    def test_sources_mapping_is_immutable(self) -> None:
        with self.assertRaises(TypeError):
            self.gateway.sources.files["fixture/state_guard.py"] = b"changed"  # type: ignore[index]

    def test_revocation_during_audit_completion_denies_data(self) -> None:
        gateway, binding = self.gateway, self.actor.actor_binding

        class RevokingAudit:
            def append(self, event: dict) -> None:
                if event.get("decision") == "allow":
                    gateway.revoke(binding)

        self.gateway.audit = RevokingAudit()
        self.error(
            "AUTH_REQUIRED", lambda: self.call("document_status", document_id=DOCUMENT)
        )

    def test_revocation_during_query_is_not_blocked(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = self.gateway.repository.document

        def delayed(actor: object, document_id: str) -> dict:
            entered.set()
            self.assertTrue(release.wait(2))
            return original(actor, document_id)  # type: ignore[arg-type]

        self.gateway.repository.document = delayed  # type: ignore[method-assign]
        errors: list[str] = []

        def run() -> None:
            try:
                self.call("document_status", document_id=DOCUMENT)
            except ReadError as exc:
                errors.append(exc.code)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(2))
        self.gateway.revoke(self.actor.actor_binding)
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["AUTH_REQUIRED"])

    def test_mcp_text_content_contains_result_for_older_clients(self) -> None:
        result = Connector(self.gateway, lambda: self.actor).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "samchat.document_status",
                    "arguments": {"document_id": DOCUMENT},
                },
            }
        )["result"]
        self.assertEqual(
            json.loads(result["content"][0]["text"]), result["structuredContent"]
        )

    def test_budget_projection_requires_exact_visible_version(self) -> None:
        version = "30000000-0000-4000-8000-000000000001"

        def configure(db: sqlite3.Connection) -> None:
            db.execute(
                "INSERT INTO budget_availability VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "fixture-installation",
                    "fixture-reader",
                    TOURNAMENT,
                    version,
                    "2026-01-01",
                    "2026-02-01",
                    "MXN",
                    1000,
                    100,
                    200,
                    700,
                    "synthetic_owner_projection",
                ),
            )

        self.rebuild(configure)
        args = {
            "dataset": "budget",
            "version_id": version,
            "edition_year": 2026,
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
        }
        self.assertEqual(self.call("study", **args)["rows"][0]["available_amount"], 700)
        self.error(
            "INVALID_ARGUMENTS",
            lambda: self.call(
                "study", **{k: v for k, v in args.items() if k != "version_id"}
            ),
        )
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.call("study", **{**args, "version_id": DOCUMENT}),
        )

    def test_exact_repository_source_is_hash_bound_and_not_mutable(self) -> None:
        content = b"# admitted synthetic source\nprint('hello')\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_bytes(content)
            source = Sources.from_manifest(
                root,
                {"sample.py": hashlib.sha256(content).hexdigest()},
                self.gateway.admission.source_commit,
            )
            (root / "sample.py").write_bytes(b"changed on disk")
            self.assertEqual(source.files["sample.py"], content)
            self.error(
                "SOURCE_VERSION_UNPROVEN",
                lambda: Sources.from_manifest(
                    root,
                    {"sample.py": hashlib.sha256(content).hexdigest()},
                    self.gateway.admission.source_commit,
                ),
            )
            self.error(
                "SOURCE_NOT_ADMITTED",
                lambda: Sources.from_manifest(
                    root,
                    {"../sample.py": "wrong"},
                    self.gateway.admission.source_commit,
                ),
            )

    def test_group_cap_is_not_returned_as_partial_total(self) -> None:
        def configure(db: sqlite3.Connection) -> None:
            for index in range(101):
                db.execute(
                    "INSERT INTO document_status SELECT installation_id,reader_binding,tournament_id,?,kind,?,reference_operations,reference_samchat,reference_base,created_at,submitted_at,currency,amount,active_expenses,classification_present,authority_coverage FROM document_status WHERE document_id=?",
                    (str(index), f"state-{index}", DOCUMENT),
                )

        self.rebuild(configure)
        self.error(
            "OUTPUT_LIMIT",
            lambda: self.call(
                "study", date_from="2026-01-01", date_to="2026-02-01", group_by="state"
            ),
        )

    def test_source_cannot_claim_deployed_evidence(self) -> None:
        self.error(
            "SOURCE_NOT_ADMITTED",
            lambda: Sources(
                {"x.py": b"x=1"},
                self.gateway.admission.source_commit,
                source_kind="deployed_verified",
            ),
        )
        self.error("SOURCE_NOT_ADMITTED", lambda: Sources({"x.py": bytearray(b"x=1")}, self.gateway.admission.source_commit))  # type: ignore[dict-item]

    def test_real_stdio_process_defaults_disabled_and_synthetic_is_explicit(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[3]
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        for flag, expected in (([], 0), (["--synthetic-demo"], 7)):
            result = subprocess.run(
                [sys.executable, "-m", "samchat.readonly_gateway.mcp", *flag],
                input=request,
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
                env={"PYTHONPATH": str(root / "src")},
            )
            self.assertEqual(
                len(json.loads(result.stdout)["result"]["tools"]), expected
            )


if __name__ == "__main__":
    unittest.main()
