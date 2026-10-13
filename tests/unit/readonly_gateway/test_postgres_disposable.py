"""Native PostgreSQL via existing PGlite; no external database or install."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from samchat.readonly_gateway.agentius import AgentiusReadSlice, CanonicalGateway
from samchat.readonly_gateway.canonical_policy import document_route_approver_sql
from samchat.readonly_gateway.contracts import (
    Admission,
    MemoryAudit,
    Principal,
    ReadError,
    utc_now,
)
from samchat.readonly_gateway.fixtures import (
    BASE_COMMIT,
    DOCUMENT,
    OTHER_DOCUMENT,
    TOURNAMENT,
)
from samchat.readonly_gateway.gateway import Sources
from samchat.readonly_gateway.postgres import (
    PostgresBinding,
    PostgresCanonicalAdapter,
    ScopeGrant,
    normalise,
)

ROOT = Path(__file__).resolve().parents[3]
NODE = Path(
    os.environ.get("SAMCHAT_TEST_NODE_PATH")
    or shutil.which("node")
    or r"C:\Users\ffg\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
PGLITE = Path(
    os.environ.get("SAMCHAT_TEST_PGLITE_PATH")
    or r"C:\Users\ffg\Documents\Codex\2026-10-07\task\.pg-policy-check\node_modules\@electric-sql\pglite\dist\index.js"
)
ACTOR = "40000000-0000-4000-8000-000000000001"


class Engine:
    def __init__(self) -> None:
        if not NODE.is_file() or not PGLITE.is_file():
            raise RuntimeError(
                "Existing Node/PGlite required; set SAMCHAT_TEST_NODE_PATH and SAMCHAT_TEST_PGLITE_PATH. No installation is performed."
            )
        self.process = subprocess.Popen(
            [
                str(NODE),
                str(Path(__file__).with_name("pglite_bridge.mjs")),
                str(PGLITE),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.ready = json.loads(self.process.stdout.readline())

    def query(self, sql: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        names: list[str] = []

        def substitute(match: re.Match[str]) -> str:
            names.append(match.group(1))
            return "$" + str(len(names))

        sql = re.sub(r"%\((\w+)\)s", substitute, sql)
        self.process.stdin.write(
            json.dumps(
                {"sql": sql, "parameters": [(args or {})[name] for name in names]}
            )
            + "\n"
        )
        self.process.stdin.flush()
        result = json.loads(self.process.stdout.readline())
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=15)
        self.process.stdout.close()
        self.process.stderr.close()


class TestCursor:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.description = None
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, parameters: dict[str, Any] | None = None) -> None:
        result = self.engine.query(sql, parameters)
        self.description = [(name,) for name in result["fields"]]
        self.rows = [
            tuple(row.get(name) for name in result["fields"]) for row in result["rows"]
        ]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def close(self) -> None:
        self.rows = []


class TestConnection:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def cursor(self) -> TestCursor:
        return TestCursor(self.engine)

    def rollback(self) -> None:
        self.engine.query("ROLLBACK")

    def close(self) -> None:
        return None

    def cancel(self) -> None:
        return None  # This test transport does not claim cancellation support.


class Owner:
    def resolve(self, principal: Principal) -> ScopeGrant:
        return ScopeGrant(
            ACTOR,
            frozenset({DOCUMENT}),
            principal.installation,
            principal.generation,
            "base-route-v1",
        )


class LegacyExecutor:
    tools = {"demo": ({"value": {"type": "string"}}, ["value"], "demo")}

    def call(self, name: str, args: dict[str, Any]) -> str:
        if not all(isinstance(value, str) for value in args.values()):
            raise ValueError("legacy strings only")
        return args["value"]


class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("SAMCHAT_TEST_SOCKET"):
            from native_legacy_engine import NativeLegacyEngine

            cls.engine = NativeLegacyEngine(os.environ["SAMCHAT_TEST_SOCKET"])
        else:
            cls.engine = Engine()
        cls.fingerprint = hashlib.sha256(
            json.dumps(cls.engine.ready["schema"], sort_keys=True).encode()
        ).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.close()

    def setUp(self) -> None:
        self.actor = Principal(
            "synthetic-binding",
            "fixture",
            frozenset({TOURNAMENT}),
            utc_now() + timedelta(minutes=5),
            "g1",
        )
        self.binding = PostgresBinding(
            "disposable_test",
            "fixture",
            self.engine.ready["identity"]["db"],
            "fixture_reader",
            self.fingerprint,
            "base-route-v1",
        )
        self.backend = PostgresCanonicalAdapter(
            self.binding, lambda: TestConnection(self.engine), Owner()
        )
        sources = Sources(
            {"src/review.py": b"# reviewed synthetic source\n"}, BASE_COMMIT
        )
        self.gateway = CanonicalGateway(
            Admission(
                "synthetic", "fixture", self.fingerprint, BASE_COMMIT, "base-route-v1"
            ),
            self.backend,
            MemoryAudit(),
            sources,
        )

    def error(self, code: str, callback: Any) -> None:
        with self.assertRaises(ReadError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_actual_pg_status_authority_diagnose_audit(self) -> None:
        result = self.gateway.call(
            "samchat.document_diagnose",
            {"document_id": DOCUMENT, "intent": "approve"},
            self.actor,
        )["data"]
        self.assertEqual(result["diagnosis"]["state_blocker"], "invalid_estado")
        self.assertEqual(result["diagnosis"]["approval_eligibility"], "unknown")
        self.assertTrue(
            self.backend.authority(self.actor, DOCUMENT)["actor_can_decide"]
        )
        self.assertEqual(len(self.backend.audit(self.actor, DOCUMENT, 25, "", "")), 1)

    def test_exact_document_scope_and_bound_reference(self) -> None:
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.backend.document(self.actor, OTHER_DOCUMENT),
        )
        result = self.backend.reference(
            self.actor, {"reference_kind": "operations", "reference": "SYNTH-001"}
        )
        self.assertEqual(result["resolution"], "unique")
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.backend.reference(
                self.actor,
                {"reference_kind": "operations", "reference": "' OR TRUE --"},
            ),
        )
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: self.backend.document(
                replace(self.actor, tournaments=frozenset({OTHER_DOCUMENT})), DOCUMENT
            ),
        )

    def test_admission_scope_role_and_schema_fail_closed(self) -> None:
        self.backend.binding = PostgresBinding()
        self.error(
            "ACTIVATION_REQUIRED", lambda: self.backend.document(self.actor, DOCUMENT)
        )
        self.backend.binding = self.binding
        self.backend.owner = None
        denied = PostgresCanonicalAdapter(
            self.binding, lambda: self.fail("must not connect")
        )
        self.error("SCOPE_UNPROVEN", lambda: denied.document(self.actor, DOCUMENT))
        self.backend.owner = Owner()
        self.backend.binding = replace(self.binding, role="wrong")
        self.error("ROLE_UNPROVEN", lambda: self.backend.document(self.actor, DOCUMENT))
        self.backend.binding = replace(self.binding, expected_schema="wrong")
        self.error(
            "SCHEMA_MISMATCH", lambda: self.backend.document(self.actor, DOCUMENT)
        )
        self.error(
            "AUTH_REQUIRED",
            lambda: self.backend.document(
                replace(self.actor, expires_at=utc_now() - timedelta(seconds=1)),
                DOCUMENT,
            ),
        )

    def test_native_role_denies_writes_and_readonly_transaction(self) -> None:
        for sql in (
            "UPDATE documentos SET estado='enviado'",
            "CREATE TABLE should_fail(id int)",
            "DELETE FROM aprobaciones",
        ):
            with self.assertRaises(RuntimeError):
                self.engine.query(sql)
        self.engine.query("BEGIN READ ONLY")
        try:
            with self.assertRaises(RuntimeError):
                self.engine.query("UPDATE documentos SET estado='enviado'")
        finally:
            self.engine.query("ROLLBACK")
        self.assertEqual(
            self.backend.document(self.actor, DOCUMENT)["state"], "control_presupuestal"
        )

    def test_driver_not_applying_readonly_is_rejected_before_data(self) -> None:
        class IneffectiveCursor(TestCursor):
            def execute(
                inner_self, sql: str, parameters: dict[str, Any] | None = None
            ) -> None:
                if sql == "BEGIN READ ONLY":
                    return None
                return super().execute(sql, parameters)

        class IneffectiveConnection(TestConnection):
            def cursor(inner_self) -> IneffectiveCursor:
                return IneffectiveCursor(inner_self.engine)

        self.engine.query("BEGIN READ WRITE")
        self.assertEqual(
            self.engine.query(
                "SELECT current_setting('transaction_read_only') AS readonly"
            )["rows"][0]["readonly"],
            "off",
        )
        self.backend.connect = lambda: IneffectiveConnection(self.engine)
        self.error(
            "READONLY_UNPROVEN", lambda: self.backend.document(self.actor, DOCUMENT)
        )
        self.assertEqual(self.backend.query_count, 0)

    def test_native_keyset_and_ambiguous_reference(self) -> None:
        class BroadOwner:
            def resolve(inner_self, principal: Principal) -> ScopeGrant:
                return replace(
                    Owner().resolve(principal),
                    document_ids=frozenset({DOCUMENT, OTHER_DOCUMENT}),
                )

        self.backend.owner = BroadOwner()
        self.assertEqual(
            self.backend.reference(
                self.actor, {"reference_kind": "operations", "reference": "SYNTH-001"}
            )["resolution"],
            "ambiguous",
        )
        args = {
            "dataset": "documents",
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
            "page_size": 1,
        }
        first = self.gateway.call("samchat.study", args, self.actor)["data"]
        self.assertTrue(first["truncated"])
        second = self.gateway.call(
            "samchat.study", {**args, "cursor": first["next_cursor"]}, self.actor
        )["data"]
        self.assertFalse(second["truncated"])
        self.assertNotEqual(
            first["rows"][0]["document_id"], second["rows"][0]["document_id"]
        )

    def test_study_registered_fields_only_and_budget_block(self) -> None:
        args = {
            "dataset": "documents",
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
        }
        self.assertEqual(len(self.backend.study(self.actor, args)["rows"]), 1)
        self.assertEqual(
            self.backend.study(self.actor, {**args, "group_by": "state"})["groups"][0][
                "documents"
            ],
            1,
        )
        self.error(
            "CANONICAL_BUDGET_OWNER_UNBOUND",
            lambda: self.backend.study(self.actor, {**args, "dataset": "budget"}),
        )
        self.error(
            "INVALID_ARGUMENTS",
            lambda: self.backend.study(self.actor, {**args, "date_to": "2028-01-01"}),
        )
        self.error(
            "INVALID_CURSOR",
            lambda: self.gateway.call(
                "samchat.study", {**args, "cursor": "x"}, self.actor
            ),
        )
        self.error("UNREGISTERED_QUERY", lambda: self.backend.select("SELECT 1", {}))

    def test_agentius_catalog_typed_dispatch_default_disabled(self) -> None:
        disabled = AgentiusReadSlice(LegacyExecutor())
        self.assertEqual(list(disabled.tools), ["demo"])
        self.error("ACTIVATION_REQUIRED", lambda: disabled.call("samchat.study", {}))
        adapter = AgentiusReadSlice(LegacyExecutor(), self.gateway, lambda: self.actor)
        self.assertEqual(adapter.call("demo", {"value": "untouched"}), "untouched")
        for forbidden in (
            "samchat.unknown",
            "transfer.create_draft",
            "create_draft_transfer",
        ):
            self.error(
                "UNKNOWN_TOOL",
                lambda: adapter.call(forbidden, {"value": "must not reach legacy"}),
            )
        self.assertTrue(
            all(
                item["annotations"]["readOnlyHint"]
                for item in adapter.annotated_catalog()
                if item["name"].startswith("samchat.")
            )
        )
        data = adapter.call(
            "samchat.study",
            {
                "dataset": "documents",
                "date_from": "2026-01-01",
                "date_to": "2026-02-01",
                "page_size": 2,
            },
        )["data"]
        self.assertEqual(len(data["rows"]), 1)
        self.error(
            "INVALID_ARGUMENTS",
            lambda: adapter.call(
                "samchat.document_status",
                {"document_id": DOCUMENT, "actor_id": "forged"},
            ),
        )
        executor = LegacyExecutor()
        executor.tools = {"samchat.study": ()}
        self.error("TOOL_COLLISION", lambda: AgentiusReadSlice(executor, self.gateway))

    def test_canonical_policy_exact_ast_and_identifier_validation(self) -> None:
        source = ROOT / "src/devnous/gastos/services/project_authorization_service.py"
        original = next(
            node
            for node in ast.parse(source.read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "document_route_approver_sql"
        )
        copied = next(
            node
            for node in ast.parse(
                (ROOT / "src/samchat/readonly_gateway/canonical_policy.py").read_text()
            ).body
            if isinstance(node, ast.FunctionDef)
        )
        self.assertEqual(ast.dump(original), ast.dump(copied))
        with self.assertRaises(ValueError):
            document_route_approver_sql("d;DELETE", "actor")
        moment = utc_now()
        self.assertEqual(normalise(moment), str(moment))
