from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.models import Aprobacion, ProveedorCliente
from devnous.gastos.services import beneficiary_onboarding_service as svc


def test_final_beneficiary_reviewer_is_email_scoped() -> None:
    assert (
        svc.is_final_beneficiary_reviewer(
            SimpleNamespace(correo="jlopez@plataformasports.com")
        )
        is True
    )
    assert (
        svc.is_final_beneficiary_reviewer(
            SimpleNamespace(correo="otrujillo@plataformasports.com")
        )
        is False
    )


def test_normalize_clabe_rejects_invalid_length() -> None:
    with pytest.raises(svc.BeneficiaryOnboardingError) as exc:
        svc.normalize_clabe("123")

    assert exc.value.code == "invalid_clabe"


@pytest.mark.asyncio
async def test_area_approval_moves_to_final_review_and_notifies_reviewers(monkeypatch):
    request = SimpleNamespace(
        id=uuid4(),
        status="pendiente_area",
        area_approver_id=uuid4(),
        target_tipo="participante_torneo",
        nombre="Papa Bimbo",
        banco="BBVA",
        cuenta_clabe="123456789012345678",
        cuenta_bancaria=None,
        rfc=None,
        entidad_region="Bimbo",
        notas=None,
    )
    actor = SimpleNamespace(id=request.area_approver_id)
    notified = []

    monkeypatch.setattr(svc, "_load_onboarding_request", AsyncMock(return_value=request))
    monkeypatch.setattr(
        svc,
        "_final_reviewers",
        AsyncMock(
            return_value=[
                SimpleNamespace(id=uuid4(), nombre="Benjamin", telegram_user_id=1),
                SimpleNamespace(id=uuid4(), nombre="Juan Pablo", telegram_user_id=2),
            ]
        ),
    )

    async def fake_notify(_session, *, empleado, notification_type, header, text):
        notified.append((empleado.nombre, notification_type, header, text))

    monkeypatch.setattr(svc, "_notify_employee", fake_notify)
    added = []
    session = SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await svc.approve_beneficiary_onboarding_area(
        session,
        request_id=request.id,
        actor=actor,
        comment="ok",
    )

    assert result.status == "pendiente_revision_final"
    assert [item[0] for item in notified] == ["Benjamin", "Juan Pablo"]
    assert any(isinstance(obj, Aprobacion) and obj.accion == "aprobar_area" for obj in added)


@pytest.mark.asyncio
async def test_final_approval_creates_provider_registry_entry(monkeypatch):
    request = SimpleNamespace(
        id=uuid4(),
        status="pendiente_revision_final",
        target_tipo="participante_torneo",
        nombre="Papa Bimbo",
        rfc=None,
        banco="BBVA",
        cuenta_clabe="123456789012345678",
        cuenta_bancaria=None,
        entidad_region="Bimbo",
        empleado_id=None,
        requested_by=None,
        requested_by_empleado_id=None,
        created_proveedor_cliente_id=None,
        final_approved_by_empleado_id=None,
        final_decision_comment=None,
        final_decided_at=None,
        actualizado_en=None,
        notas=None,
    )
    actor = SimpleNamespace(
        id=uuid4(),
        correo="bjimenez@plataformasports.com",
    )
    monkeypatch.setattr(svc, "_load_onboarding_request", AsyncMock(return_value=request))
    monkeypatch.setattr(svc, "_ensure_no_active_duplicate", AsyncMock())
    monkeypatch.setattr(svc, "_notify_employee", AsyncMock())
    added = []

    async def fake_flush():
        for obj in added:
            if isinstance(obj, ProveedorCliente) and obj.id is None:
                obj.id = uuid4()

    session = SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(side_effect=fake_flush),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await svc.approve_beneficiary_onboarding_final(
        session,
        request_id=request.id,
        actor=actor,
        comment="palomita",
    )

    providers = [obj for obj in added if isinstance(obj, ProveedorCliente)]
    assert len(providers) == 1
    assert providers[0].tipo == "participante_torneo"
    assert providers[0].nombre == "Papa Bimbo"
    assert result.status == "aprobada_registrada"
    assert result.created_proveedor_cliente_id == providers[0].id
    assert any(isinstance(obj, Aprobacion) and obj.accion == "aprobar_final" for obj in added)
