import ast
from pathlib import Path


USER_ROUTES = Path("src/devnous/gastos/routes/user_routes.py")


def _function_node(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def _raw_500_details(function: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "HTTPException":
            continue
        status_code = None
        detail = None
        for keyword in node.keywords:
            if keyword.arg == "status_code":
                status_code = keyword.value
            if keyword.arg == "detail":
                detail = keyword.value
        if not isinstance(status_code, ast.Constant) or status_code.value != 500:
            continue
        if (
            isinstance(detail, ast.Call)
            and isinstance(detail.func, ast.Name)
            and detail.func.id == "str"
        ):
            lines.append(node.lineno)
    return lines


def test_download_routes_do_not_return_raw_exception_details():
    module = ast.parse(USER_ROUTES.read_text())
    route_names = [
        "descargar_gasto_comprobante",
        "descargar_gasto_adjunto",
        "descargar_documento_adjunto",
        "descargar_reembolso_adjunto",
    ]

    for route_name in route_names:
        assert _raw_500_details(_function_node(module, route_name)) == []

