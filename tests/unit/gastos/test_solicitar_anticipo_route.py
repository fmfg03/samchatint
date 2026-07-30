"""Tests for Solicitar Anticipo flow from Solicitudes de transferencia."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.responses import RedirectResponse

from devnous.gastos.routes import user_routes


def test_bank_account_name_matching_ignores_punctuation_and_accents() -> None:
    assert user_routes._normalize_name_for_similarity(
        "Magnus McCoy de México, S.A. de C.V."
    ) == "magnus mccoy de mexico sa de cv"
    assert user_routes._empleado_proveedor_name_similarity(
        "Magnus McCoy de México, S.A. de C.V.",
        "Magnus McCoy de Mexico SA de CV",
    ) == 1.0


def test_solicitar_anticipo_form_url_preserves_fields() -> None:
    url = user_routes._solicitar_anticipo_form_url(
        error_msg="Proyecto requerido.",
        torneo_id="abc-123",
        concepto_pago="Anticipo viaje",
        monto_solicitado="1500.00",
        budget_concept_id="concept-1",
    )

    assert url.startswith("/gastos-terceros/solicitar-anticipo?")
    assert "error_msg=Proyecto%20requerido." in url
    assert "torneo_id=abc-123" in url
    assert "concepto_pago=Anticipo%20viaje" in url
    assert "monto_solicitado=1500.00" in url
    assert "budget_concept_id=concept-1" in url


@pytest.mark.asyncio
async def test_solicitar_anticipo_submit_creates_informe_and_solicitud(
    monkeypatch,
) -> None:
    cuenta_id = uuid4()
    solicitud_id = uuid4()
    torneo_id = uuid4()
    proveedor_id = uuid4()
    beneficiario_id = uuid4()
    beneficiario = SimpleNamespace(id=beneficiario_id, nombre="Beneficiario B")
    requester = SimpleNamespace(
        id=UUID(next(iter(user_routes._THIRD_PARTY_EMPLOYEE_REQUESTER_IDS))),
        nombre="Alicia",
        correo="azuniga@plataformasports.com",
    )

    async def fake_validate(*_args, **kwargs):
        return None, "local", torneo_id, None

    create_kwargs = {}

    async def fake_create(_session, **kwargs):
        create_kwargs.update(kwargs)
        return SimpleNamespace(id=cuenta_id), None

    matched_for = {}

    async def fake_matches(*_args, **kwargs):
        matched_for["empleado"] = kwargs["empleado"]
        return [(SimpleNamespace(id=proveedor_id), 1.0)]

    async def fake_resolve(*_args, **_kwargs):
        return beneficiario

    async def fake_execute(_query):
        class Result:
            def scalar_one_or_none(self):
                return SimpleNamespace(id=proveedor_id, activo=True)

        return Result()

    captured = {}

    async def fake_create_solicitud(session, payload):
        captured["payload"] = payload
        return SimpleNamespace(id=solicitud_id)

    monkeypatch.setattr(
        user_routes,
        "_validate_cuenta_informe_proyecto_fields",
        fake_validate,
    )
    monkeypatch.setattr(
        user_routes,
        "_create_cuenta_de_gastos_with_informe",
        fake_create,
    )
    monkeypatch.setattr(
        user_routes,
        "_get_matching_bank_accounts_for_empleado",
        fake_matches,
    )
    monkeypatch.setattr(
        user_routes,
        "_resolve_active_beneficiary_empleado",
        fake_resolve,
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_personal_document",
        fake_create_solicitud,
    )

    async def async_form():
        return {
            "tipo_cuenta": "local",
            "torneo_id": str(torneo_id),
            "fase": "",
            "monto_solicitado": "2500.50",
            "concepto_pago": "Anticipo operativo",
            "fecha_pago": "2026-06-10",
            "proveedor_cliente_id": str(proveedor_id),
            "beneficiario_empleado_id": str(beneficiario_id),
        }

    request = SimpleNamespace(form=async_form)

    session = SimpleNamespace(execute=fake_execute)

    response = await user_routes.solicitar_anticipo_submit(
        request=request,
        session=session,
        current_empleado=requester,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/documentos/{solicitud_id}?")
    assert "success=solicitud_creada" in response.headers["location"]
    assert "Enviar%20para%20autorizaci%C3%B3n" in response.headers["location"]
    assert captured["payload"].cuenta_id == cuenta_id
    assert captured["payload"].monto_solicitado == 2500.50
    assert captured["payload"].budget_concept_id is None
    assert create_kwargs["empleado"] is not beneficiario
    assert create_kwargs["beneficiario"] is beneficiario
    assert matched_for["empleado"] is beneficiario


@pytest.mark.asyncio
async def test_solicitar_anticipo_submit_returns_to_form_on_project_error(
    monkeypatch,
) -> None:
    async def fake_validate(*_args, **kwargs):
        return "Debe seleccionar Torneo/Proyecto.", None, None, None

    monkeypatch.setattr(
        user_routes,
        "_validate_cuenta_informe_proyecto_fields",
        fake_validate,
    )

    async def async_form():
        return {
            "tipo_cuenta": "local",
            "torneo_id": "",
            "fase": "",
            "monto_solicitado": "100",
            "concepto_pago": "Test",
            "fecha_pago": "",
            "proveedor_cliente_id": str(uuid4()),
        }

    request = SimpleNamespace(form=async_form)

    response = await user_routes.solicitar_anticipo_submit(
        request=request,
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(id=uuid4()),
    )

    assert isinstance(response, RedirectResponse)
    assert response.headers["location"].startswith(
        "/gastos-terceros/solicitar-anticipo?"
    )
    assert "Debe%20seleccionar%20Torneo" in response.headers["location"]


@pytest.mark.asyncio
async def test_solicitar_anticipo_submit_returns_to_form_on_invalid_monto(
    monkeypatch,
) -> None:
    torneo_id = uuid4()
    proveedor_id = uuid4()

    async def fake_validate(*_args, **kwargs):
        return None, "local", torneo_id, None

    async def fake_matches(*_args, **_kwargs):
        return [(SimpleNamespace(id=proveedor_id), 1.0)]

    async def fake_execute(_query):
        class Result:
            def scalar_one_or_none(self):
                return SimpleNamespace(id=proveedor_id, activo=True)

        return Result()

    monkeypatch.setattr(
        user_routes,
        "_validate_cuenta_informe_proyecto_fields",
        fake_validate,
    )
    monkeypatch.setattr(
        user_routes,
        "_get_matching_bank_accounts_for_empleado",
        fake_matches,
    )

    async def async_form():
        return {
            "tipo_cuenta": "local",
            "torneo_id": str(torneo_id),
            "fase": "",
            "monto_solicitado": "0",
            "concepto_pago": "Test",
            "fecha_pago": "",
            "proveedor_cliente_id": str(proveedor_id),
        }

    request = SimpleNamespace(form=async_form)

    response = await user_routes.solicitar_anticipo_submit(
        request=request,
        session=SimpleNamespace(execute=fake_execute),
        current_empleado=SimpleNamespace(id=uuid4()),
    )

    assert isinstance(response, RedirectResponse)
    assert response.headers["location"].startswith(
        "/gastos-terceros/solicitar-anticipo?"
    )
    assert "error_msg=" in response.headers["location"]


@pytest.mark.asyncio
async def test_solicitar_anticipo_submit_rolls_back_on_unexpected_solicitud_error(
    monkeypatch,
) -> None:
    cuenta_id = uuid4()
    torneo_id = uuid4()
    proveedor_id = uuid4()

    async def fake_validate(*_args, **_kwargs):
        return None, "local", torneo_id, None

    async def fake_create(_session, **_kwargs):
        return SimpleNamespace(id=cuenta_id), None

    async def fake_matches(*_args, **_kwargs):
        return [(SimpleNamespace(id=proveedor_id), 1.0)]

    async def fake_execute(_query):
        class Result:
            def scalar_one_or_none(self):
                return SimpleNamespace(id=proveedor_id, activo=True)

        return Result()

    monkeypatch.setattr(
        user_routes,
        "_validate_cuenta_informe_proyecto_fields",
        fake_validate,
    )
    monkeypatch.setattr(
        user_routes,
        "_create_cuenta_de_gastos_with_informe",
        fake_create,
    )
    monkeypatch.setattr(
        user_routes,
        "_get_matching_bank_accounts_for_empleado",
        fake_matches,
    )
    monkeypatch.setattr(
        user_routes,
        "create_solicitud_personal_document",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    async def async_form():
        return {
            "tipo_cuenta": "local",
            "torneo_id": str(torneo_id),
            "fase": "",
            "monto_solicitado": "2500.50",
            "concepto_pago": "Anticipo operativo",
            "fecha_pago": "2026-06-10",
            "proveedor_cliente_id": str(proveedor_id),
        }

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=fake_execute)
    request = SimpleNamespace(form=async_form)

    response = await user_routes.solicitar_anticipo_submit(
        request=request,
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/informes-de-gastos/{cuenta_id}/nueva-solicitud"
        "?error=unexpected_anticipo_create&error_msg="
        "Ocurri%C3%B3%20un%20error%20al%20procesar%20la%20solicitud.%20"
        "Revise%20los%20datos%20capturados%20e%20intente%20nuevamente."
    )
    session.rollback.assert_awaited_once()


def test_solicitar_anticipo_source_separates_employee_beneficiary_from_bank_account() -> None:
    source = user_routes.solicitar_anticipo_form.__code__.co_consts
    rendered_literals = "\n".join(str(value) for value in source if isinstance(value, str))

    assert "Paso 2" in rendered_literals
    assert "Cuenta bancaria del beneficiario" in rendered_literals
    assert "La cuenta debe pertenecer al empleado beneficiario" in rendered_literals


class _QueryParams(dict):
    def getlist(self, key):
        value = self.get(key, [])
        if isinstance(value, list):
            return value
        return [value]


def test_can_request_for_other_employee_matches_juan_pablo_by_display_name() -> None:
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo L\u00f3pez",
        correo="juan.pablo@example.com",
        rol="empleado",
        permissions=set(),
    )

    assert user_routes._can_request_for_other_employee(requester) is True


@pytest.mark.asyncio
async def test_solicitar_anticipo_form_shows_full_active_employee_list_for_authorized_user(monkeypatch):
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo L\u00f3pez",
        correo="juan.pablo@example.com",
        rol="empleado",
        permissions=set(),
    )
    bibiana = SimpleNamespace(id=uuid4(), nombre="Bibiana Roman", correo="bibiana@example.com")
    carlos = SimpleNamespace(id=uuid4(), nombre="Carlos Lozano", correo="carlos@example.com")
    torneo = SimpleNamespace(id=uuid4(), name="Proyecto Demo")
    bank_called = {}

    async def fake_resolve(_session, raw_id, *, default):
        return bibiana if str(raw_id) == str(bibiana.id) else default

    async def fake_active_empleados(_session, current_empleado):
        return [requester, bibiana, carlos]

    async def fake_tournaments(*_args, **_kwargs):
        return [torneo]

    async def fake_bank_options(_session, empleado, *, selected_id=None):
        bank_called["empleado"] = empleado
        bank_called["selected_id"] = selected_id
        return '<option value="bank-1">Cuenta beneficiario</option>'

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve)
    monkeypatch.setattr(user_routes, "_active_beneficiary_empleados", fake_active_empleados)
    monkeypatch.setattr(user_routes, "fetch_active_tournaments_for_empleado", fake_tournaments)
    monkeypatch.setattr(user_routes, "_html_empleado_bank_account_options", fake_bank_options)
    monkeypatch.setattr(user_routes, "_cuenta_etapas_map_for_js", lambda _torneos: {})
    monkeypatch.setattr(user_routes, "_tournament_categories_map_for_js", lambda _torneos: {})

    html = await user_routes.solicitar_anticipo_form(
        request=SimpleNamespace(
            query_params=_QueryParams({"beneficiario_empleado_id": str(bibiana.id)})
        ),
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert 'name="beneficiario_empleado_id"' in html
    assert 'id="beneficiario_empleado_id"' in html
    assert "Empleado beneficiario" in html
    assert "Juan Pablo L\u00f3pez" in html
    assert "Bibiana Roman" in html
    assert "Carlos Lozano" in html
    assert f'<option value="{bibiana.id}" selected>Bibiana Roman</option>' in html
    assert "Cuenta bancaria del beneficiario" in html
    assert bank_called["empleado"] is bibiana


@pytest.mark.asyncio
async def test_solicitar_anticipo_form_locks_beneficiary_to_self_for_unauthorized_user(monkeypatch):
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Usuario Normal",
        correo="normal@example.com",
        rol="empleado",
        permissions=set(),
    )
    other = SimpleNamespace(id=uuid4(), nombre="Otro Empleado", correo="otro@example.com")
    torneo = SimpleNamespace(id=uuid4(), name="Proyecto Demo")

    async def fake_resolve(*_args, **_kwargs):
        return other

    async def fake_active_empleados(_session, current_empleado):
        return [current_empleado]

    async def fake_tournaments(*_args, **_kwargs):
        return [torneo]

    async def fake_bank_options(_session, empleado, *, selected_id=None):
        return '<option value="bank-self">Cuenta propia</option>'

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve)
    monkeypatch.setattr(user_routes, "_active_beneficiary_empleados", fake_active_empleados)
    monkeypatch.setattr(user_routes, "fetch_active_tournaments_for_empleado", fake_tournaments)
    monkeypatch.setattr(user_routes, "_html_empleado_bank_account_options", fake_bank_options)
    monkeypatch.setattr(user_routes, "_cuenta_etapas_map_for_js", lambda _torneos: {})
    monkeypatch.setattr(user_routes, "_tournament_categories_map_for_js", lambda _torneos: {})

    html = await user_routes.solicitar_anticipo_form(
        request=SimpleNamespace(
            query_params=_QueryParams({"beneficiario_empleado_id": str(other.id)})
        ),
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert '<input type="hidden" name="beneficiario_empleado_id"' in html
    assert str(requester.id) in html
    assert "Usuario Normal" in html
    assert "Otro Empleado" not in html
    assert "solamente puede solicitar" in html


@pytest.mark.asyncio
async def test_employee_bank_accounts_api_uses_selected_beneficiary_for_authorized_requester(monkeypatch):
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Juan Pablo L\u00f3pez",
        correo="juan.pablo@example.com",
        rol="empleado",
        permissions=set(),
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana Roman", correo="bibiana@example.com")
    account = SimpleNamespace(
        id=uuid4(),
        nombre="Bibiana Roman",
        banco="Santander",
        cuenta_bancaria="1234567890",
        cuenta_clabe="012345678901234567",
    )
    called = {}

    class Result:
        def scalar_one_or_none(self):
            return beneficiary

    async def fake_execute(_query):
        return Result()

    async def fake_matches(session, empleado):
        called["empleado"] = empleado
        return [(account, 1.0)]

    monkeypatch.setattr(user_routes, "_get_matching_bank_accounts_for_empleado", fake_matches)

    response = await user_routes.employee_bank_accounts_for_beneficiary(
        beneficiario_id=beneficiary.id,
        session=SimpleNamespace(execute=fake_execute),
        current_empleado=requester,
    )

    assert response.status_code == 200
    assert called["empleado"] is beneficiary
    body = response.body.decode("utf-8")
    assert "Bibiana Roman" in body
    assert "Santander" in body
    assert str(account.id) in body


@pytest.mark.asyncio
async def test_employee_bank_accounts_api_rejects_unpermitted_other_beneficiary(monkeypatch):
    requester = SimpleNamespace(
        id=uuid4(),
        nombre="Usuario Normal",
        correo="normal@example.com",
        rol="empleado",
        permissions=set(),
    )
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana Roman", correo="bibiana@example.com")

    class Result:
        def scalar_one_or_none(self):
            return beneficiary

    async def fake_execute(_query):
        return Result()

    with pytest.raises(user_routes.HTTPException) as exc:
        await user_routes.employee_bank_accounts_for_beneficiary(
            beneficiario_id=beneficiary.id,
            session=SimpleNamespace(execute=fake_execute),
            current_empleado=requester,
        )

    assert exc.value.status_code == 403
