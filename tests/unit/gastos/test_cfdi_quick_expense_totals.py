'''RQF-056K tests for CFDI totals in quick expense capture.'''

from decimal import Decimal

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services.cfdi_autofill import (
    autofill_quick_expense_from_parsed_cfdi,
    quick_expense_tax_components_from_parsed,
)
from devnous.gastos.services.cfdi_parser import parse_cfdi_xml


def _cfdi_xml(*, subtotal='110.34', traslados='17.66', retenciones='0.00', total='128.00') -> str:
    retenciones_xml = ''
    total_retenciones_attr = ''
    if Decimal(str(retenciones)):
        total_retenciones_attr = f' TotalImpuestosRetenidos="{retenciones}"'
        retenciones_xml = f'''
            <cfdi:Retenciones>
                <cfdi:Retencion Base="100.00" Impuesto="001" TipoFactor="Tasa" TasaOCuota="0.040000" Importe="{retenciones}" />
            </cfdi:Retenciones>
        '''
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
        xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
        Version="4.0" Serie="A" Folio="8CD" Fecha="2026-07-16T10:30:00"
        Moneda="MXN" SubTotal="{subtotal}" Total="{total}" TipoDeComprobante="I">
        <cfdi:Emisor Rfc="AAA010101AAA" Nombre="EMISOR PRUEBA" RegimenFiscal="601" />
        <cfdi:Receptor Rfc="BBB010101BBB" Nombre="RECEPTOR PRUEBA" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601" UsoCFDI="G03" />
        <cfdi:Conceptos>
            <cfdi:Concepto ClaveProdServ="01010101" Cantidad="1" ClaveUnidad="H87" Descripcion="MARC SHARPIE METAL ORO BRO P" ValorUnitario="{subtotal}" Importe="{subtotal}">
                <cfdi:Impuestos>
                    <cfdi:Traslados>
                        <cfdi:Traslado Base="{subtotal}" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="{traslados}" />
                    </cfdi:Traslados>
                    {retenciones_xml}
                </cfdi:Impuestos>
            </cfdi:Concepto>
        </cfdi:Conceptos>
        <cfdi:Impuestos TotalImpuestosTrasladados="{traslados}"{total_retenciones_attr}>
            {retenciones_xml}
            <cfdi:Traslados>
                <cfdi:Traslado Base="{subtotal}" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="{traslados}" />
            </cfdi:Traslados>
        </cfdi:Impuestos>
        <cfdi:Complemento>
            <tfd:TimbreFiscalDigital UUID="12345678-1234-1234-1234-1234567890ab" FechaTimbrado="2026-07-16T10:31:00" />
        </cfdi:Complemento>
    </cfdi:Comprobante>
    '''


def test_quick_expense_autofill_uses_xml_total_as_authority_for_128_case() -> None:
    parsed = parse_cfdi_xml(_cfdi_xml())

    taxes = quick_expense_tax_components_from_parsed(parsed)
    autofill = autofill_quick_expense_from_parsed_cfdi(parsed)
    values = user_routes._quick_expense_values(
        concepto="Descripcion capturada por usuario",
        fecha=None,
        numero_factura=None,
        subtotal=None,
        descuento=None,
        impuestos_y_retenciones=None,
        xml_data=parsed,
    )

    assert taxes.subtotal == Decimal('110.34')
    assert taxes.impuestos_trasladados == Decimal('17.66')
    assert taxes.impuestos_y_retenciones == Decimal('17.66')
    assert taxes.total == Decimal('128.00')
    assert autofill is not None
    assert autofill.subtotal == '110.34'
    assert autofill.impuestos_y_retenciones == '17.66'
    assert autofill.total == '128.00'
    assert values['subtotal'] == Decimal('110.34')
    assert values['impuestos_y_retenciones'] == Decimal('17.66')
    assert values['total'] == Decimal('128.00')



def test_quick_expense_xml_preserves_user_description_and_adds_tip_to_paid_total() -> None:
    parsed = parse_cfdi_xml(_cfdi_xml(subtotal='180.00', traslados='28.80', total='208.80'))

    values = user_routes._quick_expense_values(
        concepto='Consumo de alimentos con cliente',
        fecha=None,
        numero_factura=None,
        subtotal=None,
        descuento=None,
        impuestos_y_retenciones=None,
        propina_no_deducible='40.00',
        xml_data=parsed,
    )

    assert values['concepto'] == 'Consumo de alimentos con cliente'
    assert values['subtotal'] == Decimal('180.00')
    assert values['impuestos_y_retenciones'] == Decimal('28.80')
    assert values['propina_no_deducible'] == Decimal('40.00')
    assert values['total'] == Decimal('248.80')


def test_quick_expense_xml_requires_user_description_even_when_cfdi_has_description() -> None:
    parsed = parse_cfdi_xml(_cfdi_xml())

    with pytest.raises(ValueError) as exc:
        user_routes._quick_expense_values(
            concepto='',
            fecha=None,
            numero_factura=None,
            subtotal=None,
            descuento=None,
            impuestos_y_retenciones=None,
            xml_data=parsed,
        )

    assert 'gasto es requerida' in str(exc.value)

def test_quick_expense_xml_validation_rejects_inconsistent_total() -> None:
    parsed = parse_cfdi_xml(_cfdi_xml(total='124.00'))

    with pytest.raises(ValueError) as exc:
        user_routes._quick_expense_values(
            concepto="Descripcion capturada por usuario",
            fecha=None,
            numero_factura=None,
            subtotal=None,
            descuento=None,
            impuestos_y_retenciones=None,
            xml_data=parsed,
        )

    assert 'TOTAL del XML no coincide' in str(exc.value)


def test_quick_expense_taxes_are_net_of_retenciones() -> None:
    parsed = parse_cfdi_xml(
        _cfdi_xml(subtotal='100.00', traslados='16.00', retenciones='4.00', total='112.00')
    )

    taxes = quick_expense_tax_components_from_parsed(parsed)
    autofill = autofill_quick_expense_from_parsed_cfdi(parsed)

    assert taxes.impuestos_trasladados == Decimal('16.00')
    assert taxes.retenciones == Decimal('4.00')
    assert taxes.impuestos_y_retenciones == Decimal('12.00')
    assert taxes.calculated_total == Decimal('112.00')
    assert taxes.total == Decimal('112.00')
    assert autofill is not None
    assert autofill.impuestos_y_retenciones == '12.00'
    assert autofill.total == '112.00'



def _cfdi_xml_with_local_lodging_tax() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?>
    <cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
        xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
        xmlns:implocal="http://www.sat.gob.mx/implocal"
        Version="4.0" Serie="H" Folio="100" Fecha="2026-08-08T10:30:00"
        Moneda="MXN" SubTotal="1000.00" Total="1196.00" TipoDeComprobante="I">
        <cfdi:Emisor Rfc="HOT010101AAA" Nombre="HOTEL PRUEBA" RegimenFiscal="601" />
        <cfdi:Receptor Rfc="PSP1705058S4" Nombre="PLATAFORMA SPORTS" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601" UsoCFDI="G03" />
        <cfdi:Conceptos>
            <cfdi:Concepto ClaveProdServ="90111501" Cantidad="1" ClaveUnidad="E48" Descripcion="HOSPEDAJE" ValorUnitario="1000.00" Importe="1000.00">
                <cfdi:Impuestos>
                    <cfdi:Traslados>
                        <cfdi:Traslado Base="1000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="160.00" />
                    </cfdi:Traslados>
                </cfdi:Impuestos>
            </cfdi:Concepto>
        </cfdi:Conceptos>
        <cfdi:Impuestos TotalImpuestosTrasladados="160.00">
            <cfdi:Traslados>
                <cfdi:Traslado Base="1000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="160.00" />
            </cfdi:Traslados>
        </cfdi:Impuestos>
        <cfdi:Complemento>
            <implocal:ImpuestosLocales version="1.0" TotaldeRetenciones="0.00" TotaldeTraslados="36.00">
                <implocal:TrasladosLocales ImpLocTrasladado="ISH" TasadeTraslado="3.60" Importe="36.00" />
            </implocal:ImpuestosLocales>
            <tfd:TimbreFiscalDigital UUID="22345678-1234-1234-1234-1234567890ab" FechaTimbrado="2026-08-08T10:31:00" />
        </cfdi:Complemento>
    </cfdi:Comprobante>
    '''


def test_quick_expense_xml_with_local_lodging_tax_accepts_total() -> None:
    parsed = parse_cfdi_xml(_cfdi_xml_with_local_lodging_tax())

    taxes = quick_expense_tax_components_from_parsed(parsed)
    autofill = autofill_quick_expense_from_parsed_cfdi(parsed)
    values = user_routes._quick_expense_values(
        concepto="Descripcion capturada por usuario",
        fecha=None,
        numero_factura=None,
        subtotal=None,
        descuento=None,
        impuestos_y_retenciones=None,
        xml_data=parsed,
    )

    assert taxes.subtotal == Decimal('1000')
    assert taxes.impuestos_trasladados == Decimal('160')
    assert taxes.impuestos_locales_trasladados == Decimal('36')
    assert taxes.impuestos_y_retenciones == Decimal('196.00')
    assert taxes.calculated_total == Decimal('1196.00')
    assert taxes.total == Decimal('1196')
    assert autofill is not None
    assert autofill.impuestos_y_retenciones == '196.00'
    assert autofill.total == '1196.00'
    assert values['impuestos_y_retenciones'] == Decimal('196.00')
    assert values['iva'] == Decimal('160.00')
    assert values['total'] == Decimal('1196.00')
