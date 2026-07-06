import ast
from pathlib import Path


INVOICE_STATUS_UPDATER = Path("src/devnous/gastos/workers/invoice_status_updater.py")


def _function_node(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def test_update_single_invoice_does_not_return_raw_exception_messages():
    module = ast.parse(INVOICE_STATUS_UPDATER.read_text())
    function = _function_node(module, "update_single_invoice")

    for node in ast.walk(function):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
        if "message" not in keys:
            continue
        for value in node.values:
            assert not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "str"
            )
