"""Approved frontend regressions exercised without a database or business rows."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import RedirectResponse

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / "copa_telmex_dashboard.py"


def _isolated_home():
    source = ast.parse(ENTRY.read_text())
    nodes = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {"_has_internal_session", "_is_session_empleado_role", "root_enterprise_home"}]
    for node in nodes:
        node.decorator_list = []
        node.returns = None
        for arg in node.args.args:
            arg.annotation = None
    namespace = {"RedirectResponse": RedirectResponse, "_redirect_to_login": lambda request, fallback: RedirectResponse('/login', status_code=307)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ENTRY), "exec"), namespace)
    return namespace["root_enterprise_home"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,target", [("admin", "/panel"), ("finanzas", "/panel"), ("superadmin", "/panel"), ("super_admin", "/panel"), ("empleado", "/panel"), ("coordinador", "/assistant"), ("unknown", "/assistant"), ("", "/assistant")])
async def test_home_preserves_profile_destinations(role, target):
    response = await _isolated_home()(SimpleNamespace(session={"empleado_id": "test", "rol": role}))
    assert response.headers["location"] == target


@pytest.mark.asyncio
async def test_home_retains_login_gate():
    response = await _isolated_home()(SimpleNamespace(session={}))
    assert response.headers["location"] == "/login"


def test_document_operational_reference_and_sort_stay_aligned():
    source = (ROOT / "src/devnous/gastos/routes/user_routes.py").read_text()
    block = source.split("async def documentos_todos(", 1)[1].split("\n@router.", 1)[0]
    header = block.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert header.index('data-sort-key="referencia_operaciones"') < header.index('data-sort-key="id_interno"')
    body = block.split('rows_html += f"""', 1)[1].split("</tr>", 1)[0]
    assert body.index('row_values["referencia_operaciones"]') < body.index('doc_id_short')
    assert 'data-default-sort-index="11"' in block


def test_finance_projection_preserves_real_document_and_expense_references():
    from samchat.finance_platform.service import _serialize_document, _serialize_expense

    assert _serialize_document(SimpleNamespace(referencia_operaciones="032"))["referencia_operaciones"] == "032"
    expense = SimpleNamespace(informe_documento=SimpleNamespace(referencia_operaciones="041"), solicitud_documento=SimpleNamespace(referencia_operaciones="042"))
    assert _serialize_expense(expense)["referencia_operaciones"] == "041"
    expense.informe_documento = None
    assert _serialize_expense(expense)["referencia_operaciones"] == "042"
    expense.solicitud_documento = None
    assert _serialize_expense(expense)["referencia_operaciones"] is None


@pytest.mark.asyncio
async def test_reference_lookup_is_batched_and_does_not_query_unlinked_expenses():
    from contextlib import nullcontext
    from unittest.mock import AsyncMock
    from devnous.gastos.routes.admin_routes import _expense_operational_references, _expense_operational_reference

    session = SimpleNamespace(no_autoflush=nullcontext(), execute=AsyncMock())
    assert await _expense_operational_references(session, [SimpleNamespace()]) == {}
    session.execute.assert_not_called()
    session.execute.return_value = SimpleNamespace(all=lambda: [("informe", "041"), ("solicitud", "042")])
    expense = SimpleNamespace(informe_documento_id="informe", solicitud_documento_id="solicitud")
    references = await _expense_operational_references(session, [expense, expense])
    session.execute.assert_awaited_once()
    statement = session.execute.call_args.args[0]
    assert set(statement.compile().params["id_1"]) == {"informe", "solicitud"}
    assert _expense_operational_reference(expense, references) == "041"
    assert _expense_operational_reference(SimpleNamespace(), references) == "—"


@pytest.mark.asyncio
async def test_expense_board_and_client_csv_include_actual_reference(monkeypatch):
    from contextlib import nullcontext
    from datetime import datetime
    import inspect
    import json
    import re
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from devnous.gastos.models import ExpenseReport
    from devnous.gastos.routes import admin_routes

    informe_id = uuid4()
    expense = ExpenseReport(
        id=uuid4(), informe_documento_id=informe_id,
        numero_referencia="G-TEST", gasto_cantidad=100,
        created_at=datetime(2026, 10, 9), updated_at=datetime(2026, 10, 9),
    )

    def scalars(values):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: values))
    session = SimpleNamespace(
        no_autoflush=nullcontext(),
        execute=AsyncMock(side_effect=[scalars([expense]), SimpleNamespace(all=lambda: [(informe_id, "032")]), scalars([])]),
    )
    monkeypatch.setattr(admin_routes, "fetch_expense_ids_with_archivo_data", AsyncMock(return_value=set()))
    monkeypatch.setattr(admin_routes, "fetch_gasto_adjuntos_meta_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(admin_routes, "render_admin_navigation", lambda *args, **kwargs: "")
    kwargs = {name: None for name in inspect.signature(admin_routes.admin_expenses).parameters}
    kwargs.update(request=SimpleNamespace(), session=session, current_empleado=SimpleNamespace())
    html = await admin_routes.admin_expenses(**kwargs)
    assert "Error" not in html.split("<title>", 1)[1].split("</title>", 1)[0]
    assert '<th>Referencia</th>\n                        <th>Referencia Operaciones</th>' in html
    assert '<td>032</td>' in html
    assert 'escapeCsv(row.numero_referencia), escapeCsv(row.referencia_operaciones)' in html
    # Verify the export actually contains the owning reference beside SamChat's.
    match = re.search(r'<script type="application/json" id="expenses-export-data">(.*?)</script>', html)
    assert match is not None
    rows = json.loads(match.group(1))
    assert rows[0]["numero_referencia"] == "G-TEST"
    assert rows[0]["referencia_operaciones"] == "032"
    session.execute.assert_awaited()
    assert session.execute.await_count == 3
