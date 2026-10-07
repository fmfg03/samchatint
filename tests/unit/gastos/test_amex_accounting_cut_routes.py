"""Acceptance tests for finance review, confirmation and immutable cut downloads."""

import io
import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from openpyxl import load_workbook
from starlette.requests import Request

from devnous.gastos.routes import admin_amex_accounting_routes as routes
from devnous.gastos.routes.dependencies import get_current_empleado, get_db_session


def session_with(*rows):
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(scalars=lambda rows=row: SimpleNamespace(all=lambda: rows))
        for row in rows
    ]
    return session


@pytest.mark.asyncio
async def test_reports_empty_and_escaped_reference():
    for documents in (
        [],
        [SimpleNamespace(id=uuid4(), numero_referencia="<script>x</script>")],
    ):
        response = await routes.amex_accounting_reports(
            session=session_with(documents), current_empleado=SimpleNamespace()
        )
        html = response.body.decode()
        assert "<script>" not in html
        assert ("Sin informes" if not documents else "&lt;script&gt;") in html


@pytest.mark.asyncio
async def test_review_form_check_choice_versions_and_adjustment_downloads_escape():
    informe = SimpleNamespace(
        id=uuid4(), tipo="INFORME", cuenta_gastos_id=uuid4(), numero_referencia="<I>"
    )
    expense = SimpleNamespace(
        id=uuid4(),
        numero_referencia="<G>",
        concepto="<script>bad</script>",
        fecha=datetime(2026, 9, 15),
        gasto_cantidad=116,
    )
    account = SimpleNamespace(id=uuid4(), codigo="1170-002-004", nombre="<Partner>")
    review = SimpleNamespace(
        expense_id=expense.id,
        treatment="partner_receivable",
        debtor_account_id=account.id,
        version=3,
        reason='" onmouseover="bad',
    )
    cut = SimpleNamespace(id=uuid4(), kind="initial", accounting_date=date(2026, 9, 30))
    session = session_with([expense], [review], [account], [cut])
    session.get.return_value = informe
    request = Request(
        {"type": "http", "query_string": b"msg=%3Cscript%3Ex%3C%2Fscript%3E"}
    )
    response = await routes.amex_accounting_report_review(
        informe.id, request, session, SimpleNamespace()
    )
    html = response.body.decode()
    assert "<script>" not in html
    assert 'name="reviewed"' in html and "checked required" in html
    assert "partner_receivable" in html and "&lt;Partner&gt;" in html
    assert "&quot; onmouseover=&quot;bad" in html
    assert str(expense.id) in html and "&quot;: 3" in html
    assert "Generar una póliza de ajuste" in html
    assert all(f"/cortes/{cut.id}.{fmt}" in html for fmt in ["xlsx", "csv", "zip"])


@pytest.mark.asyncio
@pytest.mark.parametrize("document", [None, SimpleNamespace(tipo="SOLICITUD")])
async def test_review_missing_or_wrong_document(document):
    session = AsyncMock()
    session.get.return_value = document
    with pytest.raises(HTTPException) as exc:
        await routes.amex_accounting_report_review(
            uuid4(), None, session, SimpleNamespace()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewed,debtor", [(False, ""), (True, "invalid")])
async def test_save_requires_explicit_review_and_valid_uuid(
    monkeypatch, reviewed, debtor
):
    service = AsyncMock()
    monkeypatch.setattr(routes, "review_amex_partida", service)
    session = AsyncMock()
    response = await routes.amex_accounting_review_save(
        uuid4(), uuid4(), reviewed, "expense", debtor, "", session, SimpleNamespace()
    )
    assert "error_msg=" in response.headers["location"]
    service.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "created"])
@pytest.mark.parametrize("treatment", ["expense", "partner_receivable"])
async def test_save_transaction_and_explicit_account(monkeypatch, status, treatment):
    service = AsyncMock(
        return_value=SimpleNamespace(status=status, reason="account_invalid")
    )
    monkeypatch.setattr(routes, "review_amex_partida", service)
    session = AsyncMock()
    account = uuid4()
    actor = SimpleNamespace(id=uuid4())
    response = await routes.amex_accounting_review_save(
        uuid4(),
        uuid4(),
        True,
        treatment,
        str(account),
        "Approved reason",
        session,
        actor,
    )
    assert service.await_args.kwargs["actor"] is actor
    assert service.await_args.kwargs["debtor_account_id"] == (
        account if treatment != "expense" else None
    )
    (session.rollback if status == "pending" else session.commit).assert_awaited_once()
    assert ("error_msg=" in response.headers["location"]) == (status == "pending")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "versions", ["[]", "null", '{"x":true}', '{"x":0}', '{"x":"1"}', "bad"]
)
async def test_confirm_rejects_invalid_versions(monkeypatch, versions):
    service = AsyncMock()
    monkeypatch.setattr(routes, "create_amex_accounting_cut", service)
    response = await routes.amex_accounting_cut_confirm(
        uuid4(),
        versions,
        date(2026, 9, 30),
        True,
        False,
        "",
        AsyncMock(),
        SimpleNamespace(),
    )
    assert "error_msg=" in response.headers["location"]
    service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "created", "exists"])
async def test_confirm_commit_or_rollback_and_passes_version_map(monkeypatch, status):
    service = AsyncMock(
        return_value=SimpleNamespace(status=status, reason="stale_review")
    )
    monkeypatch.setattr(routes, "create_amex_accounting_cut", service)
    session = AsyncMock()
    versions = {str(uuid4()): 2}
    await routes.amex_accounting_cut_confirm(
        uuid4(),
        json.dumps(versions),
        date(2026, 10, 1),
        True,
        True,
        "Adjustment",
        session,
        SimpleNamespace(),
    )
    assert service.await_args.kwargs["expected_versions"] == versions
    assert service.await_args.kwargs["adjustment"] is True
    (session.rollback if status == "pending" else session.commit).assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_checkbox_required(monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr(routes, "create_amex_accounting_cut", service)
    response = await routes.amex_accounting_cut_confirm(
        uuid4(),
        "{}",
        date(2026, 9, 30),
        False,
        False,
        "",
        AsyncMock(),
        SimpleNamespace(),
    )
    assert "error_msg=" in response.headers["location"]
    service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["format", "missing", "bad_snapshot"])
async def test_cut_export_fails_closed(failure):
    session = AsyncMock()
    cut = SimpleNamespace(snapshot_json={}, informe_id=uuid4())
    session.get.side_effect = (
        [None] if failure == "missing" else [cut, SimpleNamespace()]
    )
    with pytest.raises(HTTPException) as exc:
        await routes.amex_accounting_cut_export(
            uuid4(), "pdf" if failure == "format" else "csv", session, SimpleNamespace()
        )
    assert exc.value.status_code == (409 if failure == "bad_snapshot" else 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("role,status", [("finanzas", 200), ("empleado", 403)])
async def test_real_permission_dependency_and_uuid_validation(role, status):
    app = FastAPI()
    app.include_router(routes.router)
    actor = SimpleNamespace(id=uuid4(), rol=role, permisos=[], activo=True)
    app.dependency_overrides[get_current_empleado] = lambda: actor
    app.dependency_overrides[get_db_session] = lambda: session_with([])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(routes.BASE)
        assert response.status_code == status
        if status == 200:
            response = await client.get(routes.BASE + "/informes/not-a-uuid")
            assert response.status_code == 422
            response = await client.post(
                routes.BASE + f"/informes/{uuid4()}/cortes",
                data={
                    "expected_versions": "{}",
                    "accounting_date": "not-date",
                    "confirmed": "true",
                },
            )
            assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["xlsx", "csv", "zip"])
async def test_cut_download_actual_format_and_read_only(format):
    from tests.unit.gastos.test_amex_cut_exports import frozen_cut

    session = AsyncMock()
    cut = frozen_cut()
    session.get.return_value = cut
    response = await routes.amex_accounting_cut_export(
        cut.id, format, session, SimpleNamespace()
    )
    assert response.status_code == 200
    assert f"Corte_AMEX_{cut.id}.{format}" in response.headers["content-disposition"]
    assert (
        response.body.startswith(b"PK")
        if format != "csv"
        else b"FIN_PARTIDAS" in response.body
    )
    if format == "xlsx":
        workbook = load_workbook(io.BytesIO(response.body))
        sheet = workbook["Poliza COI"]
        assert [sheet[cell].value for cell in ("C2", "D2", "E2")] == [None] * 3
        assert sheet["C3"].value.startswith(
            "Operaciones: OP-FROZEN / Beneficiario: Beneficiaria congelada / "
            "Contexto: Torneo congelado / "
        )
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
