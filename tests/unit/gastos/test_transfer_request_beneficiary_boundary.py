from pathlib import Path


ROUTE = Path("src/devnous/gastos/routes/user_routes.py")


def _route_block(start: str, end: str) -> str:
    source = ROUTE.read_text()
    return source[source.index(start) : source.index(end, source.index(start))]


def test_transfer_request_form_uses_report_beneficiary_not_requester_account() -> None:
    block = _route_block(
        "async def nueva_solicitud_desde_cuenta_form(",
        '@router.post("/informes-de-gastos/{cuenta_id}/nueva-solicitud")',
    )

    assert "effective_account_provider_beneficiary_id(cuenta)" in block
    assert "effective_account_beneficiary_id(cuenta)" in block
    assert "empleado=beneficiary_empleado" in block
    assert "escape(beneficiary_name)" in block
    assert "empleado=current_empleado" not in block


def test_transfer_request_submit_authorizes_selected_account_against_report_beneficiary() -> None:
    block = _route_block(
        "async def nueva_solicitud_desde_cuenta_submit(",
        '@router.post("/informes-de-gastos/{cuenta_id}/cerrar")',
    )

    assert "effective_account_provider_beneficiary_id(cuenta)" in block
    assert "beneficiario=beneficiary_empleado" in block
    assert "proveedor_cliente_id_raw == str(provider_beneficiary_id)" in block
    assert "empleado=current_empleado" not in block
