"""Actual canonical Payment Run SELECT in disposable PostgreSQL/WASM.

The function and its pure helpers are compiled from their inspected AST to avoid
application bootstrap. There is no replacement SQL or production connection.
"""

from __future__ import annotations

import ast
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from test_postgres_disposable import Engine

ROOT = Path(__file__).resolve().parents[3]
DOC = "20000000-0000-4000-8000-000000000001"
OTHER = "20000000-0000-4000-8000-000000000002"
TOURNAMENT = "10000000-0000-4000-8000-000000000001"


class PaymentRunPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine()
        # Own disposable fixture only. Never runs against an external database.
        cls.engine.query("RESET ROLE")
        statements = [
            "ALTER TABLE documentos ADD COLUMN empleado_id uuid, ADD COLUMN beneficiario_empleado_id uuid, ADD COLUMN proveedor_cliente_id uuid, ADD COLUMN fecha_pago date, ADD COLUMN fecha_pago_efectiva date, ADD COLUMN aprobado_en timestamptz, ADD COLUMN pago_urgente boolean, ADD COLUMN monto_solicitado numeric",
            "ALTER TABLE empleados ADD COLUMN nombre text",
            "CREATE TABLE proveedores_clientes(id uuid,nombre text)",
            "CREATE TABLE payment_run_closures(id uuid,closed_at timestamptz,run_date date)",
            "CREATE TABLE payment_run_closure_items(documento_id uuid,closure_id uuid)",
            "UPDATE documentos SET tipo='SOLICITUD',estado='en_proceso_pago',empleado_id='40000000-0000-4000-8000-000000000001',fecha_pago='2026-01-15',monto_total=12.50",
            "UPDATE documentos SET monto_total=NULL,monto_solicitado=NULL WHERE id='20000000-0000-4000-8000-000000000002'",
            "GRANT SELECT ON proveedores_clientes,payment_run_closures,payment_run_closure_items TO fixture_reader",
            "SET ROLE fixture_reader",
        ]
        for statement in statements:
            cls.engine.query(statement)
        path = ROOT / "src/devnous/gastos/services/payment_run_service.py"
        source = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            "list_payment_run_items",
            "_parse_uuid",
            "_money",
            "_status_for_row",
            "payment_run_cutoff_date",
            "parse_payment_run_date",
        }
        tree = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__", names=[ast.alias(name="annotations")], level=0
                )
            ]
            + [
                node
                for node in source.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names
            ],
            type_ignores=[],
        )
        cls.ddl_calls = 0

        async def forbidden_schema(session):
            cls.ddl_calls += 1
            raise AssertionError("DDL must never be attempted by the readonly path")

        namespace = {
            "text": text,
            "UUID": UUID,
            "Decimal": Decimal,
            "date": date,
            "datetime": datetime,
            "EMPLOYEE_REIMBURSEMENT_PREFIX": "reembolso",
            "ensure_payment_run_schema": forbidden_schema,
        }
        exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
        cls.read = staticmethod(namespace["list_payment_run_items"])

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    async def test_real_query_exact_scope_period_and_zero_ddl(self):
        engine = self.engine
        seen = []

        class Rows:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def all(self):
                return self.rows

        class Session:
            async def execute(self, statement, params):
                sql = str(statement.compile(dialect=postgresql.dialect()))
                seen.append(sql)
                values = {
                    k: v.isoformat() if isinstance(v, date) else v
                    for k, v in params.items()
                }
                return Rows(engine.query(sql, values)["rows"])

        engine.query("BEGIN READ ONLY")
        try:
            rows = await self.read(
                Session(),
                status_filter="todas",
                ensure_schema=False,
                document_ids=[DOC],
                tournament_id=TOURNAMENT,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
            )
            self.assertEqual([str(row["id"]) for row in rows], [DOC])
            self.assertEqual(rows[0]["monto"], Decimal("12.50"))
            self.assertIsNone(rows[0]["confirmation_date"])
            missing = await self.read(
                Session(),
                status_filter="todas",
                ensure_schema=False,
                document_ids=[OTHER],
                tournament_id=TOURNAMENT,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
            )
            self.assertIs(missing[0]["source_amount_missing"], True)
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                engine.query("UPDATE documentos SET monto_total=0")
        finally:
            engine.query("ROLLBACK")
        self.assertEqual(self.ddl_calls, 0)
        self.assertEqual(len(seen), 2)
        self.assertIn("ANY(CAST(", seen[0])
        self.assertNotIn(DOC, seen[0])
        self.assertEqual(
            await self.read(Session(), ensure_schema=False, document_ids=[]), []
        )
        with self.assertRaises(ValueError):
            await self.read(
                Session(), ensure_schema=False, document_ids=["bad' OR true"]
            )

    async def test_legacy_default_retains_schema_behavior(self):
        with self.assertRaisesRegex(AssertionError, "DDL must never"):
            await self.read(object())
        # The counter is reset because the explicit negative legacy check is separate.
        type(self).ddl_calls = 0
