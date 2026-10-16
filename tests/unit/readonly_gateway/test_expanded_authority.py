"""Actual canonical guard bodies + SQL on synthetic SQLite fixtures.

No web bootstrap or environment loading. FastAPI Depends/HTTPException are
minimal framework fixtures. Direct AMEX schema admission is a labelled test
boundary, NOT native PostgreSQL/schema acceptance. Production uses the real
require_amex_direct_schema without overrides. All policy/source SQL is real.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import types
import unicodedata
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session, declarative_base

from samchat.readonly_gateway.canonical_authority import (
    CanonicalAuthorityResolver,
    ServerBinding,
    compose_candidate,
)
from samchat.readonly_gateway.contracts import MemoryAudit, ReadError
from samchat.readonly_gateway.expanded import (
    DomainScope,
    ExpandedReads,
    Observation,
    digest,
)
from samchat.readonly_gateway.expanded_catalog import Query
from test_expanded_reads import A, B, T, COMMIT, request

ROOT = Path(__file__).resolve().parents[3]
ACTOR = "71000000-0000-4000-8000-000000000001"
OTHER = "71000000-0000-4000-8000-000000000002"
CARD = "72000000-0000-4000-8000-000000000001"
CONTEXT = "73000000-0000-4000-8000-000000000001"


@lru_cache
def source_blocks(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    starts = list(
        re.finditer(
            r"^(?:(?:async )?def |class )(?P<def>[A-Za-z_]\w*)|^(?P<assign>[A-Za-z_]\w*)\s*=",
            source,
            re.M,
        )
    )
    return {
        match.group("def")
        or match.group("assign"): source[
            match.start() : (
                starts[i + 1].start() if i + 1 < len(starts) else len(source)
            )
        ].split("\n@", 1)[0]
        for i, match in enumerate(starts)
    }


@lru_cache
def source_node(relative, key):
    # Parse selected unchanged top-level blocks, not the 2MB web route module.
    return ast.parse(source_blocks(relative)[key]).body[0]


def canonical_module(name, relative, roots, injected=None):
    """Extract unchanged guard bodies and their local dependencies/constants."""
    path = ROOT / relative
    blocks = source_blocks(relative)
    nodes = {}
    needed, pending = set(), list(roots)
    injected = injected or {}
    while pending:
        key = pending.pop()
        if key in needed or key in injected or key not in blocks:
            continue
        needed.add(key)
        nodes[key] = source_node(relative, key)
        pending.extend(n.id for n in ast.walk(nodes[key]) if isinstance(n, ast.Name))
    tree = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            )
        ]
        + [nodes[key] for key in blocks if key in nodes],
        type_ignores=[],
    )
    module = types.ModuleType(name)
    module.__dict__.update(vars(sa))
    module.__dict__.update(
        dict(
            json=json,
            os=os,
            re=re,
            logging=logging,
            unicodedata=unicodedata,
            UUID=UUID,
            logger=logging.getLogger(name),
        )
    )
    module.__dict__.update(injected)
    module.__name__ = name
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)
    return module


class HTTPDenied(Exception):
    def __init__(self, **kwargs):
        super().__init__("canonical_http_denial")


class FixtureUUID(sa.TypeDecorator):
    impl = sa.String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return str(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return UUID(value) if value else None


class CanonicalAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = sa.create_engine(
            "sqlite:///" + str(Path(self.tmp.name) / "fixture.db")
        )
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            c.exec_driver_sql("PRAGMA journal_mode=WAL")
            for sql in (
                "CREATE TABLE empleados(id TEXT PRIMARY KEY,activo BOOLEAN,rol TEXT,departamento TEXT,correo TEXT,password_hash TEXT)",
                "CREATE TABLE access_profiles(id TEXT,permissions TEXT,active BOOLEAN)",
                "CREATE TABLE empleado_access_profiles(empleado_id TEXT,profile_id TEXT)",
                "CREATE TABLE access_control_rules(tool_key TEXT,action_key TEXT,role_key TEXT,area_key TEXT,allowed BOOLEAN,active BOOLEAN,updated_at TEXT)",
                "CREATE TABLE documentos(id TEXT,empleado_id TEXT,tipo TEXT,estado TEXT,pagado_en TEXT,referencia_operaciones TEXT,torneo_id TEXT,creado_en TEXT,currency TEXT,monto_total NUMERIC)",
                "CREATE TABLE budget_versions(id TEXT,status TEXT,edition_year INT)",
                "CREATE TABLE budget_lines(budget_version_id TEXT,tournament_id TEXT)",
            ):
                c.exec_driver_sql(sql)
            c.execute(
                sa.text(
                    "INSERT INTO empleados VALUES (:id,1,'empleado','operations','synthetic@example.invalid','NEVER_READ')"
                ),
                {"id": ACTOR},
            )
            c.execute(
                sa.text(
                    "INSERT INTO documentos VALUES (:id,:actor,'SOLICITUD','aprobado',NULL,NULL,:tor,'2026-01-10','MXN',12)"
                ),
                {"id": A, "actor": ACTOR, "tor": T},
            )
            c.execute(
                sa.text(
                    "INSERT INTO documentos VALUES (:id,:actor,'INFORME','aprobado',NULL,NULL,:tor,'2026-01-10','MXN',999)"
                ),
                {"id": B, "actor": OTHER, "tor": T},
            )
        base = declarative_base()
        models = types.ModuleType("devnous.gastos.models")
        models.Empleado = type("Empleado", (), {})
        for name, table, columns in (
            (
                "AmexStatementImport",
                "amex_statement_imports",
                dict(
                    id=sa.Column(FixtureUUID, primary_key=True),
                    card_account_id=sa.Column(FixtureUUID),
                    workflow_mode=sa.Column(sa.String),
                    period_start=sa.Column(sa.Date),
                    period_end=sa.Column(sa.Date),
                    total_rows=sa.Column(sa.Integer),
                    imported_count=sa.Column(sa.Integer),
                    skipped_count=sa.Column(sa.Integer),
                    credit_count=sa.Column(sa.Integer),
                    unknown_count=sa.Column(sa.Integer),
                ),
            ),
            (
                "AmexStatementDirectState",
                "amex_statement_direct_states",
                dict(
                    import_id=sa.Column(FixtureUUID, primary_key=True),
                    context_id=sa.Column(FixtureUUID),
                ),
            ),
            (
                "AmexStatementDirectContext",
                "amex_statement_direct_contexts",
                dict(
                    id=sa.Column(FixtureUUID, primary_key=True),
                    company_code=sa.Column(sa.String),
                    receiver_rfc=sa.Column(sa.String),
                    config_hash=sa.Column(sa.String),
                ),
            ),
            (
                "AmexDirectAccountingCut",
                "amex_direct_accounting_cuts",
                dict(
                    id=sa.Column(FixtureUUID, primary_key=True),
                    import_id=sa.Column(FixtureUUID),
                ),
            ),
        ):
            setattr(
                models, name, type(name, (base,), {"__tablename__": table, **columns})
            )
        base.metadata.create_all(self.engine)
        self.models = models
        access = canonical_module(
            "devnous.gastos.services.access_control_service",
            "src/devnous/gastos/services/access_control_service.py",
            {"explicit_tool_decision", "is_superadmin_role"},
        )
        deps = canonical_module(
            "devnous.gastos.routes.dependencies",
            "src/devnous/gastos/routes/dependencies.py",
            {"_load_effective_permissions", "require_admin_finanzas"},
            {
                "Depends": lambda dependency: SimpleNamespace(dependency=dependency),
                "get_current_empleado": lambda: None,
                "HTTPException": HTTPDenied,
            },
        )
        payment = canonical_module(
            "devnous.gastos.services.payment_run_service",
            "src/devnous/gastos/services/payment_run_service.py",
            {
                "can_access_payment_run",
                "require_payment_run_access",
                "require_payment_run_payment_confirmation",
            },
            {"is_superadmin_role": access.is_superadmin_role},
        )
        users = canonical_module(
            "devnous.gastos.routes.user_routes",
            "src/devnous/gastos/routes/user_routes.py",
            {"_can_read_documento"},
            {
                key: getattr(payment, key)
                for key in (
                    "require_payment_run_access",
                    "require_payment_run_payment_confirmation",
                    "PaymentRunPermissionError",
                )
            },
        )
        admin = canonical_module(
            "devnous.gastos.routes.admin_routes",
            "src/devnous/gastos/routes/admin_routes.py",
            {"_budget_can_view"},
        )
        legacy = canonical_module(
            "devnous.gastos.services.amex_statement_workflow_service",
            "src/devnous/gastos/services/amex_statement_workflow_service.py",
            {"_authorize", "_statement"},
            {
                **vars(models),
                "FINANCE_AMEX_ROLES": frozenset(
                    {"finanzas", "admin", "superadmin", "super_admin"}
                ),
            },
        )
        self.schema_calls = 0
        self.schema_admitted = True

        async def synthetic_schema_admission(session):
            self.schema_calls += 1
            if not self.schema_admitted:
                raise ReadError("SYNTHETIC_SCHEMA_NOT_ADMITTED")

        async def forbidden_lock(*args):
            raise AssertionError("read path attempted recognition lock")

        direct = canonical_module(
            "devnous.gastos.services.amex_direct_common",
            "src/devnous/gastos/services/amex_direct_common.py",
            {"load_statement", "digest"},
            {
                **vars(models),
                "hashlib": __import__("hashlib"),
                "explicit_tool_decision": access.explicit_tool_decision,
                "require_amex_direct_schema": synthetic_schema_admission,
                "lock_recognition": forbidden_lock,
            },
        )
        self.direct, self.deps, self.payment = direct, deps, payment
        routes = types.ModuleType("devnous.gastos.routes")
        routes.__path__ = []
        modules = {
            m.__name__: m
            for m in (
                models,
                access,
                deps,
                payment,
                users,
                admin,
                legacy,
                direct,
                routes,
            )
        }
        # vars(models) injection includes __name__; restore the canonical names.
        modules.update(
            {
                "devnous.gastos.services.amex_statement_workflow_service": legacy,
                "devnous.gastos.services.amex_direct_common": direct,
            }
        )
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "AMEX_DIRECT_AUTHORIZED_CONTEXTS": "[]",
                **{
                    key: ""
                    for key in payment.PAYMENT_RUN_MANAGER_ENV_KEYS
                    + payment.PAYMENT_RUN_PAYMENT_CONFIRMER_ENV_KEYS
                },
            },
        )
        self.env_patch.start()
        self.statements = []
        self.open_authorities = 0
        self.authority_opens = 0
        self.binding_value = ServerBinding(
            "synthetic-server-binding",
            UUID(ACTOR),
            "isolated",
            "1",
            "canon-v1",
            datetime.now(timezone.utc) + timedelta(hours=1),
            frozenset({DomainScope("solicitud", T, frozenset({A, B}))}),
        )
        self.resolver = CanonicalAuthorityResolver(
            lambda: self.binding_value, self.authority_scope
        )
        self.audit = MemoryAudit()
        self.gateway = compose_candidate(
            binding_provider=lambda: self.binding_value,
            authority_session_scope=self.authority_scope,
            data_session_scope=self.data_scope,
            audit=self.audit,
            source_commit=COMMIT,
        )

    def tearDown(self):
        self.env_patch.stop()
        self.module_patch.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def fixture(self, sql, **params):
        with self.engine.begin() as c:
            c.execute(sa.text(sql), params)

    def facade(self, session):
        test = self

        class ReadSession:
            async def execute(self, statement, params=None):
                sql = str(statement)
                test.assertTrue(sql.lstrip().upper().startswith("SELECT"), sql)
                test.assertNotIn("FOR UPDATE", sql.upper())
                test.assertNotIn("password_hash", sql)
                test.statements.append(sql)
                if isinstance(statement, sa.sql.elements.TextClause):
                    params = {
                        k: str(v) if isinstance(v, UUID) else v
                        for k, v in (params or {}).items()
                    }
                return session.execute(statement, params or {})

            async def get(self, model, identity, **kwargs):
                test.assertIsNot(model, test.models.Empleado)
                test.assertFalse(kwargs.get("with_for_update"))
                return session.get(model, identity, **kwargs)

        return ReadSession()

    @asynccontextmanager
    async def authority_scope(self):
        self.open_authorities += 1
        self.authority_opens += 1
        try:
            with Session(self.engine, autoflush=False) as session:
                session.execute(sa.text("PRAGMA query_only=ON"))
                try:
                    yield self.facade(session)
                finally:
                    session.rollback()
                    session.execute(sa.text("PRAGMA query_only=OFF"))
        finally:
            self.open_authorities -= 1

    @asynccontextmanager
    async def data_scope(self):
        self.assertEqual(self.open_authorities, 0)
        with Session(self.engine, autoflush=False) as session:
            session.execute(sa.text("PRAGMA query_only=ON"))
            try:
                yield self.facade(session)
            finally:
                session.rollback()
                session.execute(sa.text("PRAGMA query_only=OFF"))

    def scope(self, dataset, ids=(A,), version=""):
        self.binding_value = replace(
            self.binding_value,
            ceilings=frozenset({DomainScope(dataset, T, frozenset(ids), version)}),
        )

    async def test_solicitud_real_guards_sql_and_minimal_projection(self):
        result = await self.gateway.query(request(object_ids=[A]))
        self.assertEqual(result["rows"][0]["amount"], "12")
        self.assertEqual(self.authority_opens, 3)
        self.assertEqual(self.open_authorities, 0)
        self.assertNotIn("NEVER_READ", json.dumps(result))
        with self.assertRaisesRegex(ReadError, "NOT_FOUND_OR_NOT_VISIBLE"):
            await self.gateway.query(request())
        self.fixture(
            "UPDATE documentos SET empleado_id=:other WHERE id=:id", other=OTHER, id=A
        )
        with self.assertRaisesRegex(ReadError, "NOT_FOUND_OR_NOT_VISIBLE"):
            await self.gateway.query(request(object_ids=[A]))

    async def test_informe_visibility_does_not_grant_solicitud_or_wrong_tournament(
        self,
    ):
        self.fixture("UPDATE empleados SET correo='otrujillo@plataformasports.com'")
        self.fixture(
            "UPDATE documentos SET empleado_id=:other WHERE id=:id", other=OTHER, id=A
        )
        result = await self.resolver(Query.parse(request(object_ids=[A])))
        self.assertFalse(result.scopes)
        self.fixture("UPDATE empleados SET rol='superadmin'")
        self.fixture(
            "UPDATE documentos SET torneo_id=:other WHERE id=:id", other=OTHER, id=A
        )
        self.assertFalse(
            (await self.resolver(Query.parse(request(object_ids=[A])))).scopes
        )

    async def test_explicit_denial_profile_failure_and_binding_expiry(self):
        self.fixture(
            "INSERT INTO access_control_rules VALUES ('gastos.solicitudes','ver','empleado','operations',0,1,'2026-01-01')"
        )
        self.assertFalse(
            (await self.resolver(Query.parse(request(object_ids=[A])))).scopes
        )
        self.fixture("DROP TABLE access_profiles")
        with self.assertRaises(ReadError):
            await self.gateway.query(request(object_ids=[A]))
        self.binding_value = replace(
            self.binding_value,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        with self.assertRaises(ReadError):
            await self.gateway.capabilities_async({})

    async def test_budget_finance_payment_guards_and_version(self):
        self.fixture("INSERT INTO budget_versions VALUES (:id,'approved',2026)", id=A)
        self.fixture("INSERT INTO budget_lines VALUES (:id,:tor)", id=A, tor=T)
        self.scope("budget.snapshot", version=A)
        q = Query.parse(
            request(dataset="budget.snapshot", object_ids=[A], version_id=A)
        )
        self.assertFalse((await self.resolver(q)).scopes)
        self.fixture("UPDATE empleados SET departamento='Direccion'")
        self.assertTrue((await self.resolver(q)).permits(q))
        self.fixture("UPDATE budget_versions SET status='draft'")
        self.assertFalse((await self.resolver(q)).scopes)
        self.fixture("UPDATE budget_versions SET status='approved'")
        self.scope("ar.summary", version=A)
        q = replace(q, dataset="ar.summary")
        self.assertFalse((await self.resolver(q)).scopes)
        self.fixture(
            "INSERT INTO access_profiles VALUES ('p','[\"admin.finanzas.manage\"]',1)"
        )
        self.fixture("INSERT INTO empleado_access_profiles VALUES (:id,'p')", id=ACTOR)
        self.assertTrue((await self.resolver(q)).permits(q))
        self.fixture("UPDATE access_profiles SET active=0")
        self.assertFalse((await self.resolver(q)).scopes)
        self.scope("payment_run")
        q = Query.parse(request(dataset="payment_run", object_ids=[A]))
        self.assertFalse((await self.resolver(q)).scopes)
        self.fixture("UPDATE empleados SET rol='finanzas'")
        self.assertTrue((await self.resolver(q)).permits(q))
        actor = SimpleNamespace(
            id=UUID(ACTOR), rol="finanzas", departamento="", effective_permissions=set()
        )
        self.assertFalse(self.payment.can_manage_payment_run(actor))
        self.assertFalse(self.payment.can_confirm_payment_run_payment(actor))

    def amex_fixture(self, mode):
        self.fixture("UPDATE empleados SET rol='finanzas'")
        self.config = dict(
            entity_code="SYNTHETIC",
            company_code="SYNTHETIC",
            receiver_rfc="AAA010101AAA",
            card_account_ids=[CARD],
            actor_scopes=[{"actor_id": ACTOR}],
        )
        os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"] = json.dumps([self.config])
        with Session(self.engine) as session:
            session.add(
                self.models.AmexStatementImport(
                    id=UUID(A),
                    card_account_id=UUID(CARD),
                    workflow_mode=mode,
                    period_start=date(2026, 1, 1),
                    period_end=date(2026, 1, 31),
                    total_rows=2,
                    imported_count=1,
                    skipped_count=0,
                    credit_count=1,
                    unknown_count=0,
                )
            )
            session.add(
                self.models.AmexStatementDirectState(
                    import_id=UUID(A), context_id=UUID(CONTEXT)
                )
            )
            session.add(
                self.models.AmexStatementDirectContext(
                    id=UUID(CONTEXT),
                    company_code="SYNTHETIC",
                    receiver_rfc="AAA010101AAA",
                    config_hash=self.direct.digest(self.config),
                )
            )
            session.commit()
        self.scope("amex.statement")
        return request(dataset="amex.statement", object_ids=[A])

    async def test_amex_legacy_real_authorize_statement_and_denial(self):
        args = self.amex_fixture("legacy")
        result = await self.gateway.query(args)
        self.assertEqual(result["rows"][0]["workflow_mode"], "legacy")
        self.assertEqual(self.schema_calls, 0)
        self.fixture(
            "INSERT INTO access_control_rules VALUES ('admin.contabilidad','ver','finanzas','operations',0,1,'2026-01-01')"
        )
        with self.assertRaises(ReadError):
            await self.gateway.query(args)

    async def test_amex_direct_real_owner_scope_context_and_schema_boundary(self):
        args = self.amex_fixture("direct")
        result = await self.gateway.query(args)
        self.assertEqual(result["rows"][0]["workflow_mode"], "direct")
        self.assertEqual(self.schema_calls, 4)
        self.assertNotIn(CARD, json.dumps(result))
        self.schema_admitted = False
        with self.assertRaises(ReadError):
            await self.gateway.query(args)
        self.schema_admitted = True
        self.fixture("UPDATE amex_statement_direct_contexts SET company_code='OTHER'")
        with self.assertRaises(ReadError):
            await self.gateway.query(args)

    async def test_amex_direct_duplicate_config_actor_and_mode_rejected(self):
        args = self.amex_fixture("direct")
        os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"] = json.dumps(
            [self.config, self.config]
        )
        with self.assertRaises(ReadError):
            await self.gateway.query(args)
        os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"] = json.dumps(
            [{**self.config, "actor_scopes": [{"actor_id": OTHER}]}]
        )
        with self.assertRaises(ReadError):
            await self.gateway.query(args)
        os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"] = json.dumps([self.config])
        self.fixture("UPDATE amex_statement_imports SET workflow_mode='unknown'")
        with self.assertRaises(ReadError):
            await self.gateway.query(args)

    async def test_amex_direct_explicit_denial_before_schema(self):
        args = self.amex_fixture("direct")
        self.fixture(
            "INSERT INTO access_control_rules VALUES ('amex_direct','ver','finanzas','operations',0,1,'2026-01-01')"
        )
        with self.assertRaises(ReadError):
            await self.gateway.query(args)
        self.assertEqual(self.schema_calls, 0)

    async def test_revocation_committed_outside_real_data_snapshot(self):
        test = self

        class SnapshotSource:
            async def read(self, query, authority):
                with test.engine.connect() as old:
                    old.exec_driver_sql("BEGIN")
                    test.assertEqual(
                        old.exec_driver_sql(
                            "SELECT activo FROM empleados"
                        ).scalar_one(),
                        1,
                    )
                    test.fixture("UPDATE empleados SET activo=0")
                    test.assertEqual(
                        old.exec_driver_sql(
                            "SELECT activo FROM empleados"
                        ).scalar_one(),
                        1,
                    )
                    old.rollback()
                return Observation([], (), "a" * 64, "synthetic_snapshot")

        self.gateway.adapters["solicitud"] = SnapshotSource()
        with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED"):
            await self.gateway.query(request(object_ids=[A]))
        self.assertEqual(self.authority_opens, 2)

    async def test_revocation_after_audit_and_cancellation_close_sessions(self):
        original = self.audit.append

        def revoke(event):
            original(event)
            if event["decision"] == "allow":
                self.fixture("UPDATE empleados SET activo=0")

        self.audit.append = revoke
        with self.assertRaises(ReadError):
            await self.gateway.query(request(object_ids=[A]))
        self.assertEqual(self.authority_opens, 3)
        self.fixture("UPDATE empleados SET activo=1")

        async def cancel(session, identity, **kwargs):
            raise asyncio.CancelledError

        with patch.object(self.deps, "_load_effective_permissions", cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.gateway.query(request(object_ids=[A]))
        self.assertEqual(self.open_authorities, 0)

    async def test_capabilities_fresh_async_only_and_no_actor_arguments(self):
        result = await self.gateway.capabilities_async({})
        self.assertTrue(result["datasets"]["solicitud"]["candidate_readable"])
        self.assertFalse(result["production_activation"])
        with self.assertRaisesRegex(ReadError, "ASYNC_AUTHORITY_REQUIRED"):
            self.gateway.capabilities({})
        with self.assertRaisesRegex(ReadError, "INVALID_ARGUMENTS"):
            await self.gateway.capabilities_async({"actor": OTHER})
        self.binding_value = replace(
            self.binding_value,
            ceilings=frozenset(
                {
                    DomainScope(
                        "solicitud", T, frozenset(str(uuid4()) for _ in range(101))
                    )
                }
            ),
        )
        with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED"):
            await self.gateway.capabilities_async({})

    async def test_initial_identity_failure_audited_and_backend_details_redacted(self):
        def broken():
            raise RuntimeError("private backend detail")

        gateway = ExpandedReads(
            resolve=broken, adapters={}, audit=self.audit, source_commit=COMMIT
        )
        with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED") as caught:
            gateway.capabilities({})
        self.assertNotIn("private", str(caught.exception))
        with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED"):
            await gateway.query(request(object_ids=[A]))
        self.assertEqual(self.audit.events[-1]["decision"], "deny")
        self.assertNotIn("private", json.dumps(self.audit.events))
        self.fixture("UPDATE empleados SET activo=0")
        with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED"):
            await self.gateway.capabilities_async({})
        self.assertEqual(self.audit.events[-1]["decision"], "deny")

    async def test_amex_context_revocation_after_allow_prevents_delivery(self):
        args = self.amex_fixture("direct")
        append = self.audit.append

        def revoke(event):
            append(event)
            if event["decision"] == "allow":
                os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"] = "[]"

        self.audit.append = revoke
        with self.assertRaisesRegex(ReadError, "AUTH_REQUIRED"):
            await self.gateway.query(args)
        self.assertEqual(self.audit.events[-1]["decision"], "deny")

    async def test_server_employee_rebinding_and_ceiling_removal_prevent_delivery(self):
        append = self.audit.append

        def revoke(event):
            append(event)
            if event["decision"] == "allow":
                self.binding_value = replace(self.binding_value, ceilings=frozenset())

        self.audit.append = revoke
        with self.assertRaisesRegex(ReadError, "AUTH_CHANGED"):
            await self.gateway.query(request(object_ids=[A]))
        self.assertEqual(self.audit.events[-1]["decision"], "deny")
        self.scope("solicitud")
        self.fixture(
            "INSERT INTO empleados VALUES (:id,1,'finanzas','operations','other@example.invalid','NEVER_READ')",
            id=OTHER,
        )

        def rebind(event):
            append(event)
            if event["decision"] == "allow":
                self.binding_value = replace(
                    self.binding_value, employee_id=UUID(OTHER)
                )

        self.audit.append = rebind
        with self.assertRaisesRegex(ReadError, "AUTH_CHANGED"):
            await self.gateway.query(request(object_ids=[A]))
        self.assertEqual(self.audit.events[-1]["decision"], "deny")
