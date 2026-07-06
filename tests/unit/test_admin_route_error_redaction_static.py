import ast
from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def _function_node(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def _raw_exception_detail_calls(function: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "detail":
                continue
            detail = keyword.value
            if (
                isinstance(detail, ast.Call)
                and isinstance(detail.func, ast.Name)
                and detail.func.id == "str"
            ):
                lines.append(node.lineno)
    return lines


def test_admin_error_pages_do_not_render_raw_exception_details():
    module = ast.parse(ADMIN_ROUTES.read_text())
    route_names = [
        "admin_expenses",
        "admin_invoices",
        "cfdi_matching_control_room",
    ]

    for route_name in route_names:
        assert _raw_exception_detail_calls(_function_node(module, route_name)) == []

