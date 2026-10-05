"""ISH capture classification using production functions and synthetic evidence."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from functools import lru_cache
import sys
import sqlite3

import pytest

from test_quick_shared_invoice_amounts import _load_functions

_scope = lru_cache(maxsize=1)(_load_functions)


def fiscal(ish="36.00", label="ISH"):
    return {
        "subtotal": "1000.00",
        "descuento": "100.00",
        "total": str(Decimal("1004.00") + Decimal(ish)),
        "cfdi_uuid": "12345678-1234-1234-1234-1234567890AB",
        "fecha": datetime(2026, 10, 1),
        "total_impuestos_trasladados": "144.00",
        "impuestos_detalle": {
            "traslados": [{"impuesto": "002", "importe": "144.00"}],
            "retenciones": [{"impuesto": "001", "importe": "40.00"}],
            "locales": [{"tipo": "traslado", "impuesto": label, "importe": ish}],
        },
    }


@pytest.mark.parametrize("ish", ["0.00", "36.00", "36.03"])
@pytest.mark.parametrize("label", ["ISH", " I.S.H. ", "Impuesto sobre Hospedaje"])
def test_capture_and_export_preserve_fiscal_fields_and_total(ish, label):
    scope = _scope()
    parsed = fiscal(ish, label)
    original = deepcopy(parsed)
    taxes = scope["quick_expense_tax_components_from_parsed"](parsed)
    autofill = sys.modules[
        "_shared_invoice_autofill"
    ].autofill_quick_expense_from_parsed_cfdi(parsed)
    assert Decimal(autofill.subtotal) == Decimal("1000") + Decimal(ish)
    assert autofill.impuestos_y_retenciones == "104.00"
    values = scope["_quick_expense_values"](
        concepto="Hospedaje sintético",
        fecha="2026-10-01",
        numero_factura=None,
        subtotal=None,
        descuento=None,
        impuestos_y_retenciones=None,
        xml_data=parsed,
    )
    assert taxes.subtotal == Decimal("1000")
    assert taxes.retenciones == Decimal("40")
    assert values["subtotal"] == Decimal("1000") + Decimal(ish)
    assert values["impuestos_y_retenciones"] == Decimal("104")
    assert values["iva"] == Decimal("144")
    assert taxes.calculated_total == values["total"] == Decimal(parsed["total"])
    assert parsed == original
    if Decimal(ish):
        row = scope["_informe_expense_export_amounts"](
            SimpleNamespace(gasto_cantidad=values["total"], iva=144),
            SimpleNamespace(**parsed),
            None,
        )
        assert Decimal(str(row["importe_sin_iva"])) == Decimal("900") + Decimal(ish)
        assert row["iva"] == 104
        assert row["importe_sin_iva"] + row["iva"] == row["total"]


@pytest.mark.parametrize("shared", [False, True])
def test_reference_amounts_flow_to_capture_budget_and_export(shared):
    from samchat.budgets.service import (
        _budget_expense_base_amount_sql,
        budget_document_effect_snapshot,
        budget_expense_effect_amount,
    )

    scope = _scope()
    parsed = fiscal("176.00")
    parsed.update(
        subtotal="4400.00",
        descuento="0",
        total="5280.00",
        total_impuestos_trasladados="704.00",
    )
    parsed["impuestos_detalle"]["traslados"][0]["importe"] = "704.00"
    parsed["impuestos_detalle"]["retenciones"] = []
    original = deepcopy(parsed)
    autofill = sys.modules[
        "_shared_invoice_autofill"
    ].autofill_quick_expense_from_parsed_cfdi(parsed)
    assert autofill.to_dict()["subtotal"] == "4576.00"
    assert autofill.to_dict()["impuestos_y_retenciones"] == "704.00"
    assert autofill.to_dict()["total"] == "5280.00"
    report = SimpleNamespace(**parsed)
    expenses, rows = [], []
    for _ in range(2 if shared else 1):
        values = scope["_quick_expense_values"](
            concepto="Hospedaje sintético",
            fecha="2026-10-01",
            numero_factura=None,
            subtotal="2288.00" if shared else None,
            descuento="0",
            impuestos_y_retenciones="352.00" if shared else None,
            xml_data=parsed,
            factura_compartida=shared,
        )
        expected = Decimal("2288.00" if shared else "4576.00")
        expense = SimpleNamespace(
            gasto_cantidad=values["total"],
            iva=values["iva"],
            cfdi_compartido_confirmado=shared,
            estado_gasto="pendiente",
            budget_concept_id="synthetic",
            cfdi_report=report,
        )
        assert budget_expense_effect_amount(expense, report) == expected
        with sqlite3.connect(":memory:") as db:
            db.create_function("GREATEST", -1, max)
            db.create_function("LEAST", -1, min)
            db.execute("CREATE TABLE adjuntos (gasto_id, categoria, activo)")
            amount = db.execute(
                f"SELECT {_budget_expense_base_amount_sql()} "
                "FROM (SELECT 1 id, ? gasto_cantidad, ? iva, "
                "0 propina_no_deducible, NULL hospedaje_impuesto_monto) e "
                "CROSS JOIN (SELECT 4400 subtotal, 0 descuento, 5280 total) cfdi",
                (float(values["total"]), float(values["iva"])),
            ).fetchone()[0]
        assert Decimal(str(amount)) == expected
        row = scope["_informe_expense_export_amounts"](expense, report, None)
        assert Decimal(str(row["importe_sin_iva"])) == expected
        assert row["iva"] == float(values["impuestos_y_retenciones"])
        assert row["importe_sin_iva"] + row["iva"] == row["total"]
        expenses.append(expense)
        rows.append(row)
    document = SimpleNamespace(tipo="INFORME", estado="aprobado", gastos=expenses)
    assert budget_document_effect_snapshot(document)["amount"] == Decimal("4576")
    assert sum(Decimal(str(row["total"])) for row in rows) == Decimal("5280")
    assert parsed == original


def test_other_local_taxes_and_local_withholdings_keep_their_classification():
    scope = _scope()
    parsed = fiscal(label="Otro impuesto local")
    parsed["impuestos_detalle"]["locales"].append(
        {"tipo": "retencion", "impuesto": "ISH", "importe": "5"}
    )
    taxes = scope["quick_expense_tax_components_from_parsed"](parsed)
    assert taxes.ish == 0
    assert taxes.subtotal_captura == Decimal("1000")
    assert taxes.impuestos_y_retenciones == Decimal("135")


def test_shared_invoice_allocates_ish_once_and_rounds_each_export_row():
    scope = _scope()
    parsed = fiscal("36.03")
    original = deepcopy(parsed)
    amounts = [("518.02", "50.00", "52.00"), ("518.01", "50.00", "52.00")]
    rows = []
    for subtotal, discount, tax in amounts:
        values = scope["_quick_expense_values"](
            concepto="Hospedaje",
            fecha="2026-10-01",
            numero_factura=None,
            subtotal=subtotal,
            descuento=discount,
            impuestos_y_retenciones=tax,
            xml_data=parsed,
            factura_compartida=True,
        )
        rows.append(
            scope["_informe_expense_export_amounts"](
                SimpleNamespace(
                    gasto_cantidad=values["total"],
                    iva=values["iva"],
                    cfdi_compartido_confirmado=True,
                ),
                SimpleNamespace(**parsed),
                None,
            )
        )
    assert sum(Decimal(str(r["total"])) for r in rows) == Decimal(parsed["total"])
    assert sum(Decimal(str(r["importe_sin_iva"])) for r in rows) == Decimal("936.03")
    assert sum(Decimal(str(r["iva"])) for r in rows) == Decimal("104.00")
    for row in rows:
        assert Decimal(str(row["importe_sin_iva"])) + Decimal(
            str(row["iva"])
        ) == Decimal(str(row["total"]))
    assert parsed == original
