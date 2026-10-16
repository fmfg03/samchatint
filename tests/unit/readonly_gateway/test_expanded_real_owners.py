"""Canonical owner code and SQL on synthetic databases, without app bootstrap.

AST loading removes only unused imports that bootstrap the application (and the
spreadsheet exporter dependency). Owner functions/SQL bodies are unmodified.
PGlite transport is a test adapter, NOT an AsyncSession/native-driver acceptance.
AMEX uses the original mapped class and a real SQLAlchemy SQLite Session.get;
the awaitable facade and JSONB-as-JSON compilation are test-only transport.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import types
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, declarative_base

from samchat.readonly_gateway.contracts import ReadError
from samchat.readonly_gateway.expanded_adapters import CanonicalDatasetAdapter
from samchat.readonly_gateway.expanded_catalog import Query
from test_postgres_disposable import Engine

ROOT = Path(__file__).resolve().parents[3]
T = "10000000-0000-4000-8000-000000000001"
T2 = "10000000-0000-4000-8000-000000000002"
V = "60000000-0000-4000-8000-000000000001"
V2 = "60000000-0000-4000-8000-000000000002"
LINE = "61000000-0000-4000-8000-000000000001"
INCOME = "61000000-0000-4000-8000-000000000002"
CFDI = "62000000-0000-4000-8000-000000000001"
LINK = "63000000-0000-4000-8000-000000000001"
DOC = "20000000-0000-4000-8000-000000000001"


def load_owner(name, path, excluded=(), only=None, injected=None):
    path = ROOT / path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if only is not None:
        tree.body = [n for n in tree.body if getattr(n, "name", None) in only]
        tree.body.insert(
            0,
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
        )
    else:
        tree.body = [
            n
            for n in tree.body
            if not (
                isinstance(n, ast.ImportFrom)
                and n.module in {*excluded, "sqlalchemy.ext.asyncio"}
            )
        ]
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition(".")[0]
    module.__dict__.update(injected or {})
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)
    return module


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class PgTransport:
    """Execute actual SQL; never substitute result rows or branch on query text."""

    def __init__(self, engine):
        self.engine = engine
        self.statements = []

    async def execute(self, statement, params=None):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.statements.append(sql)
        if not re.match(r"^\s*SELECT\b", sql, re.I):
            raise AssertionError("Owner attempted non-SELECT in read transaction")
        values = {
            k: v.isoformat() if isinstance(v, (date, datetime)) else v
            for k, v in (params or compiled.params).items()
        }
        result = self.engine.query(sql, values)
        # PGlite's JSON bridge serializes PostgreSQL timestamps; restore the
        # datetime type normally returned by the driver, without altering values.
        for row in result["rows"]:
            for key, value in row.items():
                if isinstance(value, str) and re.fullmatch(
                    r"\d{4}-\d\d-\d\dT.*", value
                ):
                    row[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return Rows(result["rows"])


def model_columns(class_name):
    """Canonical column definitions; relaxed fixture constraints, no model init."""
    path = ROOT / "src/devnous/gastos/models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name
    )
    namespace = {
        **vars(sa),
        "UUID": postgresql.UUID,
        "JSONB": postgresql.JSONB,
        "uuid4": uuid4,
        "datetime": datetime,
        "resolved_receipt_blob_column_for_orm": lambda: "archivo_data",
    }
    columns = {}
    for item in node.body:
        if (
            isinstance(item, ast.Assign)
            and isinstance(item.value, ast.Call)
            and isinstance(item.value.func, ast.Name)
            and item.value.func.id == "Column"
        ):
            value = eval(
                compile(ast.Expression(item.value), str(path), "eval"), namespace
            )
            columns[item.targets[0].id] = value.type.compile(
                dialect=postgresql.dialect()
            )
    return columns


def query(dataset, version=V, tournament=T):
    return Query.parse(
        {
            "dataset": dataset,
            "object_ids": [version],
            "version_id": version,
            "tournament_id": tournament,
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
        }
    )


class RealBudgetArOwners(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        identity = load_owner(
            "identity",
            "src/devnous/gastos/services/amex_accounting_identity_service.py",
            only={"accounting_expense_sql"},
            injected={"re": re},
        )
        cls.budget = load_owner(
            "samchat.budgets.service",
            "src/samchat/budgets/service.py",
            excluded={
                "openpyxl",
                "devnous.gastos.services.amex_accounting_identity_service",
            },
            injected={"accounting_expense_sql": identity.accounting_expense_sql},
        )
        cls.bridge = load_owner(
            "devnous.gastos.services.cfdi_income_bridge_service",
            "src/devnous/gastos/services/cfdi_income_bridge_service.py",
            excluded={"cfdi_ingestion_service"},
        )
        cls.matches = load_owner(
            "samchat.ar.collection_matches", "src/samchat/ar/collection_matches.py"
        )
        cls.ar = load_owner("samchat.ar.service", "src/samchat/ar/service.py")
        cls.modules = {
            m.__name__: m for m in (cls.budget, cls.bridge, cls.matches, cls.ar)
        }
        cls.engine = Engine()
        e = cls.engine
        e.query("RESET ROLE")
        for table, model in (
            ("documentos", "Documento"),
            ("expense_reports", "ExpenseReport"),
            ("cfdi_reports", "CFDIReport"),
            ("tournaments", "Tournament"),
            ("proveedores_clientes", "ProveedorCliente"),
            ("adjuntos", "Adjunto"),
        ):
            columns = model_columns(model)
            e.query(f"CREATE TABLE IF NOT EXISTS {table}(id uuid PRIMARY KEY)")
            for name, datatype in columns.items():
                e.query(
                    f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{name}" {datatype}'
                )
        # Only canonical CREATE TABLE strings used by the selected reads. Foreign
        # keys are omitted in this synthetic fixture; no schema owner is invoked.
        wanted = {
            "budget_concepts",
            "budget_versions",
            "budget_lines",
            "budget_approvals",
            "budget_line_monthly_plan",
            "budget_cfdi_income_links",
            "cfdi_income_tournament_assignments",
            "ar_collection_matches",
        }
        for file in (
            "src/samchat/budgets/service.py",
            "src/devnous/gastos/services/cfdi_income_bridge_service.py",
            "src/samchat/ar/collection_matches.py",
        ):
            tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(
                    node.value, str
                ):
                    continue
                match = re.match(
                    r"\s*CREATE TABLE IF NOT EXISTS (\w+)\s*\(", node.value
                )
                if match and match[1] in wanted:
                    sql = re.sub(
                        r"REFERENCES\s+\w+\s*\(\w+\)(?:\s+ON (?:DELETE|UPDATE) (?:SET NULL|CASCADE|RESTRICT|NO ACTION))*",
                        "",
                        node.value,
                    )
                    try:
                        e.query(sql)
                    except RuntimeError as exc:
                        raise AssertionError(
                            f"Fixture schema {match[1]}: {sql}"
                        ) from exc
                    wanted.remove(match[1])
        if wanted:
            raise AssertionError(f"Missing canonical fixture schemas: {wanted}")
        for sql in (
            "CREATE TABLE amex_recognition_consumptions(id uuid,imported_expense_id uuid,source_kind text,binding_mode text)",
            "CREATE TABLE rfc_configs(tax_id text,name text,active boolean,display_order integer)",
            f"INSERT INTO tournaments(id,name) VALUES('{T}','Synthetic'),('{T2}','Other')",
            f"INSERT INTO budget_versions(id,edition_year,version_name,status) VALUES('{V}',2026,'Synthetic','approved'),('{V2}',2026,'Other','draft')",
            f"INSERT INTO budget_lines(id,budget_version_id,tournament_id,tournament_name,concept_name,line_direction,budget_amount) VALUES('{LINE}','{V}','{T}','Synthetic','Expenses','expense',1000),('{INCOME}','{V}','{T}','Synthetic','Income','income',2000)",
            f"INSERT INTO budget_line_monthly_plan(id,budget_line_id,month_number,budget_expense_amount,expected_income_amount) VALUES('{uuid4()}','{INCOME}',1,0,2000)",
            f"UPDATE documentos SET tipo='SOLICITUD',estado='aprobado',monto_total=116,monto_solicitado=116,budget_concept_id='{LINE}',creado_en='2026-01-10' WHERE id='{DOC}'",
            f"DELETE FROM documentos WHERE id<>'{DOC}'",
            f"INSERT INTO expense_reports(id,documento_id,estado_gasto,fecha,gasto_cantidad,iva,budget_concept_id,currency) VALUES('{uuid4()}','{DOC}','aprobado','2026-01-12',58,8,'{LINE}','MXN')",
            "INSERT INTO rfc_configs VALUES('AAA010101AAA','Synthetic',TRUE,1)",
            f"INSERT INTO cfdi_reports(id,cfdi_uuid,fecha,total,moneda,emisor_rfc,tipo_de_comprobante) VALUES('{CFDI}','synthetic', '2026-01-10',116,'MXN','AAA010101AAA','I')",
            f"INSERT INTO cfdi_income_tournament_assignments(id,cfdi_report_id,tournament_id) VALUES('{uuid4()}','{CFDI}','{T}')",
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO fixture_reader",
            "SET ROLE fixture_reader",
        ):
            e.query(sql)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    async def read(self, q):
        transport = PgTransport(self.engine)

        @asynccontextmanager
        async def session_scope():
            yield transport

        self.engine.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            with patch.dict(sys.modules, self.modules):
                result = await CanonicalDatasetAdapter(session_scope)._read(
                    transport, q
                )
            self.assertTrue(transport.statements)
            return result
        finally:
            self.engine.query("ROLLBACK")

    async def test_budget_real_owner_totals_and_scope(self):
        result = await self.read(query("budget.snapshot"))
        self.assertEqual(result.rows[0]["budget_total"], 1000)
        self.assertEqual(result.rows[0]["committed_total"], 116)
        self.assertEqual(result.rows[0]["actual_total"], 50)
        for q in (
            query("budget.snapshot", tournament=T2),
            query("budget.snapshot", version=V2),
        ):
            with self.assertRaises(ReadError):
                await self.read(q)

    async def test_ar_real_owner_candidates_and_scope(self):
        result = await self.read(query("ar.summary"))
        self.assertEqual(result.rows[0]["expected_income_total"], 2000)
        self.assertEqual(result.rows[0]["invoiced_total"], 116)
        self.assertEqual(result.rows[0]["collected_total"], 0)
        other = await self.read(query("ar.summary", tournament=T2))
        self.assertEqual(other.rows[0]["invoiced_total"], 0)
        with self.assertRaisesRegex(ReadError, "SOURCE_VERSION_UNPROVEN"):
            await self.read(query("ar.summary", version=V2))

    def edit_fixture(self, sql):
        self.engine.query("RESET ROLE")
        try:
            self.engine.query(sql)
        finally:
            self.engine.query("SET ROLE fixture_reader")

    async def test_budget_period_and_real_currency_gap(self):
        february = replace(
            query("budget.snapshot"), start=date(2026, 2, 1), end=date(2026, 3, 1)
        )
        result = await self.read(february)
        self.assertEqual(result.rows[0]["budget_total"], 1000)
        self.assertEqual(result.rows[0]["committed_total"], 0)
        self.assertEqual(result.rows[0]["actual_total"], 0)
        self.edit_fixture(f"UPDATE documentos SET currency=NULL WHERE id='{DOC}'")
        try:
            result = await self.read(query("budget.snapshot"))
            self.assertIsNone(result.rows[0]["committed_total"])
            self.assertIn("canonical_budget_quality_gaps_totals_withheld", result.gaps)
        finally:
            self.edit_fixture(f"UPDATE documentos SET currency='MXN' WHERE id='{DOC}'")

    async def test_ar_real_links_collection_and_reversal(self):
        self.edit_fixture(
            f"INSERT INTO budget_cfdi_income_links(id,cfdi_report_id,budget_line_id,budget_version_id,tournament_id,amount,income_date,status) VALUES('{LINK}','{CFDI}','{INCOME}','{V}','{T}',116,'2026-01-10','approved')"
        )
        try:
            self.edit_fixture(
                f"INSERT INTO ar_collection_matches(ar_item_id,budget_version_id,bank_movement_id,accepted_amount,status,acceptance_reason) VALUES('linked:{LINK}','{V}','{uuid4()}',50,'accepted_collection_match','synthetic')"
            )
            result = await self.read(query("ar.summary"))
            self.assertEqual(result.rows[0]["invoiced_total"], 116)
            self.assertEqual(result.rows[0]["collected_total"], 50)
            self.assertEqual(result.rows[0]["balance_total"], 66)
            self.edit_fixture(f"INSERT INTO ar_collection_matches(ar_item_id,budget_version_id,bank_movement_id,accepted_amount,status,acceptance_reason) VALUES('linked:{LINK}','{V2}','{uuid4()}',99,'accepted_collection_match','other version'),('linked:outside','{V}','{uuid4()}',99,'accepted_collection_match','other item'),('linked:outside','{V}','{uuid4()}',10,'accepted_collection_match','other item duplicate')")
            result = await self.read(query("ar.summary"))
            self.assertEqual(result.rows[0]["collected_total"], 50)
            self.edit_fixture(
                f"INSERT INTO ar_collection_matches(ar_item_id,budget_version_id,bank_movement_id,accepted_amount,status,acceptance_reason) VALUES('linked:{LINK}','{V}','{uuid4()}',20,'accepted_collection_match','synthetic second payment')"
            )
            result = await self.read(query("ar.summary"))
            self.assertIsNone(result.rows[0]["collected_total"])
            self.assertIsNone(result.rows[0]["balance_total"])
            self.assertIn("canonical_ar_multiple_matches_totals_withheld", result.gaps)
            self.edit_fixture(
                "UPDATE ar_collection_matches SET status='match_reversed' WHERE accepted_amount=20"
            )
            result = await self.read(query("ar.summary"))
            self.assertEqual(result.rows[0]["collected_total"], 50)
            self.edit_fixture(
                "UPDATE ar_collection_matches SET status='match_reversed'"
            )
            result = await self.read(query("ar.summary"))
            self.assertEqual(result.rows[0]["collected_total"], 0)
            self.edit_fixture(f"UPDATE cfdi_reports SET moneda='USD' WHERE id='{CFDI}'")
            result = await self.read(query("ar.summary"))
            self.assertIsNone(result.rows[0]["invoiced_total"])
        finally:
            self.edit_fixture("DELETE FROM ar_collection_matches")
            self.edit_fixture("DELETE FROM budget_cfdi_income_links")
            self.edit_fixture(f"UPDATE cfdi_reports SET moneda='MXN' WHERE id='{CFDI}'")

    async def test_real_owner_reads_cannot_execute_schema(self):
        self.engine.query("BEGIN READ ONLY")
        try:
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                self.engine.query("CREATE TABLE forbidden_owner_write(id int)")
        finally:
            self.engine.query("ROLLBACK")
        transport = PgTransport(self.engine)
        with self.assertRaisesRegex(AssertionError, "non-SELECT"):
            await self.budget.ensure_budget_schema(transport)

    async def test_ar_real_candidate_limit_withholds_partial_totals(self):
        self.edit_fixture(
            "INSERT INTO cfdi_reports(id,fecha,total,moneda,emisor_rfc,tipo_de_comprobante) SELECT gen_random_uuid(),'2026-01-12',10,'MXN','AAA010101AAA','I' FROM generate_series(1,500)"
        )
        self.edit_fixture(
            f"INSERT INTO cfdi_income_tournament_assignments(id,cfdi_report_id,tournament_id) SELECT gen_random_uuid(),id,'{T}' FROM cfdi_reports WHERE id<>'{CFDI}'"
        )
        try:
            result = await self.read(query("ar.summary"))
            self.assertIsNone(result.rows[0]["invoiced_total"])
            self.assertIn(
                "canonical_ar_quality_or_coverage_gaps_totals_withheld", result.gaps
            )
        finally:
            self.edit_fixture(
                f"DELETE FROM cfdi_income_tournament_assignments WHERE cfdi_report_id<>'{CFDI}'"
            )
            self.edit_fixture(f"DELETE FROM cfdi_reports WHERE id<>'{CFDI}'")


@compiles(postgresql.JSONB, "sqlite")
def jsonb_fixture(element, compiler, **kw):
    return "JSON"


@compiles(postgresql.UUID, "sqlite")
def uuid_fixture(element, compiler, **kw):
    return "CHAR(32)"


class RealAmexOwner(unittest.IsolatedAsyncioTestCase):
    async def test_original_owner_and_model_real_get_minimization(self):
        base = declarative_base()
        model = load_owner(
            "isolated_amex_model",
            "src/devnous/gastos/models.py",
            only={"AmexStatementImport"},
            injected={
                **vars(sa),
                "UUID": postgresql.UUID,
                "JSONB": postgresql.JSONB,
                "uuid4": uuid4,
                "Base": base,
            },
        )
        owner = load_owner(
            "devnous.gastos.services.amex_statement_import_service",
            "src/devnous/gastos/services/amex_statement_import_service.py",
            only={"get_amex_statement_import"},
            injected={"AmexStatementImport": model.AmexStatementImport},
        )
        engine = sa.create_engine("sqlite://")
        # Required FK metadata for the unchanged canonical mapped class.
        for table in ("amex_card_accounts", "empleados"):
            sa.Table(
                table, base.metadata, sa.Column("id", postgresql.UUID, primary_key=True)
            )
        base.metadata.create_all(engine)
        identifier = UUID(V)
        with Session(engine) as session:
            session.add(
                model.AmexStatementImport(
                    id=identifier,
                    card_account_id=uuid4(),
                    employee_id=uuid4(),
                    actor_id=uuid4(),
                    period_start=date(2026, 1, 1),
                    period_end=date(2026, 1, 31),
                    file_sha256="a" * 64,
                    filename="PRIVATE.csv",
                    parser_version="amex-csv-v1",
                    workflow_mode="direct",
                    total_rows=3,
                    imported_count=1,
                    skipped_count=0,
                    credit_count=1,
                    unknown_count=1,
                    summary_json={"secret": "must_not_escape"},
                )
            )
            session.commit()
        statements = []

        @sa.event.listens_for(engine, "before_cursor_execute")
        def select_only(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
            if not statement.lstrip().upper().startswith("SELECT"):
                raise AssertionError("AMEX owner attempted a write")

        with Session(engine, autoflush=False) as session:

            class AwaitableGet:
                async def get(self, mapped, key):
                    return session.get(mapped, key)

            q = Query.parse(
                {
                    "dataset": "amex.statement",
                    "object_ids": [V],
                    "tournament_id": T,
                    "date_from": "2026-01-01",
                    "date_to": "2026-02-01",
                }
            )

            # Deliberately synthetic authorization binding: validates the getter
            # and projection only, not canonical direct/legacy authority.
            async def synthetic_authorized_reader(session, authority, identifier):
                return await owner.get_amex_statement_import(session, identifier)

            with patch.dict(sys.modules, {owner.__name__: owner}):
                with self.assertRaisesRegex(ReadError, "AMEX_AUTHORITY_UNBOUND"):
                    await CanonicalDatasetAdapter(None)._read(AwaitableGet(), q)
                result = await CanonicalDatasetAdapter(
                    None, amex_authorized_reader=synthetic_authorized_reader
                )._read(AwaitableGet(), q, object())
            self.assertEqual(result.rows[0]["total_rows"], 3)
            self.assertEqual(result.rows[0]["credit_count"], 1)
            self.assertNotIn("PRIVATE", json.dumps(result.rows))
            self.assertNotIn("must_not_escape", json.dumps(result.rows))
            self.assertEqual(len(statements), 1)
            self.assertIn("WHERE amex_statement_imports.id = ?", statements[0])
        engine.dispose()
