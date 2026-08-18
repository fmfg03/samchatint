from types import SimpleNamespace

from devnous.gastos.routes.admin_routes import _cuentas_contables_export_csv


def test_cuentas_contables_export_csv_has_bulk_catalog_headers_and_rows():
    payload = _cuentas_contables_export_csv([
        SimpleNamespace(codigo="1110-000-000", nombre="FONDO FIJO DE CAJA", tipo="caja", activo=True),
        SimpleNamespace(codigo="5300-010-001", nombre="GASTOS DE VIAJE", tipo="gasto", activo=False),
    ])

    assert payload.startswith("\ufeffcodigo,nombre,tipo,activo")
    assert "1110-000-000,FONDO FIJO DE CAJA,caja,si" in payload
    assert "5300-010-001,GASTOS DE VIAJE,gasto,no" in payload
