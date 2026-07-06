import ast
from pathlib import Path


USER_ROUTES = Path("src/devnous/gastos/routes/user_routes.py")


def _function_node(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def _direct_upload_reads(node: ast.AST, upload_names: set[str]) -> list[str]:
    direct_reads: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Await):
            continue
        call = child.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "read":
            continue
        receiver = func.value
        if isinstance(receiver, ast.Name) and receiver.id in upload_names:
            direct_reads.append(receiver.id)
    return direct_reads


def test_user_upload_handlers_do_not_buffer_files_before_limit_check():
    module = ast.parse(USER_ROUTES.read_text())
    expectations = {
        "crear_gasto": {"archivo"},
        "carga_masiva_amex_post": {"archivo_csv"},
        "saldar_cuenta_submit": {"comprobante"},
    }

    for function_name, upload_names in expectations.items():
        function = _function_node(module, function_name)
        assert _direct_upload_reads(function, upload_names) == []

