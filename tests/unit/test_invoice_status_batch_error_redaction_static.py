import ast
from pathlib import Path


INVOICE_STATUS_UPDATER = Path("src/devnous/gastos/workers/invoice_status_updater.py")


def _function_node(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def test_update_invoice_statuses_does_not_append_raw_exception_messages():
    module = ast.parse(INVOICE_STATUS_UPDATER.read_text())
    function = _function_node(module, "update_invoice_statuses")

    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id != "error_msg":
                continue
            assert not (
                isinstance(node.value, ast.JoinedStr)
                and any(
                    isinstance(value, ast.FormattedValue)
                    and isinstance(value.value, ast.Call)
                    and isinstance(value.value.func, ast.Name)
                    and value.value.func.id == "str"
                    for value in node.value.values
                )
            )
