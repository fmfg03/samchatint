import ast
from pathlib import Path


ASSISTANT_ROUTER = Path("src/samchat/assistant/router.py")


def _function_node(module: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function not found: {name}")


def test_create_media_message_uses_chunked_upload_limit_before_processing():
    module_source = ASSISTANT_ROUTER.read_text()
    module = ast.parse(module_source)
    function = _function_node(module, "create_media_message")
    function_source = ast.get_source_segment(module_source, function) or ""

    assert "read_upload_limited(" in function_source

    for node in ast.walk(function):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        assert not (
            isinstance(func, ast.Attribute)
            and func.attr == "read"
            and isinstance(func.value, ast.Name)
            and func.value.id == "file"
        )
