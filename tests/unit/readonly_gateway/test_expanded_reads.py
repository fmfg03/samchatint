"""Synthetic policy/source doubles plus actual SQL reads in disposable SQLite.

No production identity, connection, grant or executor is used.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import types
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from samchat.readonly_gateway.contracts import MemoryAudit, ReadError
from samchat.readonly_gateway.development_contracts import (
    DevelopmentContract,
    DevelopmentManifest,
    WorkReceipt,
)
from samchat.readonly_gateway.expanded import (
    Authority,
    DomainScope,
    ExpandedReads,
    Observation,
)
from samchat.readonly_gateway.expanded_adapters import (
    CanonicalDatasetAdapter,
    postgres_read_session,
)
from samchat.readonly_gateway.expanded_catalog import Query
from samchat.readonly_gateway.gateway import Sources
from samchat.readonly_gateway.technical_contracts import TechnicalReads, redact_source

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "a983be79e1ad8a6b5aa56affe27a945160ac8166"
T = "10000000-0000-4000-8000-000000000001"
A = "20000000-0000-4000-8000-000000000001"
B = "20000000-0000-4000-8000-000000000002"
C = "20000000-0000-4000-8000-000000000003"


def request(**kwargs):
    return {
        "dataset": "solicitud",
        "object_ids": [A, B],
        "tournament_id": T,
        "date_from": "2026-01-01",
        "date_to": "2026-02-01",
        **kwargs,
    }


class SyntheticSource:
    def __init__(self):
        self.calls = 0
        self.rows = [
            {
                "object_id": identifier,
                "state": "aprobado",
                "kind": "SOLICITUD",
                "currency": "MXN",
                "amount": amount,
                "bank_account": "123456789012345678",
                "secret": "synthetic-secret",
                "attachment": "private",
            }
            for identifier, amount in ((A, "12.10"), (B, "7.90"))
        ]

    async def read(self, query, authority):
        self.calls += 1
        return Observation(
            self.rows, ("synthetic_source",), "a" * 64, "synthetic_fixture"
        )


class PerimeterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, tzinfo=timezone.utc)
        self.actor = Authority(
            "alice",
            "psp",
            "g1",
            "p1",
            self.now + timedelta(hours=1),
            frozenset({DomainScope("solicitud", T, frozenset({A, B}))}),
        )
        self.source, self.audit = SyntheticSource(), MemoryAudit()
        self.gateway = ExpandedReads(
            resolve=lambda: self.actor,
            adapters={"solicitud": self.source},
            audit=self.audit,
            source_commit=COMMIT,
            clock=lambda: self.now,
        )

    async def error(self, code, args):
        with self.assertRaisesRegex(ReadError, code):
            await self.gateway.query(args)

    async def test_real_perimeter_minimizes_and_carries_provenance(self):
        result = await self.gateway.query(request())
        self.assertEqual(result["rows"][0]["amount"], "12.10")
        self.assertNotIn("bank_account", json.dumps(result))
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertEqual(result["reviewed_commit"], COMMIT)
        self.assertIn("candidate_not_activated", result["gaps"])

    async def test_closed_inputs_reject_actor_sql_functions_bad_types_and_limits(self):
        for change in (
            {"actor": "admin"},
            {"sql": "SELECT 1"},
            {"function": "eval"},
            {"dataset": "unknown"},
            {"fields": ["bank_account"]},
            {"group_by": "state;drop table x"},
            {"measure": "amount"},
            {"page_size": True},
            {"page_size": 101},
            {"object_ids": [A, A]},
            {"object_ids": ["../private"]},
            {"tournament_id": "invalid"},
            {"date_to": "2028-01-01"},
            {"date_to": "2026-01-01"},
            {"date_from": "bad"},
            {"fields": "amount"},
            {"fields": []},
            {"version_id": A},
            {"cursor": 1},
        ):
            with self.subTest(change=change):
                await self.error("INVALID_ARGUMENTS", request(**change))
        self.assertEqual(self.source.calls, 0)

    async def test_object_type_tournament_version_and_user_isolation(self):
        await self.error("NOT_FOUND_OR_NOT_VISIBLE", request(object_ids=[C]))
        await self.error("NOT_FOUND_OR_NOT_VISIBLE", request(dataset="amex.statement"))
        await self.error("NOT_FOUND_OR_NOT_VISIBLE", request(tournament_id=B))
        await self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            request(dataset="budget.snapshot", object_ids=[A], version_id=A),
        )
        self.actor = replace(self.actor, actor_binding="bob", scopes=frozenset())
        await self.error("NOT_FOUND_OR_NOT_VISIBLE", request())
        self.assertEqual(self.source.calls, 0)

    async def test_capabilities_never_grants_and_does_not_take_actor(self):
        data = self.gateway.capabilities({})
        self.assertTrue(data["datasets"]["solicitud"]["candidate_readable"])
        self.assertFalse(data["datasets"]["amex.statement"]["candidate_readable"])
        self.assertFalse(data["business_writes"])
        with self.assertRaisesRegex(ReadError, "INVALID_ARGUMENTS"):
            self.gateway.capabilities({"actor": "admin"})
        self.gateway.adapters.clear()
        await self.error("CANONICAL_ADAPTER_UNBOUND", request())

    async def test_expiration_resolver_failure_and_revocation_before_delivery(self):
        self.actor = replace(self.actor, expires_at=self.now)
        await self.error("AUTH_REQUIRED", request())
        self.actor = replace(self.actor, expires_at=self.now + timedelta(hours=1))
        original = self.source.read

        async def revoke(query, authority):
            result = await original(query, authority)
            self.actor = replace(self.actor, generation="revoked")
            return result

        self.source.read = revoke
        await self.error("AUTH_CHANGED", request())

    async def test_revocation_during_audit(self):
        original = self.audit.append

        def revoke(event):
            original(event)
            if event.get("decision") == "allow":
                self.actor = replace(self.actor, scopes=frozenset())

        self.audit.append = revoke
        await self.error("AUTH_CHANGED", request())

    async def test_cursor_bound_to_identity_filters_snapshot_policy_and_expiration(
        self,
    ):
        first = await self.gateway.query(request(page_size=1))
        cursor = first["next_cursor"]
        second = await self.gateway.query(request(page_size=1, cursor=cursor))
        self.assertEqual(second["rows"][0]["object_id"], B)
        self.assertFalse(second["truncated"])
        for change in (
            {"fields": ["state"]},
            {"date_to": "2026-03-01"},
            {"cursor": cursor + "bad"},
            {"cursor": "malformed"},
        ):
            await self.error(
                "CURSOR_INVALID_OR_STALE", request(page_size=1, cursor=cursor) | change
            )
        self.source.rows[0]["amount"] = "13.10"
        await self.error("CURSOR_INVALID_OR_STALE", request(page_size=1, cursor=cursor))
        self.source.rows[0]["amount"] = "12.10"
        self.actor = replace(self.actor, actor_binding="bob")
        await self.error("CURSOR_INVALID_OR_STALE", request(page_size=1, cursor=cursor))
        self.actor = replace(self.actor, actor_binding="alice", policy_version="p2")
        await self.error("CURSOR_INVALID_OR_STALE", request(page_size=1, cursor=cursor))
        self.actor = replace(self.actor, policy_version="p1")
        self.now += timedelta(minutes=6)
        await self.error("CURSOR_INVALID_OR_STALE", request(page_size=1, cursor=cursor))

    async def test_grouping_currency_separation_and_missing_amount(self):
        result = await self.gateway.query(request(group_by="state", measure="amount"))
        self.assertEqual(result["rows"][0]["sum"], "20.00")
        self.source.rows[1]["currency"] = "USD"
        result = await self.gateway.query(request(group_by="state", measure="amount"))
        self.assertEqual(len(result["rows"]), 2)
        self.source.rows[0]["amount"] = None
        result = await self.gateway.query(request(group_by="state", measure="amount"))
        self.assertIsNone(result["rows"][0]["sum"])
        self.assertEqual(result["rows"][0]["missing"], 1)

    async def test_unknown_currency_and_nonfinite_numeric_strings_are_not_totals(self):
        for row in self.source.rows:
            row["currency"] = None
        result = await self.gateway.query(request(group_by="state", measure="amount"))
        self.assertIsNone(result["rows"][0]["sum"])
        self.assertEqual(result["rows"][0]["missing_currency"], 2)
        for value in ("NaN", "Infinity", "-Infinity", "123456789012345678", True):
            self.source.rows[0]["amount"] = value
            await self.error(
                "SOURCE_INVALID", request(group_by="state", measure="amount")
            )
        self.source.rows[0]["amount"] = "12.10"
        for field, value in (
            ("state", "1234567890123456"),
            ("kind", "INFORME"),
            ("created_at", "not-a-date"),
            ("currency", "bad-value"),
        ):
            original = self.source.rows[0].get(field)
            self.source.rows[0][field] = value
            await self.error("SOURCE_INVALID", request())
            self.source.rows[0][field] = original

    async def test_scope_escape_duplicate_secret_nan_and_backend_exception(self):
        self.source.rows[0]["object_id"] = C
        await self.error("SOURCE_SCOPE_ESCAPE", request())
        self.source.rows[0]["object_id"] = B
        await self.error("SOURCE_INVALID", request())
        self.source.rows[0]["object_id"] = A
        for value in (
            float("nan"),
            {"secret": "x"},
            "postgresql://user:password@host/db",
        ):
            self.source.rows[0]["amount"] = value
            await self.error("SOURCE_INVALID", request())

        async def failed(q, a):
            raise RuntimeError("password=private")

        self.source.read = failed
        await self.error("SOURCE_UNAVAILABLE", request())
        self.assertNotIn("private", json.dumps(self.audit.events))

    async def test_timeout_cancel_and_fail_closed_audit(self):
        async def slow(q, a):
            await asyncio.sleep(1)

        self.source.read = slow
        self.gateway.timeout = 0.01
        await self.error("TIMEOUT", request())
        task = asyncio.create_task(self.gateway.query(request()))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        def failed(event):
            raise RuntimeError("secret")

        self.audit.append = failed
        await self.error("AUDIT_UNAVAILABLE", request())


class CanonicalAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_rejects_unproven_versions_and_withholds_quality_gaps(self):
        @asynccontextmanager
        async def scope():
            yield object()

        adapter = CanonicalDatasetAdapter(scope)
        raw = {
            "source": "budget_db",
            "version": {"id": A},
            "summary": {"budget_total": 100},
            "executive_quality_gaps": ["missing_amount"],
        }

        async def source(session, **kwargs):
            return raw

        module = types.ModuleType("samchat.budgets.service")
        module.build_budget_snapshot = source
        q = Query.parse(
            request(dataset="budget.snapshot", object_ids=[A], version_id=A)
        )
        actor = Authority(
            "a",
            "psp",
            "g",
            "p",
            datetime.now(timezone.utc) + timedelta(hours=1),
            frozenset({DomainScope("budget.snapshot", T, frozenset({A}), A)}),
        )
        with patch.dict(sys.modules, {"samchat.budgets.service": module}):
            result = await adapter.read(q, actor)
            self.assertIsNone(result.rows[0]["budget_total"])
            self.assertIn(
                "budget_total_full_version_edition_not_period_prorated", result.gaps
            )
            raw["version"]["id"] = B
            with self.assertRaisesRegex(ReadError, "SOURCE_VERSION_UNPROVEN"):
                await adapter.read(q, actor)
            with self.assertRaisesRegex(ReadError, "NOT_FOUND_OR_NOT_VISIBLE"):
                await adapter.read(q, replace(actor, scopes=frozenset()))
        ar_raw = {
            "budget_version_id": A,
            "tournament_id": T,
            "source_status": {"strict_tournament_scope": True, "amount_gap": True},
            "summary": {"collected_total": 100},
        }

        async def ar_source(session, **kwargs):
            return ar_raw

        module = types.ModuleType("samchat.ar.service")
        module.build_ar_read_model = ar_source
        budget_module = types.ModuleType("samchat.budgets.service")

        async def selected_version(session, **kwargs):
            return {"id": A, "edition_year": 2026, "status": "approved"}

        budget_module._select_budget_version = selected_version
        q = replace(q, dataset="ar.summary")
        with patch.dict(
            sys.modules,
            {"samchat.ar.service": module, "samchat.budgets.service": budget_module},
        ):
            result = await adapter._read(object(), q)
            self.assertIsNone(result.rows[0]["collected_total"])
            self.assertIn("historical_as_of_balances_not_supported", result.gaps)
            ar_raw["tournament_id"] = B
            with self.assertRaisesRegex(ReadError, "SOURCE_SCOPE_ESCAPE"):
                await adapter._read(object(), q)
            ar_raw["tournament_id"] = T
            ar_raw["source_status"]["strict_tournament_scope"] = False
            with self.assertRaisesRegex(ReadError, "SOURCE_SCOPE_ESCAPE"):
                await adapter._read(object(), q)

    async def test_read_session_checks_role_transaction_and_rolls_back_on_cancel(self):
        from types import SimpleNamespace

        commands = []
        row = {
            "role": "candidate_reader",
            "session_role": "candidate_reader",
            "readonly": "on",
            "isolation": "repeatable read",
            **{
                k: False
                for k in (
                    "rolsuper",
                    "rolcreaterole",
                    "rolcreatedb",
                    "rolreplication",
                    "rolbypassrls",
                )
            },
        }

        class Session:
            new, dirty, deleted = [], [], []
            autoflush = True
            transaction = False
            rolled_back = 0

            def in_transaction(self):
                return self.transaction

            async def execute(self, sql):
                commands.append(str(sql))
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(one=lambda: row)
                )

            async def rollback(self):
                self.rolled_back += 1

        session = Session()

        @asynccontextmanager
        async def factory():
            yield session

        with self.assertRaisesRegex(ReadError, "SEPARATE_READ_ROLE_REQUIRED"):
            async with postgres_read_session(factory, expected_role="samchat_ro_fmfg"):
                pass
        with self.assertRaises(asyncio.CancelledError):
            async with postgres_read_session(factory, expected_role="candidate_reader"):
                raise asyncio.CancelledError
        self.assertEqual(session.rolled_back, 1)
        self.assertEqual(
            commands[0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        self.assertFalse(session.autoflush)
        row["rolsuper"] = True
        with self.assertRaisesRegex(ReadError, "READONLY_ROLE_UNPROVEN"):
            async with postgres_read_session(factory, expected_role="candidate_reader"):
                pass
        self.assertEqual(session.rolled_back, 2)
        session.transaction = True
        with self.assertRaisesRegex(ReadError, "FRESH_SESSION_REQUIRED"):
            async with postgres_read_session(factory, expected_role="candidate_reader"):
                pass

    async def test_solicitud_actual_sqlite_query_has_no_writes_or_related_reads(self):
        from sqlalchemy import create_engine, text

        from devnous.gastos.services.connector_read_service import read_solicitudes

        engine = create_engine("sqlite://")
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE documentos(id TEXT,torneo_id TEXT,tipo TEXT,estado TEXT,creado_en TEXT,currency TEXT,monto_total NUMERIC)"
                )
            )
            for identifier, tournament, kind in (
                (A, T, "SOLICITUD"),
                (B, T, "INFORME"),
                (C, B, "SOLICITUD"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO documentos VALUES(:id,:t,:kind,'aprobado','2026-01-15','MXN',10)"
                    ),
                    {"id": identifier, "t": tournament, "kind": kind},
                )
            conn.commit()
            sql_seen = []

            def authorize(action, table, column, database, source):
                if action in (
                    sqlite3.SQLITE_INSERT,
                    sqlite3.SQLITE_UPDATE,
                    sqlite3.SQLITE_DELETE,
                    sqlite3.SQLITE_CREATE_TABLE,
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.connection.driver_connection.set_authorizer(authorize)

            class Session:
                async def execute(self, statement, params):
                    sql_seen.append(str(statement))
                    return conn.execute(statement, params)

            rows = await read_solicitudes(
                Session(),
                object_ids=(A, B, C),
                tournament_id=T,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 2, 1),
            )
            self.assertEqual([row["object_id"] for row in rows], [A])
            self.assertEqual(len(sql_seen), 1)
            with self.assertRaises(Exception):
                conn.execute(text("DELETE FROM documentos"))
        engine.dispose()

    async def test_budget_ar_payment_amex_real_adapter_with_explicit_source_doubles(
        self,
    ):
        captured = []

        @asynccontextmanager
        async def session_scope():
            yield object()

        adapter = CanonicalDatasetAdapter(session_scope)

        async def budget(session, **kwargs):
            captured.append(kwargs)
            return {
                "source": "budget_db",
                "version": {"id": A},
                "summary": {"budget_total": 25},
            }

        async def ar(session, **kwargs):
            captured.append(kwargs)
            return {
                "budget_version_id": A,
                "tournament_id": T,
                "source_status": {"strict_tournament_scope": True},
                "summary": {"collected_total": 10},
            }

        async def payment(session, **kwargs):
            captured.append(kwargs)
            return [
                {
                    "id": A,
                    "estado": "en_proceso_pago",
                    "monto": 0,
                    "amount_issue": "missing",
                    "source_amount_missing": True,
                    "account": "private",
                }
            ]

        async def amex(session, authority, identifier):
            captured.append(identifier)
            return types.SimpleNamespace(
                id=identifier,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 1, 31),
                workflow_mode="direct",
                total_rows=1,
                filename="secret",
                card_account_id="private",
            )

        async def selected_version(session, **kwargs):
            return {"id": A, "edition_year": 2026, "status": "approved"}

        adapter.amex_authorized_reader = amex
        stubs = {
            "samchat.budgets.service": {
                "build_budget_snapshot": budget,
                "_select_budget_version": selected_version,
            },
            "samchat.ar.service": {"build_ar_read_model": ar},
            "devnous.gastos.services.payment_run_service": {
                "list_payment_run_items": payment
            },
            "devnous.gastos.services.amex_statement_import_service": {
                "get_amex_statement_import": amex
            },
        }
        modules = {}
        for name, attributes in stubs.items():
            module = types.ModuleType(name)
            module.__dict__.update(attributes)
            modules[name] = module
        with patch.dict(sys.modules, modules):
            for dataset in (
                "budget.snapshot",
                "ar.summary",
                "payment_run",
                "amex.statement",
            ):
                args = request(dataset=dataset, object_ids=[A])
                if dataset in ("budget.snapshot", "ar.summary"):
                    args["version_id"] = A
                query = Query.parse(args)
                observation = await adapter._read(object(), query, object())
                self.assertEqual(observation.rows[0]["object_id"], A)
                self.assertNotIn("private", json.dumps(observation.rows))
                if dataset == "payment_run":
                    self.assertIsNone(observation.rows[0]["amount"])
            for kwargs in captured[:3]:
                self.assertIs(kwargs["ensure_schema"], False)
            self.assertTrue(captured[0]["strict_tournament_scope"])
            self.assertTrue(captured[0]["executive_read"])
            self.assertEqual(captured[0]["date_to"], date(2026, 1, 31))
            self.assertTrue(captured[1]["strict_tournament_scope"])
            self.assertEqual(captured[2]["document_ids"], [A])


class TechnicalTests(unittest.TestCase):
    def test_audit_denial_is_attributable_and_revocation_blocks_delivery(self):
        with self.assertRaises(ReadError):
            self.technical.call(
                "source_read",
                {"path": "../secret", "source_id": self.sources.source_id},
            )
        event = self.perimeter.audit.events[-1]
        self.assertEqual(event["decision"], "deny")
        self.assertIn("actor_digest", event)
        self.assertIn("path_digest", event)
        self.assertIn("policy_version", event)
        self.assertNotIn("../secret", json.dumps(event))
        original = self.perimeter.audit.append

        def revoke(event):
            original(event)
            if event.get("decision") == "allow":
                self.actor = replace(self.actor, generation="revoked")

        self.perimeter.audit.append = revoke
        with self.assertRaisesRegex(ReadError, "AUTH_CHANGED"):
            self.technical.call("source_info", {})

    def setUp(self):
        self.sources = Sources(
            {"safe.py": b"# reviewed\nvalue = 1234567890123456\n"}, COMMIT
        )
        self.actor = Authority(
            "a",
            "psp",
            "g",
            "p",
            datetime.now(timezone.utc) + timedelta(hours=1),
            frozenset(
                {
                    DomainScope(
                        "technical.source",
                        "",
                        frozenset({self.sources.source_id}),
                        COMMIT,
                    )
                }
            ),
        )
        self.perimeter = ExpandedReads(
            resolve=lambda: self.actor,
            adapters={},
            audit=MemoryAudit(),
            source_commit=COMMIT,
        )
        self.technical = TechnicalReads(self.perimeter, self.sources)

    def test_manifest_bounds_redaction_and_checks(self):
        result = self.technical.call(
            "source_read", {"source_id": self.sources.source_id, "path": "safe.py"}
        )
        self.assertNotIn("1234567890123456", json.dumps(result))
        for op, args in (
            ("source_read", {"source_id": self.sources.source_id, "path": "../x"}),
            (
                "source_read",
                {
                    "source_id": self.sources.source_id,
                    "path": "safe.py",
                    "line_count": 101,
                },
            ),
            ("source_read", {"source_id": "stale", "path": "safe.py"}),
            ("diagnose", {"check": "shell"}),
            ("diagnose", {"command": "whoami"}),
        ):
            with self.assertRaises(ReadError):
                self.technical.call(op, args)
        self.assertIn("files", self.technical.call("source_info", {})["data"])
        for check in ("source-manifest", "adapter-bindings"):
            self.assertEqual(
                self.technical.call("diagnose", {"check": check})["data"]["check"],
                check,
            )
        self.actor = replace(self.actor, scopes=frozenset())
        with self.assertRaisesRegex(ReadError, "NOT_FOUND"):
            self.technical.call("source_info", {})

    def test_redaction_patterns(self):
        value = "Bearer abc.def postgres://user:pass@host/db password=secret 1234 5678 9012 3456"
        clean = redact_source(value)
        for secret in ("abc.def", "user:pass", "=secret", "1234 5678"):
            self.assertNotIn(secret, clean)


class DevelopmentTests(unittest.TestCase):
    def test_accepted_report_catalog_stays_at_five_and_denies_new_tools(self):
        from samchat.readonly_gateway.live_informes import (
            REPORT_TOOLS,
            ReportOnlyGateway,
        )

        expected = frozenset(
            {
                "samchat.resolve_reference",
                "samchat.document_status",
                "samchat.document_diagnose",
                "samchat.document_audit",
                "samchat.study",
            }
        )
        self.assertEqual(REPORT_TOOLS, expected)
        self.assertEqual(ReportOnlyGateway.allowed_tools, expected)
        gateway = object.__new__(ReportOnlyGateway)
        for operation in (
            "samchat.capabilities",
            "samchat.dataset_query",
            "samchat.source_read",
            "work_start",
        ):
            with self.assertRaisesRegex(ReadError, "ACTION_NOT_REGISTERED"):
                gateway.call(operation, {}, None)

    def setUp(self):
        self.manifest = DevelopmentManifest(
            "fmfg03/samchatint",
            COMMIT,
            "feat/connector-read-20261016",
            frozenset({"src/a.py"}),
            frozenset({"activation-stdlib"}),
            "a" * 64,
        )
        self.contract = DevelopmentContract(self.manifest)
        self.receipt = WorkReceipt(
            "work-1", "alice", "g1", "a" * 64, 1, "prepared", frozenset({"b" * 64})
        )

    def test_create_is_validation_only_and_denies_arbitrary_files_checks_identity(self):
        args = {
            "project": "samchat-dev",
            "objective": "Prepare read candidate",
            "files": ["src/a.py"],
            "checks": ["activation-stdlib"],
        }
        result = self.contract.create(args, trusted_owner="alice")
        self.assertFalse(result["executor_bound"])
        for change in (
            {"actor": "bob"},
            {"files": ["../secret"]},
            {"checks": ["shell"]},
            {"project": "other"},
        ):
            with self.assertRaises(ReadError):
                self.contract.create(args | change, trusted_owner="alice")

    def test_start_resume_status_artifact_revocation_revision_cancel(self):
        args = {"work_id": "work-1", "expected_revision": 1}
        for operation, state in (
            ("work_start", "prepared"),
            ("work_resume", "paused"),
            ("work_status", "completed"),
            ("work_cancel", "running"),
            ("work_artifact", "completed"),
        ):
            callargs = args | (
                {"sha256": "b" * 64} if operation == "work_artifact" else {}
            )
            self.contract.validate_operation(
                operation,
                callargs,
                current=replace(self.receipt, state=state),
                trusted_owner="alice",
                current_generation="g1",
            )
        for owner, generation, revision, operation, state in (
            ("bob", "g1", 1, "work_start", "prepared"),
            ("alice", "revoked", 1, "work_start", "prepared"),
            ("alice", "g1", 2, "work_start", "prepared"),
            ("alice", "g1", 1, "work_resume", "cancelled"),
            ("alice", "g1", 1, "push", "completed"),
        ):
            with self.assertRaises(ReadError):
                self.contract.validate_operation(
                    operation,
                    args | {"expected_revision": revision},
                    current=replace(self.receipt, state=state),
                    trusted_owner=owner,
                    current_generation=generation,
                )
        with self.assertRaises(ReadError):
            self.contract.validate_operation(
                "work_artifact",
                args | {"sha256": "c" * 64},
                current=replace(self.receipt, state="completed"),
                trusted_owner="alice",
                current_generation="g1",
            )

    def test_publication_preparation_never_executes(self):
        for checks, review in ((False, True), (True, False)):
            with self.assertRaises(ReadError):
                self.contract.prepare_publication(
                    base_sha=COMMIT,
                    diff_sha256="c" * 64,
                    checks_passed=checks,
                    independent_review_passed=review,
                )
        result = self.contract.prepare_publication(
            base_sha=COMMIT,
            diff_sha256="c" * 64,
            checks_passed=True,
            independent_review_passed=True,
        )
        self.assertFalse(result["push_executed"])
        self.assertFalse(result["pr_created"])
