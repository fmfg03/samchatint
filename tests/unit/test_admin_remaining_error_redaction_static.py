import ast
from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _function_source(module_source: str, name: str) -> str:
    module = ast.parse(module_source)
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(module_source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_remaining_admin_500_errors_do_not_render_raw_exception_details():
    module_source = ADMIN_ROUTES.read_text()
    raw_error_targets = {
        "admin_proveedores_clientes": [
            "<strong>Error:</strong> {str(e)}",
        ],
        "cleanup_contable_gasto": [
            'PlainTextResponse(f"Error: {str(exc)}", status_code=500)',
        ],
        "asignar_cuenta_contable": [
            'PlainTextResponse(f"Error: {str(e)}", status_code=500)',
        ],
    }

    for function_name, forbidden_snippets in raw_error_targets.items():
        function_source = _function_source(module_source, function_name)
        for forbidden in forbidden_snippets:
            assert forbidden not in function_source
