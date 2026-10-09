from pathlib import Path


ROUTES = Path("src/devnous/gastos/routes/user_routes.py")


def test_monthly_diot_routes_and_navigation_are_registered():
    source = ROUTES.read_text(encoding="utf-8")

    assert '("/admin/contabilidad/diot", "DIOT", "diot")' in source
    assert (
        '@router.get("/admin/contabilidad/diot", response_class=HTMLResponse)'
        in source
    )
    assert (
        '@router.get("/admin/contabilidad/diot/export.txt", '
        'response_model=None)' in source
    )
    assert (
        '@router.get("/admin/contabilidad/diot/export.xlsx", '
        'response_model=None)' in source
    )
    guard = "current_empleado: Empleado = require_admin_finanzas()"
    assert source.count(guard) >= 3
    assert "TXT bloqueado" in source
    assert "Sin fecha efectiva" in source
