"""RQF-056G tests for Informe de Gastos employee beneficiary selection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.responses import RedirectResponse

from devnous.gastos.routes import user_routes


class _QueryParams(dict):
    def getlist(self, key):
        value = self.get(key, [])
        if isinstance(value, list):
            return value
        return [value]


def _authorized_requester():
    return SimpleNamespace(
        id=UUID(next(iter(user_routes._THIRD_PARTY_EMPLOYEE_REQUESTER_IDS))),
        nombre="Alicia",
        correo="azuniga@plataformasports.com",
        rol="empleado",
    )


@pytest.mark.asyncio
async def test_crear_informe_form_shows_employee_beneficiary_selector_for_authorized_user(monkeypatch):
    requester = _authorized_requester()
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana Roman", correo="bibiana@example.com")
    torneo = SimpleNamespace(id=uuid4(), name="Proyecto Demo")

    async def fake_resolve(*_args, **_kwargs):
        return beneficiary

    async def fake_active_empleados(*_args, **_kwargs):
        return [requester, beneficiary]

    async def fake_tournaments(*_args, **_kwargs):
        return [torneo]

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve)
    monkeypatch.setattr(user_routes, "_active_beneficiary_empleados", fake_active_empleados)
    monkeypatch.setattr(user_routes, "fetch_active_tournaments_for_empleado", fake_tournaments)
    monkeypatch.setattr(user_routes, "_active_regional_operator_beneficiaries", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        user_routes,
        "_html_empleado_bank_account_options",
        AsyncMock(return_value='<option value="bank-1" selected>Cuenta gastos</option>'),
    )
    monkeypatch.setattr(user_routes, "_cuenta_etapas_map_for_js", lambda _torneos: {})
    monkeypatch.setattr(user_routes, "_tournament_categories_map_for_js", lambda _torneos: {})

    request = SimpleNamespace(query_params=_QueryParams({"beneficiario_empleado_id": str(beneficiary.id)}))

    html = await user_routes.crear_cuenta_de_gastos_form(
        request=request,
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert 'name="beneficiario_empleado_id"' in html
    assert 'id="beneficiario_empleado_id"' in html
    assert "Empleado beneficiario" in html
    assert "Bibiana Roman" in html
    assert 'name="proveedor_cliente_id"' in html
    assert "Cuenta bancaria del beneficiario" in html
    assert "El responsable" in html and "aprobador" in html

@pytest.mark.asyncio
async def test_crear_informe_form_allows_regional_operator_for_authorized_user(monkeypatch):
    requester = _authorized_requester()
    operator = SimpleNamespace(
        id=uuid4(),
        nombre="Operador Regional Norte",
        banco="Santander",
        cuenta_bancaria="9999000011112222",
        cuenta_clabe="012345678901234567",
        entidad_region="Norte",
    )
    torneo = SimpleNamespace(id=uuid4(), name="Proyecto Demo")

    async def fake_resolve_employee(_session, raw_id, *, default):
        return default

    async def fake_active_empleados(*_args, **_kwargs):
        return [requester]

    async def fake_tournaments(*_args, **_kwargs):
        return [torneo]

    async def fake_resolve_operator(_session, raw_id):
        return operator if str(raw_id) == str(operator.id) else None

    async def fake_active_operators(_session):
        return [operator]

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve_employee)
    monkeypatch.setattr(user_routes, "_active_beneficiary_empleados", fake_active_empleados)
    monkeypatch.setattr(user_routes, "fetch_active_tournaments_for_empleado", fake_tournaments)
    monkeypatch.setattr(user_routes, "_resolve_active_regional_operator_beneficiary", fake_resolve_operator)
    monkeypatch.setattr(user_routes, "_active_regional_operator_beneficiaries", fake_active_operators)
    monkeypatch.setattr(user_routes, "_cuenta_etapas_map_for_js", lambda _torneos: {})
    monkeypatch.setattr(user_routes, "_tournament_categories_map_for_js", lambda _torneos: {})

    request = SimpleNamespace(query_params=_QueryParams({"beneficiario_operador_id": str(operator.id)}))

    html = await user_routes.crear_cuenta_de_gastos_form(
        request=request,
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert 'name="beneficiario_operador_id"' in html
    assert 'id="beneficiario_operador_id_informe"' in html
    assert "Operador regional beneficiario" in html
    assert "Operador Regional Norte" in html
    assert 'name="proveedor_cliente_id"' in html
    assert "Santander" in html
    assert "solicitante y aprobacion siguen siendo" in html


@pytest.mark.asyncio
async def test_crear_informe_submit_preserves_requester_owner_and_selected_regional_operator(monkeypatch):
    requester = _authorized_requester()
    operator = SimpleNamespace(id=uuid4(), nombre="Operador Regional Norte")
    torneo_id = uuid4()
    cuenta_id = uuid4()
    captured = {}

    async def fake_resolve_employee(_session, raw_id, *, default):
        return default

    async def fake_resolve_operator(_session, raw_id):
        return operator if str(raw_id) == str(operator.id) else None

    async def fake_validate(*_args, **_kwargs):
        return None, "local", torneo_id, "Nacional"

    async def fake_metadata(*_args, **_kwargs):
        return None, ["Varonil"], 2026, "MXN"

    async def fake_create(_session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=cuenta_id), None

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve_employee)
    monkeypatch.setattr(user_routes, "_resolve_active_regional_operator_beneficiary", fake_resolve_operator)
    monkeypatch.setattr(user_routes, "_validate_cuenta_informe_proyecto_fields", fake_validate)
    monkeypatch.setattr(user_routes, "_validate_expense_metadata_for_tournament", fake_metadata)
    monkeypatch.setattr(user_routes, "_create_cuenta_de_gastos_with_informe", fake_create)

    async def async_form():
        return {
            "nombre": "Informe operador regional",
            "tipo_cuenta": "local",
            "torneo_id": str(torneo_id),
            "fase": "Nacional",
            "categorias": ["Varonil"],
            "edicion": "2026",
            "currency": "MXN",
            "beneficiario_operador_id": str(operator.id),
        }

    response = await user_routes.crear_cuenta_de_gastos_submit(
        request=SimpleNamespace(form=async_form),
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/informes-de-gastos/{cuenta_id}?")
    assert captured["empleado"] is requester
    assert captured["beneficiario_operador"] is operator
    assert captured["beneficiario"] is None


@pytest.mark.asyncio
async def test_crear_informe_submit_rejects_employee_and_regional_operator_together():
    requester = _authorized_requester()
    operator_id = uuid4()
    beneficiary_id = uuid4()
    provider_id = uuid4()

    async def async_form():
        return {
            "beneficiario_empleado_id": str(beneficiary_id),
            "beneficiario_operador_id": str(operator_id),
            "proveedor_cliente_id": str(provider_id),
        }

    response = await user_routes.crear_cuenta_de_gastos_submit(
        request=SimpleNamespace(form=async_form),
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/informes-de-gastos/crear?")
    assert "no%20ambos" in response.headers["location"]



@pytest.mark.asyncio
async def test_crear_informe_form_locks_beneficiary_to_self_for_unauthorized_user(monkeypatch):
    requester = SimpleNamespace(id=uuid4(), nombre="Usuario Normal", correo="normal@example.com", rol="empleado")
    other = SimpleNamespace(id=uuid4(), nombre="Otro Empleado", correo="otro@example.com")
    torneo = SimpleNamespace(id=uuid4(), name="Proyecto Demo")

    async def fake_resolve(*_args, **_kwargs):
        return other

    async def fake_active_empleados(*_args, **_kwargs):
        return [requester]

    async def fake_tournaments(*_args, **_kwargs):
        return [torneo]

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve)
    monkeypatch.setattr(user_routes, "_active_beneficiary_empleados", fake_active_empleados)
    monkeypatch.setattr(user_routes, "fetch_active_tournaments_for_empleado", fake_tournaments)
    monkeypatch.setattr(
        user_routes,
        "_html_empleado_bank_account_options",
        AsyncMock(return_value='<option value="bank-1" selected>Cuenta gastos</option>'),
    )
    monkeypatch.setattr(user_routes, "_cuenta_etapas_map_for_js", lambda _torneos: {})
    monkeypatch.setattr(user_routes, "_tournament_categories_map_for_js", lambda _torneos: {})

    request = SimpleNamespace(query_params=_QueryParams({"beneficiario_empleado_id": str(other.id)}))

    html = await user_routes.crear_cuenta_de_gastos_form(
        request=request,
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert '<input type="hidden" name="beneficiario_empleado_id"' in html
    assert str(requester.id) in html
    assert "Usuario Normal" in html
    assert "Otro Empleado" not in html
    assert "solamente puede solicitar" in html


@pytest.mark.asyncio
async def test_crear_informe_submit_preserves_requester_owner_and_selected_beneficiary(monkeypatch):
    requester = _authorized_requester()
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana Roman", correo="bibiana@example.com")
    provider = SimpleNamespace(id=uuid4(), nombre="Cuenta gastos Bibiana")
    torneo_id = uuid4()
    cuenta_id = uuid4()
    captured = {}

    async def fake_resolve(*_args, **_kwargs):
        return beneficiary

    async def fake_validate(*_args, **_kwargs):
        return None, "local", torneo_id, "Nacional"

    async def fake_metadata(*_args, **_kwargs):
        return None, ["Varonil"], 2026, "MXN"

    async def fake_create(_session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=cuenta_id), None

    async def fake_resolve_bank_account(*_args, **_kwargs):
        return provider

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve)
    monkeypatch.setattr(
        user_routes,
        "_resolve_selected_beneficiary_bank_account",
        fake_resolve_bank_account,
    )
    monkeypatch.setattr(user_routes, "_validate_cuenta_informe_proyecto_fields", fake_validate)
    monkeypatch.setattr(user_routes, "_validate_expense_metadata_for_tournament", fake_metadata)
    monkeypatch.setattr(user_routes, "_create_cuenta_de_gastos_with_informe", fake_create)

    async def async_form():
        return {
            "nombre": "Informe tercero",
            "tipo_cuenta": "local",
            "torneo_id": str(torneo_id),
            "fase": "Nacional",
            "categorias": ["Varonil"],
            "edicion": "2026",
            "currency": "MXN",
            "beneficiario_empleado_id": str(beneficiary.id),
            "proveedor_cliente_id": str(provider.id),
        }

    response = await user_routes.crear_cuenta_de_gastos_submit(
        request=SimpleNamespace(form=async_form),
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/informes-de-gastos/{cuenta_id}?")
    assert captured["empleado"] is requester
    assert captured["beneficiario"] is beneficiary
    assert captured["beneficiario_cuenta_bancaria"] is provider
    assert captured["tipo_cuenta"] == "local"
    assert captured["fase"] == "Nacional"
    assert captured["categorias"] == ["Varonil"]
    assert captured["currency"] == "MXN"


@pytest.mark.asyncio
async def test_crear_informe_submit_rejects_other_beneficiary_for_unauthorized_user(monkeypatch):
    requester = SimpleNamespace(id=uuid4(), nombre="Usuario Normal", correo="normal@example.com", rol="empleado")
    beneficiary = SimpleNamespace(id=uuid4(), nombre="Bibiana Roman", correo="bibiana@example.com")

    async def fake_resolve(*_args, **_kwargs):
        return beneficiary

    monkeypatch.setattr(user_routes, "_resolve_active_beneficiary_empleado", fake_resolve)

    async def async_form():
        return {"beneficiario_empleado_id": str(beneficiary.id)}

    response = await user_routes.crear_cuenta_de_gastos_submit(
        request=SimpleNamespace(form=async_form),
        session=SimpleNamespace(),
        current_empleado=requester,
    )

    assert isinstance(response, RedirectResponse)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/informes-de-gastos/crear?")
    assert "No%20tienes%20permiso" in response.headers["location"]


@pytest.mark.asyncio
async def test_sync_informe_to_enviado_records_requester_actor_not_beneficiary(monkeypatch):
    cuenta = SimpleNamespace(id=uuid4(), empleado_id=uuid4(), beneficiario_empleado_id=uuid4())
    informe = SimpleNamespace(id=uuid4(), estado="borrador", enviado_en=None)
    requester_actor = SimpleNamespace(id=uuid4())
    added = []
    session = SimpleNamespace(add=lambda obj: added.append(obj))

    async def fake_count(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(user_routes, "_count_active_cuenta_expenses", fake_count)

    changed = await user_routes._sync_informe_documento_to_enviado(
        session,
        cuenta=cuenta,
        informe_doc=informe,
        actor=requester_actor,
    )

    assert changed is True
    assert informe.estado == "enviado"
    aprobaciones = [obj for obj in added if obj.__class__.__name__ == "Aprobacion"]
    assert len(aprobaciones) == 1
    assert aprobaciones[0].aprobador_id == requester_actor.id
    assert aprobaciones[0].entidad_id == informe.id
