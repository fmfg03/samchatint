"""Anonymous PDF layouts exercise extraction through quick-expense validation."""

import ast
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from uuid import UUID

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from test_ish_cfdi_upload_paths import XML, autofill, digital_pdf, pdf_reader

ROUTE_PATH = (
    Path(__file__).resolve().parents[3] / "src/devnous/gastos/routes/user_routes.py"
)

HEADER = (
    "Folio fiscal: 12345678-1234-1234-1234-1234567890AB\n"
    "Fecha: 2026-10-15\nMoneda: MXN\n"
)
GAS = (
    HEADER + "Cantidad Unidad Descripcion Valor unitario Importe\n"
    "Descuento\n20.0000 LTR COMBUSTIBLE SINTETICO 24.2290 484.58 0.00\n"
    "Subtotal $484.58\nIVA 16% $75.22\nTotal $559.80\n"
)


@lru_cache(maxsize=1)
def _scope():
    # Execute the production pure functions without importing the web application
    # or parsing the unrelated 48,000-line route module into a second full AST.
    source = ROUTE_PATH.read_text(encoding="utf-8")
    tax_components = autofill.quick_expense_tax_components_from_parsed
    scope = dict(
        Decimal=Decimal,
        ROUND_HALF_UP=ROUND_HALF_UP,
        datetime=datetime,
        Optional=Optional,
        Dict=Dict,
        Any=Any,
        normalize_cfdi_uuid_to_canonical=lambda value: str(UUID(value)).upper(),
        is_cfdi_uuid_prefix_candidate=lambda value: False,
        quick_expense_tax_components_from_parsed=tax_components,
        compute_quick_expense_total=autofill.compute_quick_expense_total,
    )
    for name in ("_quick_expense_decimal", "_quick_expense_values"):
        match = re.search(rf"(?ms)^def {name}\(.*?(?=^(?:async )?def |\Z)", source)
        assert match is not None
        # Original line numbers keep trace/coverage receipts attributable.
        padding = "\n" * source[: match.start()].count("\n")
        exec(compile(padding + match.group(), str(ROUTE_PATH), "exec"), scope)
    return scope


@pytest.fixture(autouse=True)
def disable_external_parser(monkeypatch):
    monkeypatch.setenv("MINERU_ENABLED", "0")


def capture(parsed, **overrides):
    args = dict(
        concepto="Consumo sintetico",
        fecha="2026-10-15",
        numero_factura=None,
        subtotal=None,
        descuento=None,
        impuestos_y_retenciones=None,
        xml_data=parsed,
    )
    args.update(overrides)
    return _scope()["_quick_expense_values"](**args)


def coffee_pdf(*, total="203.00"):
    """Amounts precede their labels in the stream, but occupy the right rows."""
    stream = BytesIO()
    canvas = Canvas(stream)
    for i, line in enumerate(HEADER.splitlines()):
        canvas.drawString(60, 750 - 20 * i, line)
    canvas.drawString(460, 560, f"${total}")
    canvas.drawString(460, 600, "$175.00")
    canvas.drawString(300, 600, "SUBTOTAL")
    canvas.drawString(300, 560, "TOTAL")
    canvas.drawString(300, 580, "IVA 16% $28.00")
    canvas.save()
    return stream.getvalue()


def resolve_pdf(blob):
    parsed, error = autofill.parse_cfdi_for_autofill(pdf_bytes=blob)
    assert error is None
    return parsed


def test_coffee_stream_order_uses_printed_rows_not_amount_sorting():
    blob = coffee_pdf()
    plain = PdfReader(BytesIO(blob)).pages[0].extract_text()
    assert "$203.00\n$175.00\nSUBTOTAL\nTOTAL" in plain
    assert pdf_reader.extract_cfdi_xml_from_pdf(blob) is None
    parsed = resolve_pdf(blob)
    assert parsed["subtotal"] == 175
    assert parsed["total"] == 203
    assert parsed["total_impuestos_trasladados"] == 28
    assert {"subtotal", "total"} <= set(parsed["_pdf_explicit_fields"])
    assert capture(parsed)["total"] == Decimal("203.00")
    fields = autofill.autofill_quick_expense_from_parsed_cfdi(parsed)
    assert (fields.subtotal, fields.total) == ("175.00", "203.00")


def test_layout_discrepancy_is_not_repaired_by_arithmetic():
    parsed = resolve_pdf(coffee_pdf(total="204.00"))
    assert parsed["total"] == 204
    with pytest.raises(ValueError, match=r"TOTAL del PDF.*TOTAL PDF: \$204.00"):
        capture(parsed)


def test_gas_quantity_is_not_a_discount():
    blob = digital_pdf(GAS)
    assert pdf_reader.extract_cfdi_xml_from_pdf(blob) is None
    parsed = resolve_pdf(blob)
    assert parsed["descuento"] is None  # No printed summary discount was read.
    assert parsed["subtotal"] == 484.58
    assert parsed["total"] == 559.80
    assert parsed["total_impuestos_trasladados"] == 75.22
    assert capture(parsed)["total"] == Decimal("559.80")
    assert autofill.autofill_quick_expense_from_parsed_cfdi(parsed).descuento == ""


def test_existing_compact_summary_preserves_discount():
    parsed = pdf_reader._parse_from_text(
        HEADER + "Subtotal 100.00 Descuento 10.00 IVA 16% 14.40 Total 104.40"
    )
    assert parsed["descuento"] == 10
    assert capture(parsed)["total"] == Decimal("104.40")


def test_crlf_summary_preserves_discount():
    parsed = pdf_reader._parse_from_text(
        HEADER + "Subtotal 100.00\r\nDescuento 10.00\r\nIVA 16% 14.40\r\nTotal 104.40"
    )
    assert parsed["descuento"] == 10
    assert capture(parsed)["total"] == Decimal("104.40")


@pytest.mark.parametrize(
    "suffix",
    ["20.0000 LTR", "20.00 LTR", "20.00\nLTR", "20.00 PRODUCTO 24.22", "20.00%"],
)
def test_discount_table_and_rate_false_positives(suffix):
    parsed = pdf_reader._parse_from_text(HEADER + f"Descuento\n{suffix}\n")
    assert parsed["descuento"] is None


@pytest.mark.parametrize(
    "discount", ["Descuento $0.00", "Descuento\n0.00", "Descuento: MXN 20.00"]
)
def test_explicit_discount_zero_and_nonzero_are_preserved(discount):
    value = Decimal("20") if "20.00" in discount else Decimal("0")
    parsed = resolve_pdf(
        digital_pdf(
            HEADER
            + f"Subtotal 100.00\n{discount}\nIVA 16% 16.00\nTotal {116 - value:.2f}"
        )
    )
    assert Decimal(str(parsed["descuento"])) == value
    assert capture(parsed)["total"] == Decimal("116") - value
    assert (
        autofill.autofill_quick_expense_from_parsed_cfdi(parsed).descuento
        == f"{value:.2f}"
    )


@pytest.mark.parametrize("field", ["subtotal", "total"])
def test_missing_required_amount_is_not_reconstructed_or_zero(field):
    text = HEADER + "Subtotal 175.00\nIVA 16% 28.00\nTotal 203.00"
    text = "\n".join(
        line for line in text.splitlines() if not line.lower().startswith(field + " ")
    )
    parsed = resolve_pdf(digital_pdf(text))
    assert parsed[field] is None
    assert field not in parsed["_pdf_explicit_fields"]
    assert (
        getattr(autofill.autofill_quick_expense_from_parsed_cfdi(parsed), field) == ""
    )
    with pytest.raises(ValueError, match="No se pudo leer .* del PDF"):
        capture(
            parsed, factura_compartida=True, subtotal="10", impuestos_y_retenciones="0"
        )


def test_plain_unordered_amounts_are_not_assigned_by_balancing():
    parsed = pdf_reader._parse_from_text(
        HEADER + "$203.00\n$175.00\nSUBTOTAL\nTOTAL\nIVA 16% 28.00"
    )
    assert parsed["subtotal"] is None
    assert parsed["total"] is None
    with pytest.raises(ValueError, match="No se pudo leer"):
        capture(parsed)


@pytest.mark.parametrize("field", ["Subtotal", "Total", "Descuento", "IVA 16%"])
def test_conflicting_printed_amounts_block_capture(field):
    parsed = pdf_reader._parse_from_text(
        HEADER
        + f"Subtotal 100.00\nTotal 116.00\nDescuento 0.00\nIVA 16% 16.00\n{field} 99.00"
    )
    assert parsed["_pdf_ambiguous_fields"]
    with pytest.raises(ValueError, match="PDF contiene importes ambiguos"):
        capture(parsed)


def test_equal_repeated_totals_are_not_ambiguous():
    parsed = pdf_reader._parse_from_text(
        HEADER + "Subtotal 100.00\nTotal 100.00\nTotal 100.00"
    )
    assert parsed["_pdf_ambiguous_fields"] == []
    assert capture(parsed)["total"] == Decimal("100")


def test_conflicting_subtotal_synonyms_are_not_resolved_by_balancing():
    parsed = pdf_reader._parse_from_text(
        HEADER + "Subtotal 100.00\nImporte sin IVA 200.00\nTotal 100.00"
    )
    assert parsed["subtotal"] is None
    assert "subtotal" in parsed["_pdf_ambiguous_fields"]
    with pytest.raises(ValueError, match="importes ambiguos"):
        capture(parsed)


@pytest.mark.parametrize("tax", ["", "IVA 0.00\n", "IVA 16.00%\n"])
def test_missing_or_zero_tax_does_not_balance_a_real_discrepancy(tax):
    parsed = pdf_reader._parse_from_text(HEADER + f"Subtotal 100.00\n{tax}Total 116.00")
    assert parsed["total_impuestos_trasladados"] in (None, 0)
    with pytest.raises(ValueError, match="TOTAL del PDF no coincide"):
        capture(parsed)


def test_printed_zero_total_is_never_recalculated():
    parsed = pdf_reader._parse_from_text(
        HEADER + "Subtotal 100.00\nIVA 16% 16.00\nTotal 0.00"
    )
    assert parsed["total"] == 0
    assert autofill.autofill_quick_expense_from_parsed_cfdi(parsed).total == "0.00"
    with pytest.raises(ValueError, match=r"TOTAL PDF: \$0.00"):
        capture(parsed)


@pytest.mark.parametrize(
    "labels",
    [
        ("Sub total", "Total a pagar"),
        ("Importe sin IVA", "Importe total"),
        ("Base", "Monto total"),
    ],
)
def test_existing_labels_thousands_and_retention(labels):
    parsed = pdf_reader._parse_from_text(
        HEADER
        + f"{labels[0]}: $1,000.00\nDescuento 100.00\n"
        + f"IVA 16% 144.00\nISR retenido 40.00\n{labels[1]} $1,004.00"
    )
    assert parsed["subtotal"] == 1000
    assert capture(parsed)["total"] == Decimal("1004")


@pytest.mark.parametrize("source", ["XML", "PDF"])
def test_xml_precedence_and_embedded_xml_keep_validation(source):
    xml = XML.replace(b'Total="5280.00"', b'Total="5281.00"')
    if source == "XML":
        parsed, error = autofill.parse_cfdi_for_autofill(
            xml_bytes=xml, pdf_bytes=coffee_pdf()
        )
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_attachment("synthetic.xml", xml)
        stream = BytesIO()
        writer.write(stream)
        parsed, error = autofill.parse_cfdi_for_autofill(pdf_bytes=stream.getvalue())
    assert error is None
    assert "_pdf_explicit_fields" not in parsed
    with pytest.raises(ValueError, match=f"TOTAL del {source} no coincide"):
        capture(parsed, cfdi_source=source)


def test_submit_and_supplement_pass_the_upload_source_to_validation():
    source = ROUTE_PATH.read_text(encoding="utf-8")
    route = source.split("async def crear_gasto_rapido_en_informe(", 1)[1]
    route = "async def crear_gasto_rapido_en_informe(" + route.split("\n@router", 1)[0]
    tree = ast.parse(route)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_quick_expense_values"
    ]
    assert len(calls) == 2
    for call in calls:
        source = next(kw.value for kw in call.keywords if kw.arg == "cfdi_source")
        assert "from_uploaded_xml" in ast.unparse(source)


def test_layout_unavailable_leaves_missing_amounts_visible(monkeypatch):
    original = pdf_reader._extract_pdf_text
    monkeypatch.setattr(
        pdf_reader, "_extract_pdf_text", lambda blob, **kw: "" if kw else original(blob)
    )
    parsed = pdf_reader.parse_cfdi_pdf(coffee_pdf())
    assert parsed["total"] is None
    with pytest.raises(ValueError, match="No se pudo leer"):
        capture(parsed)


@pytest.mark.parametrize("complete", [False, True])
def test_external_text_requires_amounts_before_skipping_pdf_layout(
    monkeypatch, complete
):
    text = HEADER + (
        "Subtotal 175.00\nIVA 28.00\nTotal 203.00" if complete else "IVA 28.00"
    )
    monkeypatch.setattr(
        pdf_reader,
        "parse_document_bytes",
        lambda *args, **kwargs: SimpleNamespace(has_text=True, text=text),
    )
    parsed = pdf_reader.parse_cfdi_pdf(coffee_pdf())
    assert capture(parsed)["total"] == Decimal("203.00")


@pytest.mark.parametrize("amount", ["20.0000", "1,23.00", "20.00%", "20.00 LTR"])
def test_invalid_amount_tokens_are_not_partially_read(amount):
    parsed = pdf_reader._parse_from_text(HEADER + f"Subtotal {amount}\nTotal {amount}")
    assert parsed["subtotal"] is None
    assert parsed["total"] is None


def test_explicit_zero_subtotal_remains_zero_in_autofill():
    parsed = pdf_reader._parse_from_text(HEADER + "Subtotal 0.00\nIVA 0.00\nTotal 0.00")
    fields = autofill.autofill_quick_expense_from_parsed_cfdi(parsed)
    assert fields.subtotal == "0.00"
    assert fields.total == "0.00"
    with pytest.raises(ValueError, match="TOTAL debe ser mayor a cero"):
        capture(parsed)
