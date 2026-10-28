import io
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import dependencies
from devnous.gastos.routes import user_routes as routes


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-only-payroll-amount-secret")
    monkeypatch.setattr(routes, "render_top_navigation", lambda *a: "")
    monkeypatch.setattr(routes, "_payroll_subnav", lambda *a: "")
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    session.get.return_value = None
    actor = SimpleNamespace(
        id=uuid4(),
        rol="finanzas",
        activo=True,
        nombre="Persona prueba",
        permissions=set(),
    )
    state = {"actor": actor}

    async def identity():
        if state["actor"] is None:
            raise HTTPException(status_code=401)
        return state["actor"]

    async def db():
        yield session

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-only-cookie-secret")
    for route in routes.router.routes:
        if getattr(route, "path", "").startswith("/admin/nomina/carga-importes"):
            app.add_api_route(
                route.path,
                route.endpoint,
                methods=list(route.methods),
                response_class=HTMLResponse,
            )
    app.dependency_overrides[dependencies.get_current_empleado] = identity
    app.dependency_overrides[dependencies.get_db_session] = db
    with TestClient(app) as client:
        yield client, session, state


def form(client):
    response = client.get("/admin/nomina/carga-importes")
    assert response.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
    return {"csrf_token": csrf, "employer_id": str(uuid4()), "period_id": str(uuid4())}


def plan():
    return {
        "rows": [
            {
                "source_row": 7,
                "employee_code": "001",
                "source_name": "<script>unsafe</script>",
                "components": [
                    {
                        "label": "SUELDO",
                        "amount": "100.00",
                        "account_code": "5200",
                        "pending_mapping": True,
                    }
                ],
            }
        ],
        "errors": [],
        "warnings": ["Recalcule el archivo"],
        "pending_count": 1,
        "employer_label": "Empresa prueba",
        "period_label": "Octubre 2026",
        "totals": {
            "gross_total": "100.00",
            "deductions_total": "0.00",
            "net_total": "100.00",
        },
        "plan_sha256": "a" * 64,
    }


def files():
    return {
        "file": (
            "n.xlsx",
            io.BytesIO(b"test workbook"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def mock_preview(monkeypatch, value=None):
    monkeypatch.setattr(
        routes, "_read_payroll_amount_upload", AsyncMock(return_value=object())
    )
    preview = AsyncMock(return_value=plan() if value is None else value)
    monkeypatch.setattr(routes, "build_payroll_amount_preview", preview)
    return preview


def test_preview_escapes_content_and_does_not_write(harness, monkeypatch):
    client, session, _ = harness
    data = form(client)
    mock_preview(monkeypatch)
    response = client.post(
        "/admin/nomina/carga-importes/preview", data=data, files=files()
    )
    assert response.status_code == 200
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in response.text
    assert "<script>unsafe" not in response.text
    assert "Pendiente de cuenta" in response.text
    assert 'name="preview_token"' in response.text
    session.commit.assert_not_awaited()


def test_errors_disable_confirmation(harness, monkeypatch):
    client, _, _ = harness
    data = form(client)
    value = plan()
    value["errors"] = ["Fila 7: diferencia en neto."]
    mock_preview(monkeypatch, value)
    response = client.post(
        "/admin/nomina/carga-importes/preview", data=data, files=files()
    )
    assert response.status_code == 422
    assert 'name="preview_token"' not in response.text


def test_invalid_workbook_is_a_controlled_error(harness):
    client, session, _ = harness
    response = client.post(
        "/admin/nomina/carga-importes/preview", data=form(client), files=files()
    )
    assert response.status_code == 422
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("path", ["preview", "confirmar"])
def test_csrf_is_required(harness, path):
    client, session, _ = harness
    data = {**form(client), "csrf_token": "altered", "preview_token": "fake"}
    response = client.post(
        "/admin/nomina/carga-importes/" + path, data=data, files=files()
    )
    assert response.status_code == 403
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("role,status", [("empleado", 403), (None, 401)])
def test_finance_authority_is_enforced_on_every_route(harness, role, status):
    client, session, state = harness
    data = form(client)
    state["actor"] = SimpleNamespace(rol=role, permissions=set()) if role else None
    for method, path in [
        ("get", ""),
        ("post", "/preview"),
        ("post", "/confirmar"),
        ("get", "/" + str(uuid4())),
    ]:
        if method == "get":
            response = client.get("/admin/nomina/carga-importes" + path)
        else:
            response = client.post(
                "/admin/nomina/carga-importes" + path,
                data={**data, "preview_token": "x"},
                files=files(),
            )
        assert response.status_code == status
    session.commit.assert_not_awaited()


def test_confirm_bound_to_actor_selection_and_plan(harness, monkeypatch):
    client, _, state = harness
    data = form(client)
    mock_preview(monkeypatch)
    response = client.post(
        "/admin/nomina/carga-importes/preview", data=data, files=files()
    )
    data["preview_token"] = re.search(
        r'name="preview_token" value="([^"]+)"', response.text
    ).group(1)
    result_id = uuid4()
    confirm = AsyncMock(return_value=result_id)
    monkeypatch.setattr(routes, "confirm_payroll_amount_import", confirm)
    response = client.post(
        "/admin/nomina/carga-importes/confirmar",
        data=data,
        files=files(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(str(result_id))
    assert confirm.await_args.kwargs["actor_id"] == state["actor"].id
    assert confirm.await_args.kwargs["expected_plan_sha256"] == "a" * 64
    data["employer_id"] = str(uuid4())
    assert (
        client.post(
            "/admin/nomina/carga-importes/confirmar", data=data, files=files()
        ).status_code
        == 409
    )
    assert confirm.await_count == 1


@pytest.mark.parametrize(
    "kind", ["bad", "expired", "wrong_actor", "invalid_claim", "changed_file"]
)
def test_confirmation_rejects_invalid_or_stale_preview(harness, monkeypatch, kind):
    client, _, state = harness
    data = form(client)
    claim = {
        "actor": str(state["actor"].id),
        "employer": data["employer_id"],
        "period": data["period_id"],
        "plan": "a" * 64,
    }
    if kind == "wrong_actor":
        claim["actor"] = str(uuid4())
    if kind == "invalid_claim":
        claim = []
    signer = routes._payroll_amount_signer()
    if kind == "expired":
        with monkeypatch.context() as clock:
            clock.setattr(
                "itsdangerous.timed.TimestampSigner.get_timestamp", lambda self: 100
            )
            token = signer.dumps(claim)
    else:
        token = "bad" if kind == "bad" else signer.dumps(claim)
    data["preview_token"] = token
    mock_preview(monkeypatch)
    confirm = AsyncMock(side_effect=ValueError("El archivo cambió"))
    monkeypatch.setattr(routes, "confirm_payroll_amount_import", confirm)
    response = client.post(
        "/admin/nomina/carga-importes/confirmar", data=data, files=files()
    )
    assert response.status_code == 409
    assert confirm.await_count == (1 if kind == "changed_file" else 0)


def test_receipt_preserves_provenance_and_pending_state(harness):
    client, session, _ = harness
    batch = SimpleNamespace(
        id=uuid4(),
        status="pending_mapping",
        created_at=datetime.now(timezone.utc),
        created_by_empleado_id=uuid4(),
        period_id=uuid4(),
        payroll_employer_id=uuid4(),
        gross_total="100.00",
        deductions_total="0.00",
        net_total="100.00",
        file_sha256="a" * 64,
    )
    session.get.return_value = batch
    session.execute.return_value.scalars.return_value.all.return_value = [
        SimpleNamespace(source_json=plan()["rows"][0])
    ]
    response = client.get(f"/admin/nomina/carga-importes/{batch.id}")
    assert response.status_code == 200
    assert "no lista para contabilizar" in response.text
    assert "a" * 64 in response.text
    session.get.return_value = None
    assert client.get(f"/admin/nomina/carga-importes/{uuid4()}").status_code == 404


def test_missing_signing_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        routes._payroll_amount_signer()
    assert exc.value.status_code == 503


def test_payroll_navigation_exposes_amount_intake():
    assert "/admin/nomina/carga-importes" in routes._payroll_subnav("carga-importes")


def test_payroll_import_tables_never_enter_runtime_startup_ddl():
    from devnous.gastos.amex_schema_policy import runtime_managed_tables
    from devnous.gastos.models import Base, PayrollAmountImport, PayrollAmountImportLine

    managed = {table.name for table in runtime_managed_tables(Base.metadata)}
    assert PayrollAmountImport.__tablename__ not in managed
    assert PayrollAmountImportLine.__tablename__ not in managed
    assert "documentos" in managed
