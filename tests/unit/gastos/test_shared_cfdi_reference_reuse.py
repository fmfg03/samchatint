from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services.documento_service import SolicitudValidationError


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, values):
        self.values = iter(values)

    async def execute(self, *_args, **_kwargs):
        return _Result(next(self.values))


def _employee(*, employee_id=None, role="operaciones"):
    return SimpleNamespace(id=employee_id or uuid4(), rol=role)


@pytest.mark.asyncio
async def test_shared_cfdi_reference_reuses_owner_document_uuid() -> None:
    actor = _employee()
    previous = SimpleNamespace(
        empleado_id=actor.id,
        cfdi_uuid_manual="ABCD1234-1111-2222-3333-444444444444",
        cfdi_report_id=None,
    )

    uuid = await user_routes._resolve_shared_cfdi_reference(
        _Session([previous]),
        current_empleado=actor,
        referencia="s-26000213",
    )

    assert uuid == "ABCD1234-1111-2222-3333-444444444444"


@pytest.mark.asyncio
async def test_shared_cfdi_reference_falls_back_to_canonical_report_uuid() -> None:
    actor = _employee()
    report_id = uuid4()
    previous = SimpleNamespace(
        empleado_id=actor.id,
        cfdi_uuid_manual=None,
        cfdi_report_id=report_id,
    )

    uuid = await user_routes._resolve_shared_cfdi_reference(
        _Session([previous, "AAAA0000-1111-2222-3333-444444444444"]),
        current_empleado=actor,
        referencia="S-26000213",
    )

    assert uuid == "AAAA0000-1111-2222-3333-444444444444"


@pytest.mark.asyncio
async def test_shared_cfdi_reference_rejects_unknown_or_unauthorized_documents() -> None:
    actor = _employee()
    with pytest.raises(SolicitudValidationError) as missing:
        await user_routes._resolve_shared_cfdi_reference(
            _Session([None]),
            current_empleado=actor,
            referencia="S-404",
        )
    assert missing.value.code == "shared_cfdi_reference_not_found"

    previous = SimpleNamespace(
        empleado_id=uuid4(),
        cfdi_uuid_manual="ABCD1234-1111-2222-3333-444444444444",
        cfdi_report_id=None,
    )
    with pytest.raises(SolicitudValidationError) as forbidden:
        await user_routes._resolve_shared_cfdi_reference(
            _Session([previous]),
            current_empleado=actor,
            referencia="S-26000213",
        )
    assert forbidden.value.code == "shared_cfdi_reference_forbidden"


@pytest.mark.asyncio
async def test_shared_cfdi_reference_allows_finance_and_rejects_missing_cfdi() -> None:
    finance = _employee(role="finanzas")
    previous = SimpleNamespace(
        empleado_id=uuid4(),
        cfdi_uuid_manual=None,
        cfdi_report_id=None,
    )
    with pytest.raises(SolicitudValidationError) as raised:
        await user_routes._resolve_shared_cfdi_reference(
            _Session([previous]),
            current_empleado=finance,
            referencia="S-26000213",
        )
    assert raised.value.code == "shared_cfdi_reference_without_cfdi"


def test_shared_cfdi_reference_control_explains_safe_reuse() -> None:
    html = user_routes.render_shared_cfdi_reference_input()

    assert 'name="referencia_factura_compartida"' in html
    assert "no vuelva a cargar PDF/XML" in html
