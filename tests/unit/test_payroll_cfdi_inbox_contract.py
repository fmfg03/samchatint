from pathlib import Path


USER_ROUTES = Path("src/devnous/gastos/routes/user_routes.py")


def test_payroll_cfdi_view_lists_imported_payroll_cfdis_without_moving_them():
    source = USER_ROUTES.read_text()
    block = source.split("async def nomina_cfdi_view", maxsplit=1)[1].split(
        '@router.post("/admin/nomina/cfdi/mapping/save")', maxsplit=1
    )[0]

    assert 'func.upper(CFDIReport.tipo_de_comprobante) == "N"' in block
    assert "CFDI de nómina importados" in block
    assert "quedan fuera de Cuentas por Cobrar" in block
