"""Acceptance checks for the explicit review action and fixed batch selection."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from devnous.gastos.routes import admin_routes as routes
from scripts import request_merced_cleanup_review as batch


def request(token="valid"):
    return Request(
        {"type": "http", "query_string": b"", "session": {"cleanup_review_csrf": token}}
    )


def test_review_token_is_created_once_and_bound_to_session():
    req = request("")
    token = routes._cleanup_review_token(req)
    assert token and routes._cleanup_review_token(req) == token
    routes._check_cleanup_review_token(req, token)
    with pytest.raises(HTTPException):
        routes._check_cleanup_review_token(request(""), token)


@pytest.mark.asyncio
async def test_unexpected_confirmation_error_rolls_back(monkeypatch):
    rolled_back = []

    async def allowed(*a, **kw):
        return None

    async def confirm(*a, **kw):
        raise RuntimeError("temporary storage error")

    async def rollback():
        rolled_back.append(True)

    monkeypatch.setattr(routes, "explicit_tool_decision", allowed)
    monkeypatch.setattr(routes, "confirm_review", confirm)
    with pytest.raises(RuntimeError):
        await routes.confirmar_revision_contable(
            uuid4(),
            request(),
            SimpleNamespace(rollback=rollback),
            SimpleNamespace(id=uuid4()),
            "valid",
            "yes",
        )
    assert rolled_back == [True]


@pytest.mark.asyncio
async def test_confirmation_requires_session_token_and_explicit_checkbox():
    for token, confirmed, status in [("bad", "yes", 403), ("valid", "", 400)]:
        with pytest.raises(HTTPException) as exc:
            await routes.confirmar_revision_contable(
                uuid4(), request(), None, SimpleNamespace(id=uuid4()), token, confirmed
            )
        assert exc.value.status_code == status


@pytest.mark.asyncio
async def test_confirmation_honors_explicit_denial(monkeypatch):
    async def denied(*a, **kw):
        return False

    monkeypatch.setattr(routes, "explicit_tool_decision", denied)
    with pytest.raises(HTTPException) as exc:
        await routes.confirmar_revision_contable(
            uuid4(), request(), None, SimpleNamespace(id=uuid4()), "valid", "yes"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_confirmation_commits_once_and_redirects_to_merced(monkeypatch):
    calls = []
    actor, expense_id = uuid4(), uuid4()

    async def allowed(*a, **kw):
        return None

    async def confirm(session, expense, employee):
        calls.append((expense, employee))

    async def commit():
        calls.append("commit")

    monkeypatch.setattr(routes, "explicit_tool_decision", allowed)
    monkeypatch.setattr(routes, "confirm_review", confirm)
    response = await routes.confirmar_revision_contable(
        expense_id,
        request(),
        SimpleNamespace(commit=commit),
        SimpleNamespace(id=actor),
        "valid",
        "yes",
    )
    assert calls == [(expense_id, actor), "commit"]
    assert response.status_code == 303
    assert "q=La+Merced" in response.headers["location"]


@pytest.mark.asyncio
async def test_confirmation_rolls_back_failed_review(monkeypatch):
    rolled_back = []

    async def allowed(*a, **kw):
        return None

    async def confirm(*a, **kw):
        raise ValueError("El gasto no tiene una revisión solicitada.")

    async def rollback():
        rolled_back.append(True)

    monkeypatch.setattr(routes, "explicit_tool_decision", allowed)
    monkeypatch.setattr(routes, "confirm_review", confirm)
    with pytest.raises(HTTPException) as exc:
        await routes.confirmar_revision_contable(
            uuid4(),
            request(),
            SimpleNamespace(rollback=rollback),
            SimpleNamespace(id=uuid4()),
            "valid",
            "yes",
        )
    assert exc.value.status_code == 400
    assert rolled_back == [True]


def test_preview_is_exact_and_rejects_any_drift():
    manifest = json.loads(batch.MANIFEST.read_text())
    assert len(manifest["items"]) == 24
    assert len(batch.validate_selection(manifest, manifest["items"])) == 64
    changed = json.loads(json.dumps(manifest["items"]))
    changed[0]["gasto_cantidad"] += 1
    for rows in [
        changed,
        manifest["items"][:-1],
        manifest["items"] + manifest["items"][:1],
    ]:
        with pytest.raises(ValueError, match="selección"):
            batch.validate_selection(manifest, rows)


@pytest.mark.asyncio
async def test_confirmation_dependency_rejects_non_finance_actor():
    endpoint = next(
        r
        for r in routes.router.routes
        if getattr(r, "endpoint", None) is routes.confirmar_revision_contable
    )
    guard = next(
        d.call for d in endpoint.dependant.dependencies if d.name == "current_empleado"
    )
    with pytest.raises(HTTPException) as exc:
        await guard(SimpleNamespace(rol="empleado", permissions=set()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_marker_and_confirmation_render_in_real_cleanup_page(monkeypatch):
    from datetime import datetime

    from devnous.gastos.models import CuentaContable, Documento, ExpenseReport
    from devnous.gastos.services.cleanup_review_service import ReviewState

    expense = ExpenseReport(
        id=uuid4(),
        numero_referencia="O-26001239",
        proyecto="La Merced",
        concepto="Pago semanal",
        gasto_cantidad=2289.83,
        fecha=datetime(2026, 10, 26),
        metodo_pago="TRANSFERENCIA",
    )
    expense.documento = Documento(
        id=uuid4(),
        tipo="SOLICITUD",
        numero_referencia="S-26000403",
        referencia_operaciones="325",
    )
    expense.documento_id = expense.documento.id
    expense.cuenta_contable = CuentaContable(
        id=uuid4(), codigo="5300-020-005", nombre="NO DEDUCIBLE"
    )
    expense.empleado = None
    expense._cleanup_review_state = ReviewState(pending=True)

    async def load_expenses(*a, **kw):
        return [expense]

    async def cfdis(*a, **kw):
        return []

    async def preview(*a, **kw):
        return {"issues": [], "preview": {"taxes": {}}}

    async def refs(*a, **kw):
        return {expense.documento_id: "325"}

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    async def execute(*a, **kw):
        return Result()

    monkeypatch.setattr(routes, "load_cleanup_expenses", load_expenses)
    monkeypatch.setattr(routes, "list_unassigned_cfdi_options", cfdis)
    monkeypatch.setattr(routes, "safe_build_cleanup_preview", preview)
    monkeypatch.setattr(routes, "_expense_operational_references", refs)
    monkeypatch.setattr(routes, "render_admin_navigation", lambda *a, **kw: "")
    html = await routes.gastos_sin_cuenta_contable(
        request(),
        SimpleNamespace(execute=execute),
        period="2026-10",
        bi_year=None,
        bi_scope=None,
        q="La Merced",
        document_type="all",
        issue="review",
        focus_expense_id=None,
        document_id=None,
        current_empleado=SimpleNamespace(),
    )
    assert "O-26001239" in html
    assert "325" in html
    assert "La Merced · revisión contable solicitada" in html
    assert "Confirmar revisión" in html
    assert f'action="/admin/gastos/{expense.id}/confirmar-revision-contable"' in html
    assert 'name="csrf_token"' in html
    assert 'value="review" selected' in html
