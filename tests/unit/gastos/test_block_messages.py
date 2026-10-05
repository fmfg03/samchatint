"""Acceptance checks for evidence-backed expense blockers and safe presentation."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.models import (
    CFDIReport,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    ProveedorCliente,
)
from devnous.gastos.routes import user_routes
from devnous.gastos.routes.block_messages import (
    ExpenseBlockRoute,
    duplicate_invoice_message,
    explain_block,
    expense_lock_reason,
)
from devnous.gastos.services.cfdi_expense_link_service import (
    ExpenseCFDIDuplicateError,
    _enforce_expense_cfdi_uniqueness,
)

FISCAL_UUID = "11111111-2222-4333-8444-555555555555"


@pytest.mark.parametrize(
    ("cause", "action"),
    [
        ("No tienes permiso para cerrar este informe", "responsable autorizado"),
        ("Acceso de solo lectura al informe", "permite consultar"),
        ("El informe está en estado enviado", "estado y el requisito"),
        ("No puedes seleccionar gastos cancelados", "estado del registro"),
        ("El archivo fue eliminado", "estado del registro"),
        ("Gastos de monedas distintas: MXN, USD", "misma moneda"),
        ("Fecha del gasto inválida", "fecha válida"),
        ("El PDF está vacío", "Revisa el archivo"),
        ("El XML excede el máximo de 100 bytes", "Revisa el archivo"),
        ("TOTAL debe ser mayor a cero", "Revisa el importe"),
        ("El concepto es requerido", "Completa o corrige"),
        ("Seleccione un beneficiario activo", "Completa o corrige"),
        ("Gasto no encontrado", "Verifica el registro"),
        ("La factura ya está vinculada", "revisen la vinculación"),
        ("No se pudo guardar el gasto", "No se pudo confirmar la causa"),
        ("Ocurrió un error al guardar", "No se pudo confirmar la causa"),
        ("El requisito específico no se cumple", "requisito indicado"),
    ],
)
def test_known_cause_is_preserved_and_next_step_is_specific(cause, action):
    message = explain_block(cause)
    assert cause in message
    assert action in message
    assert explain_block(message) == message


def test_empty_and_technical_errors_do_not_invent_or_expose_evidence():
    assert explain_block("") == ""
    message = explain_block("asyncpg error DATABASE_URL=private")
    assert "private" not in message
    assert "causa específica" in message


def test_actual_state_on_403_is_not_misreported_as_a_permission_problem():
    message = explain_block("La cuenta está en estado cerrada", status_code=403)
    assert "requisito pendiente" in message
    assert "tu acceso actual" not in message


def _app():
    app = FastAPI()
    router = APIRouter(route_class=ExpenseBlockRoute)

    @router.post("/informes-de-gastos/test")
    def blocked():
        raise HTTPException(
            403, "El informe está en estado enviado", headers={"X-Reason": "state"}
        )

    @router.post("/gastos/test")
    def invalid_file():
        return RedirectResponse(
            "/gastos/test?error=file&error_msg=El+PDF+est%C3%A1+vac%C3%ADo"
            "&return_to=%2Fgastos",
            303,
        )

    @router.post("/documentos/test")
    def success():
        return RedirectResponse("/documentos/test?success=ok&msg=Guardado", 303)

    @router.get("/other")
    def other():
        raise HTTPException(403, "Original")

    @router.post("/gastos/missing")
    def missing():
        raise HTTPException(404, "Gasto no encontrado")

    @router.post("/gastos/detail")
    def structured_detail():
        raise HTTPException(400, {"field": "original"})

    @router.post("/api/informes-de-gastos/cfdi-autofill")
    def autofill():
        return JSONResponse(
            {"ok": False, "error": "El PDF está vacío"}, status_code=422
        )

    @router.post("/gastos/required")
    def required(concepto: str = Form(...)):
        return {"concepto": concepto}

    @router.post("/gastos/number")
    def number(monto: float = Form(...)):
        return {"monto": monto}

    @router.post("/gastos/error-code")
    def error_code():
        return JSONResponse({"ok": False, "error": "invalid_file"}, status_code=400)

    @router.post("/gastos/code-redirect/{code}")
    def code_redirect(code: str):
        return RedirectResponse(f"/gastos?error={code}", 303)

    app.include_router(router)
    return app


def test_real_http_handler_preserves_status_headers_and_known_cause():
    with TestClient(_app()) as client:
        response = client.post("/informes-de-gastos/test")
    assert response.status_code == 403
    assert response.headers["X-Reason"] == "state"
    assert "estado enviado" in response.json()["detail"]
    assert "Qué hacer:" in response.json()["detail"]


def test_real_redirect_keeps_navigation_and_adds_file_guidance():
    with TestClient(_app()) as client:
        response = client.post("/gastos/test", follow_redirects=False)
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert response.status_code == 303
    assert params["return_to"] == ["/gastos"]
    assert params["error"] == ["file"]
    assert "PDF está vacío" in params["error_msg"][0]
    assert "Revisa el archivo" in params["error_msg"][0]


def test_success_and_other_domains_are_unchanged():
    with TestClient(_app()) as client:
        assert client.get("/other").json()["detail"] == "Original"
        assert (
            client.post("/documentos/test", follow_redirects=False).headers["location"]
            == "/documentos/test?success=ok&msg=Guardado"
        )
        assert client.post("/gastos/detail").json()["detail"] == {"field": "original"}
        assert "Verifica el registro" in client.post("/gastos/missing").json()["detail"]


def test_json_and_required_fields_explain_actual_failure_without_changing_shape():
    with TestClient(_app()) as client:
        response = client.post("/api/informes-de-gastos/cfdi-autofill")
        assert response.status_code == 422
        assert response.json()["ok"] is False
        assert "PDF está vacío" in response.json()["error"]
        assert "Qué hacer:" in response.json()["error"]
        assert int(response.headers["content-length"]) == len(response.content)
        missing = client.post("/gastos/required")
        assert missing.status_code == 422
        assert missing.json()["detail"][0]["loc"] == ["body", "concepto"]
        assert "concepto es obligatorio" in missing.json()["detail"][0]["msg"]
        invalid = client.post("/gastos/number", data={"monto": "invalid"})
        assert "Revisa el campo monto" in invalid.json()["detail"][0]["msg"]
        assert client.post("/gastos/error-code").json()["error"] == "invalid_file"


@pytest.mark.parametrize(
    ("code", "cause"),
    [
        ("invalid_gasto_ids", "identificadores de gasto inválidos"),
        ("cuenta_cerrada", "informe está cerrado"),
        ("payment_proof_required", "comprobante de pago requerido"),
        ("unexpected_create_error", "No se pudo confirmar la causa específica"),
    ],
)
def test_code_only_redirects_have_visible_evidence_or_explicit_unknown(code, cause):
    with TestClient(_app()) as client:
        response = client.post(f"/gastos/code-redirect/{code}", follow_redirects=False)
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["error"] == [code]
    assert cause in params["error_msg"][0]
    assert "Qué hacer:" in params["error_msg"][0]


def test_duplicate_code_keeps_private_details_out_of_the_redirect():
    with TestClient(_app()) as client:
        response = client.post(
            "/gastos/code-redirect/expense_cfdi_duplicate", follow_redirects=False
        )
    assert "error_msg" not in parse_qs(urlsplit(response.headers["location"]).query)


def test_duplicate_redirect_uses_single_use_session_context_without_identifiers():
    duplicate_id = uuid4()
    error = ExpenseCFDIDuplicateError(FISCAL_UUID, duplicate_id)
    request = Request({"type": "http", "session": {}})
    location = user_routes._append_error_params(
        "/gastos/test/editar", error_msg=error, request=request
    )
    params = parse_qs(urlsplit(location).query)
    assert params == {"error": ["expense_cfdi_duplicate"]}
    assert request.session["expense_block_contexts"]["/gastos/test/editar"] == {
        "expense_id": str(duplicate_id),
        "fiscal_uuid": FISCAL_UUID,
    }
    assert FISCAL_UUID not in location and str(duplicate_id) not in location
    assert "error_msg" not in params
    assert "beneficiario" not in location and "600.00" not in location
    assert "Confirma factura compartida" not in str(error)


def test_actual_quick_capture_rolls_back_and_preserves_the_blocking_record(monkeypatch):
    from inspect import signature

    from fastapi.params import Param

    actor = SimpleNamespace(
        id=uuid4(), rol="usuario", nombre="Owner", departamento="Operaciones"
    )
    cuenta = SimpleNamespace(
        id=uuid4(),
        empleado_id=actor.id,
        estado="abierta",
        empleado=actor,
        referencia_base="654321",
        torneo=None,
        fase=None,
        categorias=[],
        edicion=None,
        currency="MXN",
    )
    document = SimpleNamespace(id=uuid4(), estado="borrador")
    duplicate_id = uuid4()
    expense = SimpleNamespace(id=uuid4())

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class CaptureSession:
        rollbacks = 0
        commits = 0

        def __init__(self):
            self.results = iter([cuenta, document, None, None, duplicate_id])

        async def execute(self, statement, params=None):
            return Result(next(self.results))

        async def rollback(self):
            self.rollbacks += 1

        async def commit(self):
            self.commits += 1

    async def no_schema_change(_session):
        return None

    async def provisional_expense(**kwargs):
        return expense

    monkeypatch.setattr(user_routes, "_ensure_expense_tip_schema", no_schema_change)
    monkeypatch.setattr(user_routes, "create_expense_from_data", provisional_expense)
    kwargs = {
        name: None
        for name, parameter in signature(
            user_routes.crear_gasto_rapido_en_informe
        ).parameters.items()
        if isinstance(parameter.default, Param)
    }
    # Form/File are Body descendants rather than Param in some FastAPI versions.
    kwargs.update(
        {
            name: None
            for name, parameter in signature(
                user_routes.crear_gasto_rapido_en_informe
            ).parameters.items()
            if hasattr(parameter.default, "embed")
        }
    )
    session = CaptureSession()
    kwargs.update(
        cuenta_id=cuenta.id,
        request=Request({"type": "http", "session": {}}),
        session=session,
        current_empleado=actor,
        concepto="Gasolina",
        fecha="2026-09-21",
        numero_factura=FISCAL_UUID,
        subtotal="600",
        descuento="0",
        impuestos_y_retenciones="0",
        propina_no_deducible="0",
    )
    response = asyncio.run(user_routes.crear_gasto_rapido_en_informe(**kwargs))
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert session.rollbacks == 1 and session.commits == 0
    assert response.status_code == 303
    assert params["error"] == ["expense_cfdi_duplicate"]
    assert params == {"error": ["expense_cfdi_duplicate"]}
    context = kwargs["request"].session["expense_block_contexts"]
    assert context[f"/informes-de-gastos/{cuenta.id}"]["expense_id"] == str(
        duplicate_id
    )
    assert "error_msg" not in params
    assert expense.informe_documento_id == document.id


class ReadSession:
    def __init__(self, records):
        self.records = records
        self.reads = []

    async def get(self, model, record_id):
        self.reads.append((model, record_id))
        return self.records.get((model, record_id))


def _duplicate_context(*, state="activo", manual_uuid=FISCAL_UUID, doc=True):
    actor_id, duplicate_id, document_id, cuenta_id, beneficiary_id, cfdi_id = (
        uuid4() for _ in range(6)
    )
    expense = SimpleNamespace(
        id=duplicate_id,
        estado_gasto=state,
        empleado_id=actor_id,
        numero_referencia="F-26000101",
        gasto_cantidad=600,
        currency="MXN",
        cfdi_uuid_manual=manual_uuid,
        cfdi_report_id=cfdi_id,
        informe_documento_id=document_id if doc else None,
        documento_id=None,
        cuenta_gastos_id=cuenta_id if doc else None,
    )
    document = SimpleNamespace(
        tipo="INFORME",
        numero_referencia="I-123456",
        referencia_operaciones="101",
        estado="enviado",
        beneficiario_empleado_id=beneficiary_id,
    )
    records = {
        (ExpenseReport, duplicate_id): expense,
        (Documento, document_id): document,
        (CuentaDeGastos, cuenta_id): SimpleNamespace(
            nombre="Caja chica de prueba", empleado_id=actor_id
        ),
        (Empleado, beneficiary_id): SimpleNamespace(nombre="Persona Beneficiaria"),
        (CFDIReport, cfdi_id): SimpleNamespace(cfdi_uuid=FISCAL_UUID.lower()),
    }
    request = Request(
        {
            "type": "http",
            "path": "/gastos/test/editar",
            "query_string": b"error=expense_cfdi_duplicate",
            "session": {
                "expense_block_contexts": {
                    "/gastos/test/editar": {
                        "expense_id": str(duplicate_id),
                        "fiscal_uuid": FISCAL_UUID.lower(),
                    }
                }
            },
        }
    )
    actor = SimpleNamespace(id=actor_id, rol="usuario", correo="")
    current = SimpleNamespace(
        numero_referencia="I-654321", referencia_operaciones="202", tipo="INFORME"
    )
    return request, ReadSession(records), actor, current


def test_authorized_duplicate_matches_approved_message_with_verified_context():
    request, session, actor, current = _duplicate_context()
    message = asyncio.run(
        user_routes._expense_block_message(
            request, session, actor, current_document=current
        )
    )
    for fact in (
        "$600.00 MXN",
        "F-26000101",
        "I-123456",
        "101",
        "Caja chica de prueba",
        "Persona Beneficiaria",
        "enviado",
        "I-654321",
        "202",
    ):
        assert fact in message
    assert "Esto no significa que tú la hayas usado antes" in message
    assert "No marques" in message
    assert "intento anterior" not in message


def test_unauthorized_reader_gets_no_other_report_details():
    request, session, actor, current = _duplicate_context()
    actor.id = uuid4()
    message = asyncio.run(
        user_routes._expense_block_message(
            request, session, actor, current_document=current
        )
    )
    assert "Los detalles deben revisarse" in message
    for private in ("600", "123456", "Beneficiaria", "Caja chica", "101"):
        assert private not in message
    assert not any(model in {Documento, Empleado} for model, _ in session.reads)


def test_duplicate_context_is_consumed_and_query_identifiers_are_not_trusted():
    request, session, actor, current = _duplicate_context()
    assert "I-123456" in asyncio.run(
        user_routes._expense_block_message(request, session, actor)
    )
    assert "No se pudo confirmar" in asyncio.run(
        user_routes._expense_block_message(request, session, actor)
    )
    request.scope["query_string"] += (
        f"&blocked_expense_id={uuid4()}&blocked_cfdi_uuid={FISCAL_UUID}"
    ).encode()
    fresh = Request(request.scope)
    assert "No se pudo confirmar" in asyncio.run(
        user_routes._expense_block_message(fresh, session, actor)
    )


def test_browser_redirect_keeps_session_context_without_financial_query_values():
    request, session, actor, current = _duplicate_context()
    context = request.session["expense_block_contexts"]["/gastos/test/editar"]
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-test-secret")

    @app.post("/capture")
    async def capture(request: Request):
        error = ExpenseCFDIDuplicateError(
            context["fiscal_uuid"], user_routes.UUIDType(context["expense_id"])
        )
        return RedirectResponse(
            user_routes._append_error_params(
                "/gastos/test/editar", error_msg=error, request=request
            ),
            status_code=303,
        )

    @app.get("/gastos/test/editar")
    async def detail(request: Request):
        return {
            "message": await user_routes._expense_block_message(request, session, actor)
        }

    with TestClient(app) as client:
        redirect = client.post("/capture", follow_redirects=False)
        location = redirect.headers["location"]
        assert location == "/gastos/test/editar?error=expense_cfdi_duplicate"
        assert "I-123456" in client.get(location).json()["message"]
        assert "No se pudo confirmar" in client.get(location).json()["message"]


def test_context_consumption_replaces_mapping_for_session_change_detection():
    request, session, actor, current = _duplicate_context()
    initial = request.session.copy()
    asyncio.run(user_routes._expense_block_message(request, session, actor))
    assert request.session != initial
    assert request.session["expense_block_contexts"] == {}
    assert initial["expense_block_contexts"]


@pytest.mark.parametrize("party_source", ["document", "account"])
def test_provider_beneficiary_uses_document_then_account_precedence(party_source):
    request, session, actor, current = _duplicate_context()
    document = next(v for (m, _), v in session.records.items() if m is Documento)
    account = next(v for (m, _), v in session.records.items() if m is CuentaDeGastos)
    document.beneficiario_empleado_id = None
    provider_id = uuid4()
    target = document if party_source == "document" else account
    target.beneficiario_proveedor_cliente_id = provider_id
    session.records[(ProveedorCliente, provider_id)] = SimpleNamespace(
        nombre="Proveedor de prueba"
    )
    message = asyncio.run(user_routes._expense_block_message(request, session, actor))
    assert "Proveedor de prueba" in message
    assert "Persona Beneficiaria" not in message


def test_blocking_transfer_request_is_identified_as_solicitud():
    request, session, actor, current = _duplicate_context()
    document = next(v for (m, _), v in session.records.items() if m is Documento)
    expense = next(v for (m, _), v in session.records.items() if m is ExpenseReport)
    expense.documento_id = expense.informe_documento_id
    expense.informe_documento_id = None
    expense.cuenta_gastos_id = None
    document.tipo = "SOLICITUD"
    document.numero_referencia = "S-26000101"
    message = asyncio.run(user_routes._expense_block_message(request, session, actor))
    assert "solicitud S-26000101" in message
    assert "informe S-26000101" not in message


def test_existing_global_read_delegate_can_see_details_without_mutation():
    request, session, actor, current = _duplicate_context()
    actor.id = user_routes.UUIDType("90701d00-5f0b-4b3d-b677-e491e53caf82")
    message = asyncio.run(
        user_routes._expense_block_message(
            request, session, actor, current_document=current
        )
    )
    assert "I-123456" in message
    assert "Beneficiaria" in message


@pytest.mark.parametrize("state", ["cancelado", None])
def test_cancelled_or_null_state_is_not_reported_as_an_active_blocker(state):
    request, session, actor, current = _duplicate_context(state=state)
    message = asyncio.run(
        user_routes._expense_block_message(
            request, session, actor, current_document=current
        )
    )
    assert "No se pudo confirmar" in message
    assert "600" not in message


def test_link_only_legacy_match_and_unavailable_context():
    request, session, actor, current = _duplicate_context(manual_uuid=None, doc=False)
    message = asyncio.run(user_routes._expense_block_message(request, session, actor))
    assert "F-26000101" in message
    assert "I-123456" not in message
    assert "600.00" in message
    session.records = {
        key: value for key, value in session.records.items() if key[0] != CFDIReport
    }
    assert "No se pudo confirmar" in asyncio.run(
        user_routes._expense_block_message(request, session, actor)
    )


def test_missing_record_invalid_context_and_ordinary_message():
    request, session, actor, current = _duplicate_context()
    session.records = {}
    assert "No se pudo confirmar" in asyncio.run(
        user_routes._expense_block_message(request, session, actor)
    )
    invalid = Request(
        {
            "type": "http",
            "query_string": b"error=expense_cfdi_duplicate&blocked_expense_id=invalid",
        }
    )
    assert "No se pudo confirmar" in asyncio.run(
        user_routes._expense_block_message(invalid, session, actor)
    )
    ordinary = Request({"type": "http", "query_string": b"error_msg=Original"})
    assert (
        asyncio.run(user_routes._expense_block_message(ordinary, session, actor))
        == "Original"
    )


def test_dynamic_text_is_escaped_by_the_html_views():
    # Execute the same rendering boundary used by the report and expense pages.
    message = duplicate_invoice_message(
        amount=Decimal("600"),
        currency="MXN",
        expense_reference="F-test",
        report_reference="I-test",
        operations_reference="101",
        report_name='<script>alert("x")</script>',
        beneficiary="A & B",
        state="enviado",
    )
    rendered = user_routes.escape(message)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered and "A &amp; B" in rendered


@pytest.mark.parametrize(
    ("invoice_state", "doc_state", "expected"),
    [
        (None, "enviado", "el documento I-123 está en estado enviado"),
        ("completada", "borrador", "la factura está en estado completada"),
        (
            "en_proceso",
            "aprobado",
            "el documento I-123 está en estado aprobado; "
            "la factura está en estado en_proceso",
        ),
        (None, "borrador", ""),
    ],
)
def test_lock_banner_lists_only_observed_conditions(invoice_state, doc_state, expected):
    expense = SimpleNamespace(estado_factura=invoice_state)
    document = SimpleNamespace(id=uuid4(), numero_referencia="I-123", estado=doc_state)
    assert expense_lock_reason(expense, document) == expected


def test_capture_reason_hides_state_from_unauthorized_actor():
    actor = SimpleNamespace(id=uuid4(), rol="usuario")
    cuenta = SimpleNamespace(empleado_id=actor.id, estado="cerrada")
    document = SimpleNamespace(numero_referencia="I-123", estado="enviado")
    assert "cerrada" in user_routes._quick_capture_block_reason(cuenta, document, actor)
    actor.id = uuid4()
    assert (
        user_routes._quick_capture_block_reason(cuenta, document, actor)
        == "No tienes permiso para capturar gastos en este informe."
    )
    actor.rol = "finanzas"
    cuenta.estado = "abierta"
    assert "no tiene documento INFORME" in user_routes._quick_capture_block_reason(
        cuenta, None, actor
    )
    assert "I-123" in user_routes._quick_capture_block_reason(cuenta, document, actor)


class UniquenessSession:
    """Run the real duplicate SELECT against an isolated, in-memory database."""

    def __init__(self, connection):
        self.connection = connection

    async def execute(self, statement, params=None):
        if "pg_advisory_xact_lock" in str(statement):
            return None
        return self.connection.execute(statement, params or {})


def test_real_duplicate_select_excludes_self_and_cancelled_rows():
    engine = create_engine("sqlite://")
    current_id, cancelled_id, other_id = uuid4(), uuid4(), uuid4()
    expense = SimpleNamespace(id=current_id, cfdi_uuid_manual=FISCAL_UUID)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE expense_reports (id CHAR(32), estado_gasto TEXT, "
                "cfdi_uuid_manual TEXT, cfdi_report_id CHAR(32))"
            )
        )
        for record_id, state in ((current_id, "activo"), (cancelled_id, "cancelado")):
            connection.execute(
                text("INSERT INTO expense_reports VALUES (:id, :state, :uuid, NULL)"),
                {
                    "id": record_id.hex,
                    "state": state,
                    "uuid": " " + FISCAL_UUID.lower() + " ",
                },
            )
        session = UniquenessSession(connection)

        async def check():
            await _enforce_expense_cfdi_uniqueness(
                session,
                expense=expense,
                report=None,
                allow_shared=False,
                shared_reason=None,
                actor_id=None,
            )

        asyncio.run(check())
        connection.execute(
            text("INSERT INTO expense_reports VALUES (:id, 'activo', :uuid, NULL)"),
            {"id": other_id.hex, "uuid": FISCAL_UUID.lower()},
        )
        with pytest.raises(ExpenseCFDIDuplicateError) as caught:
            asyncio.run(check())
        assert caught.value.duplicate_id == other_id
    engine.dispose()
