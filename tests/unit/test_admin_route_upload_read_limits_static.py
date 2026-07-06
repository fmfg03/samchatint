import ast
from pathlib import Path


ADMIN_ROUTES = Path("src/devnous/gastos/routes/admin_routes.py")


def test_admin_routes_do_not_buffer_uploads_with_direct_read():
    module = ast.parse(ADMIN_ROUTES.read_text())
    direct_reads: list[tuple[int, str]] = []

    for node in ast.walk(module):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "read":
            continue
        receiver = func.value
        if isinstance(receiver, ast.Name):
            direct_reads.append((node.lineno, receiver.id))

    assert direct_reads == []

