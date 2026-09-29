"""Behavioral regressions for allocating a CFDI across expense reports.

Load the route functions without booting the composite web app. The executed
functions and fiscal arithmetic are the production source, not test replicas.
"""

import ast
import asyncio
import importlib.util
import sys
import unittest
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[3]
ROUTE_PATH = ROOT / "src/devnous/gastos/routes/user_routes.py"


def _load_functions():
    path = ROOT / "src/devnous/gastos/services/cfdi_autofill.py"
    spec = importlib.util.spec_from_file_location("_shared_invoice_autofill", path)
    autofill = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = autofill
    spec.loader.exec_module(autofill)
    names = {
        "_quick_expense_decimal",
        "_quick_expense_values",
        "_validate_quick_shared_cfdi_amount",
        "_informe_expense_export_amounts",
    }
    source = ast.parse(ROUTE_PATH.read_text(encoding="utf-8"))
    selected = [n for n in source.body if getattr(n, "name", None) in names]
    scope = {
        "Decimal": Decimal,
        "ROUND_HALF_UP": ROUND_HALF_UP,
        "datetime": datetime,
        "Optional": Optional,
        "Dict": Dict,
        "List": List,
        "Any": Any,
        "AsyncSession": object,
        "ExpenseReport": object,
        "CuentaDeGastos": object,
        "CFDIReport": object,
        "normalize_cfdi_uuid_to_canonical": lambda value: str(UUID(value)).upper(),
        "is_cfdi_uuid_prefix_candidate": lambda value: False,
        "compute_quick_expense_total": autofill.compute_quick_expense_total,
        "quick_expense_tax_components_from_parsed": (
            autofill.quick_expense_tax_components_from_parsed
        ),
        "currency_for": lambda cuenta: cuenta.currency,
        "validate_shared_cfdi_payment_amount": AsyncMock(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROUTE_PATH), "exec"), scope)
    accounting_path = ROOT / "src/devnous/gastos/services/expense_accounting_service.py"
    accounting_source = ast.parse(accounting_path.read_text(encoding="utf-8"))
    accounting_names = {
        "_money", "summarize_cfdi_tax_components",
        "summarize_expense_cfdi_tax_components",
    }
    accounting_functions = [
        n for n in accounting_source.body
        if getattr(n, "name", None) in accounting_names
    ]
    exec(compile(ast.Module(body=accounting_functions, type_ignores=[]),
                 str(accounting_path), "exec"), scope)
    return scope


class SharedInvoiceQuickCaptureTests(unittest.TestCase):
    def setUp(self):
        self.scope = _load_functions()
        self.fiscal = {
            "fecha": datetime(2026, 9, 25),
            "cfdi_uuid": "0C568D02-1111-2222-3333-444444444444",
            "subtotal": "11522.41",
            "descuento": "0.00",
            "total": "13366.00",
            "total_impuestos_trasladados": "1843.59",
            "impuestos_detalle": {
                "traslados": [{"impuesto": "002", "importe": "1843.59"}],
                "retenciones": [],
            },
        }

    def values(self, **overrides):
        args = {
            "concepto": "Alimentos en campo",
            "fecha": "2026-09-25",
            "numero_factura": None,
            "subtotal": "5000.00",
            "descuento": "0.00",
            "impuestos_y_retenciones": "800.00",
            "xml_data": self.fiscal,
            "factura_compartida": True,
        }
        args.update(overrides)
        return self.scope["_quick_expense_values"](**args)

    def test_first_capture_preserves_partial_amount_and_fiscal_total(self):
        values = self.values()
        self.assertEqual(values["total"], Decimal("5800.00"))
        self.assertEqual(values["iva"], Decimal("800.00"))
        self.assertEqual(values["numero_factura"], self.fiscal["cfdi_uuid"])
        self.assertEqual(self.fiscal["total"], "13366.00")

    def test_two_reports_allocate_the_invoice_exactly(self):
        first = self.values()
        second = self.values(subtotal="6522.41", impuestos_y_retenciones="1043.59")
        self.assertEqual(second["total"], Decimal("7566.00"))
        self.assertEqual(first["total"] + second["total"], Decimal("13366.00"))

    def test_unshared_capture_keeps_fiscal_authority(self):
        values = self.values(factura_compartida=False)
        self.assertEqual(values["total"], Decimal("13366.00"))

    def test_shared_capture_requires_explicit_amount(self):
        with self.assertRaisesRegex(ValueError, "Sub total aplicado es requerido"):
            self.values(subtotal=None)

    def test_shared_capture_rejects_zero_negative_overallocation_and_nonfinite(self):
        for subtotal in ("0", "-1", "13366.01", "NaN", "Infinity"):
            with self.subTest(subtotal=subtotal), self.assertRaises(ValueError):
                self.values(subtotal=subtotal, impuestos_y_retenciones="0")

    def test_shared_capture_keeps_discount_and_net_withholdings(self):
        values = self.values(subtotal="6000", descuento="100", impuestos_y_retenciones="-100")
        self.assertEqual(values["total"], Decimal("5800.00"))

    def test_tip_is_separate_from_fiscal_application(self):
        values = self.values(propina_no_deducible="50")
        self.assertEqual(values["total"], Decimal("5850.00"))
        self.assertEqual(values["propina"], Decimal("50.00"))
        self.assertEqual(values["iva"], Decimal("800.00"))

    def test_shared_capture_still_rejects_inconsistent_fiscal_source(self):
        with self.assertRaisesRegex(ValueError, "TOTAL del XML no coincide"):
            self.values(xml_data={**self.fiscal, "total": "13365.00"})

    def test_export_uses_applied_share_and_tip(self):
        expense = SimpleNamespace(
            gasto_cantidad=5850, propina_no_deducible=50,
            cfdi_compartido_confirmado=True, iva=800,
        )
        report = SimpleNamespace(total=13366, subtotal=11522.41, descuento=0)
        values = self.scope["_informe_expense_export_amounts"](expense, report, None)
        self.assertEqual(values, {"importe_sin_iva": 5050.0, "iva": 800.0, "total": 5850.0})

    def test_accounting_uses_partial_taxes_without_mutating_fiscal_evidence(self):
        report = SimpleNamespace(
            total=1120, subtotal=1000, descuento=0,
            total_impuestos_trasladados=160,
            impuestos_detalle={
                "traslados": [{"impuesto": "002", "importe": 160}],
                "retenciones": [{"impuesto": "001", "importe": 40}],
            },
        )
        expense = SimpleNamespace(
            gasto_cantidad=590, propina_no_deducible=30,
            cfdi_compartido_confirmado=True, iva=80,
        )
        taxes = self.scope["summarize_expense_cfdi_tax_components"](expense, report)
        self.assertEqual(taxes["iva_trasladado"], 80.0)
        self.assertEqual(taxes["retenciones_total"], 20.0)
        self.assertEqual(taxes["retenciones"][0]["importe"], 20.0)
        self.assertEqual(report.total, 1120)
        self.assertEqual(report.impuestos_detalle["retenciones"][0]["importe"], 40)

    def test_nonshared_accounting_keeps_existing_fallback(self):
        expense = SimpleNamespace(iva=16, cfdi_compartido_confirmado=False)
        taxes = self.scope["summarize_expense_cfdi_tax_components"](expense, None)
        self.assertEqual(taxes["iva_trasladado"], 16.0)

    def test_reservation_excludes_current_expense_and_tip(self):
        expense = SimpleNamespace(id=uuid4(), cfdi_report_id=uuid4())
        report = SimpleNamespace(id=expense.cfdi_report_id, moneda="MXN")
        session = SimpleNamespace(get=AsyncMock(return_value=report))
        cuenta = SimpleNamespace(currency="MXN")
        asyncio.run(self.scope["_validate_quick_shared_cfdi_amount"](
            session, expense, cuenta, self.values(propina_no_deducible="50")
        ))
        self.scope["validate_shared_cfdi_payment_amount"].assert_awaited_once_with(
            session, cfdi_report=report, requested_amount=Decimal("5800.00"),
            exclude_expense_id=expense.id,
        )

    def test_reservation_requires_linked_cfdi_and_matching_currency(self):
        for report_id, report in (
            (None, None),
            (uuid4(), None),
            (uuid4(), SimpleNamespace(moneda="USD")),
        ):
            with self.subTest(report_id=report_id, report=report), self.assertRaises(ValueError):
                asyncio.run(self.scope["_validate_quick_shared_cfdi_amount"](
                    SimpleNamespace(get=AsyncMock(return_value=report)),
                    SimpleNamespace(id=uuid4(), cfdi_report_id=report_id),
                    SimpleNamespace(currency="MXN"), self.values(),
                ))


if __name__ == "__main__":
    unittest.main()
