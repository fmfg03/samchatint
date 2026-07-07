import ast
from pathlib import Path


OPERATIONS_MODULE = Path("src/devnous/tournaments/core/operations_module.py")


def _function_node(
    module: ast.Module, name: str
) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(module):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def _contains_str_exception_call(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "str"
        for child in ast.walk(node)
    )


def test_ocr_registration_does_not_return_raw_exception_text():
    module = ast.parse(OPERATIONS_MODULE.read_text())
    function = _function_node(module, "process_ocr_registration")

    for node in ast.walk(function):
        if isinstance(node, ast.Return):
            assert not _contains_str_exception_call(node)


def test_claude_vision_payload_does_not_include_raw_exception_text():
    module = ast.parse(OPERATIONS_MODULE.read_text())
    function = _function_node(module, "_call_claude_vision")

    for node in ast.walk(function):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
        if "error" in keys:
            assert not _contains_str_exception_call(node)
