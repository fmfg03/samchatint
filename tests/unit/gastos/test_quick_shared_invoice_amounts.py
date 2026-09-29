"""Behavioral regressions for allocating a CFDI across expense reports.

Load the route functions without booting the composite web app. The executed
functions and fiscal arithmetic are the production source, not test replicas.
"""

import ast
import asyncio
import importlib.util
import inspect
import sys
import unittest
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

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
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(ROUTE_PATH), "exec"),
        scope,
    )
    accounting_path = ROOT / "src/devnous/gastos/services/expense_accounting_service.py"
    accounting_source = ast.parse(accounting_path.read_text(encoding="utf-8"))
    accounting_names = {
        "_money",
        "summarize_cfdi_tax_components",
        "summarize_expense_cfdi_tax_components",
    }
    accounting_functions = [
        n
        for n in accounting_source.body
        if getattr(n, "name", None) in accounting_names
    ]
    exec(
        compile(
            ast.Module(body=accounting_functions, type_ignores=[]),
            str(accounting_path),
            "exec",
        ),
        scope,
    )
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
        values = self.values(
            subtotal="6000", descuento="100", impuestos_y_retenciones="-100"
        )
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
            gasto_cantidad=5850,
            propina_no_deducible=50,
            cfdi_compartido_confirmado=True,
            iva=800,
        )
        report = SimpleNamespace(total=13366, subtotal=11522.41, descuento=0)
        values = self.scope["_informe_expense_export_amounts"](expense, report, None)
        self.assertEqual(
            values, {"importe_sin_iva": 5050.0, "iva": 800.0, "total": 5850.0}
        )

    def test_accounting_uses_partial_taxes_without_mutating_fiscal_evidence(self):
        report = SimpleNamespace(
            total=1120,
            subtotal=1000,
            descuento=0,
            total_impuestos_trasladados=160,
            impuestos_detalle={
                "traslados": [{"impuesto": "002", "importe": 160}],
                "retenciones": [{"impuesto": "001", "importe": 40}],
            },
        )
        expense = SimpleNamespace(
            gasto_cantidad=590,
            propina_no_deducible=30,
            cfdi_compartido_confirmado=True,
            iva=80,
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

    def test_shared_accounting_keeps_fallback_until_fiscal_source_is_linked(self):
        expense = SimpleNamespace(iva=16, cfdi_compartido_confirmado=True)
        taxes = self.scope["summarize_expense_cfdi_tax_components"](expense, None)
        self.assertEqual(taxes["iva_trasladado"], 16.0)

    def test_reservation_excludes_current_expense_and_tip(self):
        expense = SimpleNamespace(id=uuid4(), cfdi_report_id=uuid4())
        report = SimpleNamespace(id=expense.cfdi_report_id, moneda="MXN")
        session = SimpleNamespace(get=AsyncMock(return_value=report))
        cuenta = SimpleNamespace(currency="MXN")
        asyncio.run(
            self.scope["_validate_quick_shared_cfdi_amount"](
                session, expense, cuenta, self.values(propina_no_deducible="50")
            )
        )
        self.scope["validate_shared_cfdi_payment_amount"].assert_awaited_once_with(
            session,
            cfdi_report=report,
            requested_amount=Decimal("5800.00"),
            exclude_expense_id=expense.id,
        )

    def test_reservation_requires_linked_cfdi_and_matching_currency(self):
        for report_id, report in (
            (None, None),
            (uuid4(), None),
            (uuid4(), SimpleNamespace(moneda="USD")),
        ):
            with self.subTest(report_id=report_id, report=report), self.assertRaises(
                ValueError
            ):
                asyncio.run(
                    self.scope["_validate_quick_shared_cfdi_amount"](
                        SimpleNamespace(get=AsyncMock(return_value=report)),
                        SimpleNamespace(id=uuid4(), cfdi_report_id=report_id),
                        SimpleNamespace(currency="MXN"),
                        self.values(),
                    )
                )


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))


def _quick_capture_inputs():
    tree = ast.parse(ROUTE_PATH.read_text())
    detail = next(
        n for n in tree.body if getattr(n, "name", "") == "cuenta_de_gastos_detail"
    )
    value = next(
        n.value
        for n in ast.walk(detail)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "quick_capture_html" for t in n.targets
        )
        and isinstance(n.value, ast.JoinedStr)
    )
    # Read the static HTML controls from the actual template; other interpolations
    # are unrelated data values and render helpers.
    html = "".join(n.value for n in value.values if isinstance(n, ast.Constant))
    parser = _Inputs()
    parser.feed(html)
    return parser.inputs


def test_applied_discount_and_supplement_sharing_are_editable_form_controls():
    inputs = _quick_capture_inputs()
    main = [i for i in inputs if i.get("name") == "descuento"]
    assert len(main) == 1
    assert main[0]["type"] == "number"
    assert main[0]["id"] == "quick-descuento"
    for prefix in ("asiento_preferencial", "exceso_equipaje"):
        fields = {i["name"]: i for i in inputs if i.get("name", "").startswith(prefix)}
        assert fields[prefix + "_cfdi_compartido_confirmado"]["type"] == "checkbox"
        assert (
            fields[prefix + "_cfdi_compartido_confirmado"]["form"]
            == "quick-expense-form"
        )
        assert prefix + "_cfdi_compartido_motivo" in fields
        assert fields[prefix + "_descuento"]["type"] == "number"


async def _capture_with_supplement(
    monkeypatch, prefix, primary_shared, supplement_shared
):
    from devnous.gastos.routes import user_routes

    primary_uuid = str(uuid4()).upper()
    supplement_uuid = str(uuid4()).upper()
    fiscal = {
        "fecha": datetime(2026, 9, 25),
        "cfdi_uuid": primary_uuid,
        "subtotal": "1000",
        "descuento": "100",
        "total": "1044",
        "total_impuestos_trasladados": "144",
        "impuestos_detalle": {
            "traslados": [{"impuesto": "002", "importe": "144"}],
            "retenciones": [],
        },
    }
    supplement_fiscal = {
        "fecha": datetime(2026, 9, 25),
        "cfdi_uuid": supplement_uuid,
        "subtotal": "100",
        "descuento": "10",
        "total": "104.40",
        "total_impuestos_trasladados": "14.40",
        "impuestos_detalle": {
            "traslados": [{"impuesto": "002", "importe": "14.40"}],
            "retenciones": [],
        },
    }
    owner = SimpleNamespace(nombre="Solicitante", departamento="Operaciones")
    cuenta = SimpleNamespace(
        id=uuid4(),
        empleado_id=uuid4(),
        empleado=owner,
        torneo=None,
        referencia_base="260001",
        fase=None,
        categorias=[],
        edicion=None,
        beneficiario_proveedor_cliente=None,
    )
    informe = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: cuenta),
                SimpleNamespace(scalar_one_or_none=lambda: informe),
            ]
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        get=AsyncMock(return_value=SimpleNamespace(moneda="MXN")),
    )
    created = []
    links = []

    async def create(**kwargs):
        expense = SimpleNamespace(id=uuid4(), cfdi_report_id=None, **kwargs)
        created.append(expense)
        return expense

    async def ingest(_session, **kwargs):
        entity = kwargs["entity"]
        entity.cfdi_report_id = uuid4()
        return SimpleNamespace(
            cfdi_uuid=(
                primary_uuid if kwargs["xml_bytes"] == b"primary" else supplement_uuid
            )
        )

    async def link(_session, expense, **kwargs):
        links.append((expense.id, kwargs))
        expense.cfdi_compartido_confirmado = kwargs["allow_shared"]

    def resolve(**kwargs):
        if kwargs["xml_bytes"] is None:
            return None, None
        return (
            SimpleNamespace(
                parsed=(
                    fiscal if kwargs["xml_bytes"] == b"primary" else supplement_fiscal
                )
            ),
            None,
        )

    monkeypatch.setattr(user_routes, "_can_quick_capture_expense", lambda *args: True)
    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", AsyncMock())
    monkeypatch.setattr(user_routes, "currency_for", lambda *args: "MXN")
    monkeypatch.setattr(user_routes, "is_company_amex_account", lambda *args: False)
    monkeypatch.setattr(user_routes, "resolve_cfdi_upload", resolve)
    monkeypatch.setattr(user_routes, "create_expense_from_data", create)
    monkeypatch.setattr(user_routes, "ingest_cfdi_from_upload", ingest)
    monkeypatch.setattr(user_routes, "link_expense_to_cfdi_if_manual_uuid_set", link)
    monkeypatch.setattr(user_routes, "create_adjunto_record", AsyncMock())
    reserve = AsyncMock()
    monkeypatch.setattr(user_routes, "validate_shared_cfdi_payment_amount", reserve)
    args = {
        name: None
        for name in inspect.signature(
            user_routes.crear_gasto_rapido_en_informe
        ).parameters
    }
    args.update(
        cuenta_id=cuenta.id,
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        concepto="Vuelo",
        fecha="2026-09-25",
        subtotal="500",
        descuento="50",
        impuestos_y_retenciones="72",
        propina_no_deducible="0",
        cfdi_compartido_confirmado="1" if primary_shared else None,
        cfdi_compartido_motivo="Vuelo dividido" if primary_shared else None,
        cfdi_xml=SimpleNamespace(
            filename="primary.xml", read=AsyncMock(return_value=b"primary")
        ),
    )
    args[prefix + "_cfdi_xml"] = SimpleNamespace(
        filename="supplement.xml", read=AsyncMock(return_value=b"supplement")
    )
    args[prefix + "_cfdi_compartido_confirmado"] = "1" if supplement_shared else None
    args[prefix + "_cfdi_compartido_motivo"] = (
        "Suplemento dividido" if supplement_shared else None
    )
    if supplement_shared:
        args[prefix + "_subtotal"] = "50"
        args[prefix + "_descuento"] = "5"
        args[prefix + "_impuestos_y_retenciones"] = "7.20"
    accepted = inspect.signature(user_routes.crear_gasto_rapido_en_informe).parameters
    response = await user_routes.crear_gasto_rapido_en_informe(
        **{k: v for k, v in args.items() if k in accepted}
    )
    return response, session, created, links, reserve, fiscal, supplement_fiscal


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["asiento_preferencial", "exceso_equipaje"])
async def test_shared_primary_keeps_ordinary_supplement_fiscal_and_unshared(
    monkeypatch, prefix
):
    response, session, created, links, reserve, fiscal, supplement = (
        await _capture_with_supplement(monkeypatch, prefix, True, False)
    )
    assert "success=gasto_rapido_creado" in response.headers["location"]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    assert [e.gasto_cantidad for e in created] == [522.0, 104.4]
    assert [e.cfdi_compartido_confirmado for e in created] == [True, False]
    assert links[1][1]["shared_reason"] is None
    assert reserve.await_count == 1
    assert reserve.await_args.kwargs["requested_amount"] == Decimal("522")
    assert fiscal["total"] == "1044"
    assert supplement["total"] == "104.40"


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["asiento_preferencial", "exceso_equipaje"])
async def test_supplement_can_be_shared_without_sharing_primary(monkeypatch, prefix):
    response, session, created, links, reserve, _, _ = await _capture_with_supplement(
        monkeypatch, prefix, False, True
    )
    assert "success=gasto_rapido_creado" in response.headers["location"]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    assert [e.gasto_cantidad for e in created] == [1044.0, 52.2]
    assert [e.cfdi_compartido_confirmado for e in created] == [False, True]
    assert links[1][1]["shared_reason"] == "Suplemento dividido"
    assert reserve.await_count == 1
    assert reserve.await_args.kwargs["requested_amount"] == Decimal("52.20")


if __name__ == "__main__":
    unittest.main()
