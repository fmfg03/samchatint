"""Synthetic XML and PDF bytes exercise the canonical upload resolver."""

from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen.canvas import Canvas

# Load actual service modules without importing the gastos application facade.
package = ModuleType("_ish_upload_services")
package.__path__ = [
    str(Path(__file__).resolve().parents[3] / "src/devnous/gastos/services")
]
sys.modules[package.__name__] = package
autofill = importlib.import_module(package.__name__ + ".cfdi_autofill")
pdf_reader = importlib.import_module(package.__name__ + ".cfdi_pdf_reader")
autofill_quick_expense_from_parsed_cfdi = (
    autofill.autofill_quick_expense_from_parsed_cfdi
)
parse_cfdi_for_autofill = autofill.parse_cfdi_for_autofill
quick_expense_tax_components_from_parsed = (
    autofill.quick_expense_tax_components_from_parsed
)
_parse_from_text = pdf_reader._parse_from_text

XML = b"""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
 xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
 xmlns:implocal="http://www.sat.gob.mx/implocal"
 Version="4.0" Fecha="2026-10-01T12:00:00" Moneda="MXN"
 SubTotal="4400.00" Total="5280.00" TipoDeComprobante="I">
 <cfdi:Emisor Rfc="AAA010101AAA" Nombre="SYNTHETIC"/>
 <cfdi:Receptor Rfc="BBB010101BBB" Nombre="SYNTHETIC"/>
 <cfdi:Impuestos TotalImpuestosTrasladados="704.00">
  <cfdi:Traslados><cfdi:Traslado Impuesto="002" Importe="704.00"/></cfdi:Traslados>
 </cfdi:Impuestos>
 <cfdi:Complemento>
  <implocal:ImpuestosLocales TotaldeTraslados="176.00" TotaldeRetenciones="0">
   <implocal:TrasladosLocales ImpLocTrasladado="ISH" Importe="176.00"/>
  </implocal:ImpuestosLocales>
  <tfd:TimbreFiscalDigital UUID="12345678-1234-1234-1234-1234567890AB"/>
 </cfdi:Complemento>
</cfdi:Comprobante>"""

TEXT = """Folio fiscal: 12345678-1234-1234-1234-1234567890AB
Fecha: 2026-10-01
Moneda: MXN
Subtotal 4400.00
IVA 16% 704.00
ISH 4% 176.00
Total 5280.00
"""


def digital_pdf(text):
    stream = BytesIO()
    canvas = Canvas(stream)
    for line, value in enumerate(text.splitlines()):
        canvas.drawString(72, 750 - line * 20, value)
    canvas.save()
    return stream.getvalue()


@pytest.mark.parametrize("source", ["xml", "embedded_xml", "digital_pdf"])
def test_upload_paths_preserve_reference_fiscal_amounts(source, monkeypatch):
    monkeypatch.setenv("MINERU_ENABLED", "0")
    if source == "xml":
        arguments = {"xml_bytes": XML}
    elif source == "embedded_xml":
        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_attachment("synthetic.xml", XML)
        writer.write(stream)
        arguments = {"pdf_bytes": stream.getvalue()}
    else:
        arguments = {"pdf_bytes": digital_pdf(TEXT)}
    parsed, error = parse_cfdi_for_autofill(**arguments)
    assert error is None
    original = deepcopy(parsed)
    assert Decimal(str(parsed["subtotal"])) == Decimal("4400")
    assert Decimal(str(parsed["total"])) == Decimal("5280")
    taxes = quick_expense_tax_components_from_parsed(parsed)
    assert taxes.iva == Decimal("704")
    assert taxes.ish == Decimal("176")
    assert taxes.calculated_total == Decimal("5280")
    capture = autofill_quick_expense_from_parsed_cfdi(parsed).to_dict()
    assert capture["subtotal"] == "4576.00"
    assert capture["impuestos_y_retenciones"] == "704.00"
    assert capture["total"] == "5280.00"
    assert parsed == original


@pytest.mark.parametrize("label", ["ISH", "I.S.H.", "Impuesto sobre Hospedaje"])
def test_text_reads_ish_label_amount_without_recalculating_iva(label):
    parsed = _parse_from_text(TEXT.replace("ISH", label))
    assert parsed["subtotal"] == 4400
    assert parsed["total_impuestos_trasladados"] == 704
    assert parsed["impuestos_detalle"]["locales"][0]["importe"] == 176


def test_text_other_local_tax_is_not_inferred_as_ish():
    parsed = _parse_from_text(TEXT.replace("ISH", "Otro impuesto local"))
    assert parsed["impuestos_detalle"]["locales"] == []
    assert quick_expense_tax_components_from_parsed(parsed).ish == 0


def test_text_explicit_ish_is_not_inferred_into_federal_tax_when_iva_absent():
    parsed = _parse_from_text(TEXT.replace("IVA 16% 704.00\n", ""))
    assert parsed["total_impuestos_trasladados"] == 704
    assert parsed["impuestos_detalle"]["locales"][0]["importe"] == 176
