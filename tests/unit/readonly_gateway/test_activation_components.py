"""Isolated activation-component tests; no production bindings or data."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from samchat.readonly_gateway.agentius import CanonicalGateway, install_read_slice
from samchat.readonly_gateway.asyncpg_driver import (
    AsyncpgConnectionFactory,
    positional_sql,
)
from samchat.readonly_gateway.audit import AuditFileBinding, DurableJsonlAudit
from samchat.readonly_gateway.contracts import (
    Admission,
    MemoryAudit,
    ReadError,
    utc_now,
)
from samchat.readonly_gateway.delegation import (
    VERSION,
    BoundPrincipalResolver,
    DelegationFile,
    DelegationFileBinding,
)
from samchat.readonly_gateway.fixtures import DOCUMENT, TOURNAMENT, fixture
from samchat.readonly_gateway.gateway import Sources
from samchat.readonly_gateway.owners import (
    BudgetRequest,
    CanonicalBudgetOwner,
    DelegatedScopeOwner,
)
from samchat.readonly_gateway.postgres import PostgresBinding, PostgresCanonicalAdapter

ACTOR = "40000000-0000-4000-8000-000000000001"
VERSION_ID = "60000000-0000-4000-8000-000000000001"


class ActivationComponentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = utc_now()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def error(self, code: str, callback) -> None:
        with self.assertRaises(ReadError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def delegation_source(self) -> DelegationFile:
        value = {
            "version": VERSION,
            "delegations": [
                {
                    "transport_binding": "connector-private-binding",
                    "actor_binding": "actor-binding-v1",
                    "actor_uuid": ACTOR,
                    "installation": "fixture-installation",
                    "generation": "generation-1",
                    "expires_at": (self.now + timedelta(minutes=5)).isoformat(),
                    "tournament_ids": [TOURNAMENT],
                    "document_ids": [DOCUMENT],
                    "budget_version_ids": [VERSION_ID],
                    "owner_version": "owner-v1",
                }
            ],
        }
        path = self.root / "delegation.json"
        data = json.dumps(value, sort_keys=True).encode()
        path.write_bytes(data)
        path.chmod(0o640)
        return DelegationFile(
            DelegationFileBinding(
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )
        )

    def test_delegation_is_hash_bound_exact_and_not_payload_identity(self) -> None:
        source = self.delegation_source()
        resolver = BoundPrincipalResolver(
            source, "connector-private-binding", clock=lambda: self.now
        )
        principal = resolver()
        grant = DelegatedScopeOwner(source, "connector-private-binding").resolve(
            principal
        )
        self.assertEqual(grant.actor_uuid, ACTOR)
        self.assertEqual(grant.document_ids, frozenset({DOCUMENT}))
        self.assertEqual(grant.budget_version_ids, frozenset({VERSION_ID}))
        self.error(
            "SCOPE_UNPROVEN",
            lambda: DelegatedScopeOwner(source, "connector-private-binding").resolve(
                replace(principal, generation="forged")
            ),
        )
        source.binding.path.write_text("{}", encoding="utf-8")
        self.error("AUTH_REQUIRED", resolver)

    def test_durable_audit_fsync_chain_and_tamper_detection(self) -> None:
        path = self.root / "audit.jsonl"
        audit = DurableJsonlAudit(
            AuditFileBinding(
                path=path,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
                create_for_disposable_test=True,
            )
        )
        audit.append(
            {
                "correlation_id": "c1",
                "operation": "samchat.document_status",
                "policy_version": "owner-v1",
                "at_utc": self.now.isoformat(),
                "source_kind": "synthetic",
                "phase": "attempt",
            }
        )
        self.assertEqual(audit.verify()[0], 1)
        with path.open("ab") as stream:
            stream.write(b"{}\n")
        self.error("AUDIT_UNAVAILABLE", audit.verify)

    def test_budget_owner_enforces_exact_scope_and_canonical_flags(self) -> None:
        source = self.delegation_source()
        resolver = BoundPrincipalResolver(
            source, "connector-private-binding", clock=lambda: self.now
        )
        principal = resolver()
        grant = DelegatedScopeOwner(source, "connector-private-binding").resolve(
            principal
        )
        observed = {}

        def build(**kwargs):
            observed.update(kwargs)
            return {
                "tournament_id": TOURNAMENT,
                "version_id": VERSION_ID,
                "edition_year": 2026,
                "period_start": "2026-01-01",
                "period_end": "2026-02-01",
                "currency": "MXN",
                "approved_amount": "100.00",
                "consumed_amount": "20.00",
                "committed_amount": "10.00",
                "available_amount": "70.00",
                "coverage": "canonical_test_projection",
            }

        request = BudgetRequest(
            TOURNAMENT,
            VERSION_ID,
            2026,
            date(2026, 1, 1),
            date(2026, 2, 1),
        )
        result = CanonicalBudgetOwner(build).snapshot(principal, grant, request)
        self.assertEqual(result["available_amount"], "70.00")
        self.assertFalse(observed["ensure_schema"])
        self.assertTrue(observed["strict_tournament_scope"])
        self.assertTrue(observed["executive_read"])
        self.error(
            "NOT_FOUND_OR_NOT_VISIBLE",
            lambda: CanonicalBudgetOwner(build).snapshot(
                principal, grant, replace(request, version_id=DOCUMENT)
            ),
        )

    def test_bridge_install_preserves_tools_and_publishes_annotations(self) -> None:
        gateway, actor, _ = fixture()

        class Executor:
            def __init__(self) -> None:
                self.tools = {
                    "server_info": ({}, [], "Describe executor."),
                    "write_file": ({}, [], "Write bounded file."),
                }

            def call(self, name, args):
                return {"name": name, "args": args}

        class Server:
            executor = Executor()

        server = Server()
        decorated = install_read_slice(server, gateway, lambda: actor)
        names = {item["name"] for item in decorated.annotated_catalog()}
        self.assertIn("server_info", names)
        self.assertIn("samchat.document_status", names)
        annotations = {
            item["name"]: item["annotations"] for item in decorated.annotated_catalog()
        }
        self.assertTrue(annotations["server_info"]["readOnlyHint"])
        self.assertTrue(annotations["write_file"]["destructiveHint"])
        self.assertTrue(annotations["samchat.document_status"]["readOnlyHint"])
        self.assertIs(server.executor, decorated)

    def test_asyncpg_factory_is_fresh_parameterized_and_cancellable(self) -> None:
        class Record(dict):
            pass

        class Raw:
            def __init__(self) -> None:
                self.calls = []
                self.terminated = False
                self.closed = False

            async def fetch(self, sql, *values, timeout):
                self.calls.append(("fetch", sql, values, timeout))
                return [Record(value=values[0])]

            async def execute(self, sql, *values, timeout):
                self.calls.append(("execute", sql, values, timeout))
                return "OK"

            async def close(self, timeout):
                self.closed = True

            def terminate(self):
                self.terminated = True

        created = []

        async def connector():
            raw = Raw()
            created.append(raw)
            return raw

        factory = AsyncpgConnectionFactory(connector)
        first = factory()
        cursor = first.cursor()
        cursor.execute("SELECT %(value)s AS value", {"value": "literal"})
        self.assertEqual(cursor.fetchmany(2), [("literal",)])
        first.rollback()
        first.close()
        second = factory()
        self.assertIsNot(created[0], created[1])
        second.cancel()
        second.close()
        self.assertTrue(created[0].closed)
        self.assertTrue(created[1].terminated)
        sql, values = positional_sql(
            "SELECT %(same)s, %(same)s, %(other)s", {"same": 1, "other": 2}
        )
        self.assertEqual(sql, "SELECT $1, $1, $2")
        self.assertEqual(values, [1, 2])

    def test_production_binding_requires_exact_activation_receipt(self) -> None:
        required = {
            "kind": "production",
            "installation": "samchat-production",
            "database": "devnous_db",
            "role": "agentius_samchat_ro_v1",
            "expected_schema": "a" * 64,
            "owner_version": "owner-v1",
        }
        self.error("ACTIVATION_REQUIRED", lambda: PostgresBinding(**required).check())
        self.error(
            "ACTIVATION_REQUIRED",
            lambda: PostgresBinding(
                **required, activation_receipt_sha256="not-a-receipt"
            ).check(),
        )
        PostgresBinding(**required, activation_receipt_sha256="b" * 64).check()

    def test_production_composition_binds_one_exact_receipt_and_scope(self) -> None:
        receipt = "b" * 64
        admission = Admission(
            mode="production",
            installation="samchat-production",
            schema_fingerprint="a" * 64,
            source_commit="c" * 40,
            policy_version="owner-v1",
            activation_receipt_sha256=receipt,
        )
        binding = PostgresBinding(
            kind="production",
            installation="samchat-production",
            database="devnous_db",
            role="agentius_samchat_ro_v1",
            expected_schema="a" * 64,
            owner_version="owner-v1",
            activation_receipt_sha256=receipt,
        )
        backend = PostgresCanonicalAdapter(binding, lambda: None)
        sources = Sources(
            {"src/review.py": b"# reviewed\n"},
            "c" * 40,
            source_kind="reviewed_manifest",
        )
        CanonicalGateway(admission, backend, MemoryAudit(), sources)
        mismatches = (
            replace(binding, activation_receipt_sha256="d" * 64),
            replace(binding, installation="other-installation"),
            replace(binding, expected_schema="e" * 64),
            replace(binding, owner_version="owner-v2"),
        )
        for mismatch in mismatches:
            self.error(
                "ACTIVATION_REQUIRED",
                lambda mismatch=mismatch: CanonicalGateway(
                    admission,
                    PostgresCanonicalAdapter(mismatch, lambda: None),
                    MemoryAudit(),
                    sources,
                ),
            )


if __name__ == "__main__":
    unittest.main()
