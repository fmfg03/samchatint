from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.models import (
    Aprobacion,
    BeneficiaryOnboardingAttachment,
    BeneficiaryOnboardingRequest,
    ProveedorCliente,
)
from devnous.gastos.services import beneficiary_onboarding_service as svc


USER_ROUTES_SOURCE = Path("src/devnous/gastos/routes/user_routes.py")


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


def test_onboarding_form_exposes_required_attachment_fields() -> None:
    source = USER_ROUTES_SOURCE.read_text()

    assert 'enctype="multipart/form-data"' in source
    assert 'name="ine_participante"' in source
    assert 'name="ine_tutor"' in source
    assert 'name="credencial_participante"' in source
    assert 'name="caratula_estado_cuenta"' in source
    assert 'name="participant_age_group"' in source
    assert 'name="provider_person_type"' in source
    assert 'name="constancia_situacion_fiscal"' in source
    assert 'name="comprobante_domicilio_fiscal_comercial"' in source
    assert 'name="ine_apoderado_legal"' in source
    assert 'name="ine_titular_constancia"' in source
    assert 'name="ine_colaborador_externo"' in source
    assert 'name="contrato_convenio_plataforma"' in source
    assert 'name="contrato_plataforma"' in source
    assert 'name="expediente_atencion_medica"' in source


def test_onboarding_attachment_download_route_exists() -> None:
    source = USER_ROUTES_SOURCE.read_text()

    assert '"/beneficiarios/altas/{request_id}/adjuntos/{attachment_id}"' in source
    assert "can_access_beneficiary_onboarding_request" in source


def test_onboarding_form_marks_business_optional_documents_as_optional() -> None:
    source = USER_ROUTES_SOURCE.read_text()
    form = source[
        source.index("async def beneficiary_onboarding_new_form(") :
        source.index('@router.post("/beneficiarios/altas/nueva")')
    ]

    for field_name in [
        "comprobante_domicilio_fiscal_comercial",
        "ine_apoderado_legal",
        "ine_titular_constancia",
        "contrato_convenio_plataforma",
        "contrato_plataforma",
        "credencial_participante",
    ]:
        field_block = form[
            form.index(f'for="{field_name}"') :
            form.index("</div>", form.index(f'for="{field_name}"'))
        ]
        assert "Opcional" in field_block
        assert "Requerida" not in field_block
        assert "Requerido" not in field_block


def test_onboarding_create_redirects_on_unexpected_errors() -> None:
    source = USER_ROUTES_SOURCE.read_text()
    route = source[
        source.index("async def beneficiary_onboarding_create(") :
        source.index('@router.get("/beneficiarios/altas"', source.index("async def beneficiary_onboarding_create("))
    ]

    assert "except Exception:" in route
    assert "logger.exception(" in route
    assert "Unexpected beneficiary onboarding create failure" in route
    assert "No se pudo crear la solicitud. Intenta de nuevo o contacta a soporte." in route
    assert '"/beneficiarios/altas/nueva?error_msg="' in route
    assert "status_code=303" in route


def _attachment(category: str) -> svc.BeneficiaryOnboardingAttachmentInput:
    return svc.BeneficiaryOnboardingAttachmentInput(
        categoria=category,
        filename=f"{category}.pdf",
        mime_type="application/pdf",
        raw_bytes=b"pdf-bytes",
    )


def test_provider_moral_requires_only_tax_and_bank_minimum_package() -> None:
    validated = svc.validate_required_attachments(
        target_tipo="proveedor",
        provider_person_type="persona_moral",
        participant_is_minor=None,
        attachments=[
            _attachment("constancia_situacion_fiscal"),
            _attachment("comprobante_domicilio_fiscal_comercial"),
            _attachment("caratula_estado_cuenta"),
            _attachment("ine_apoderado_legal"),
            _attachment("contrato_convenio_plataforma"),
        ],
    )
    assert {item.categoria for item in validated} == {
        "constancia_situacion_fiscal",
        "comprobante_domicilio_fiscal_comercial",
        "caratula_estado_cuenta",
        "ine_apoderado_legal",
        "contrato_convenio_plataforma",
    }

    minimum = svc.validate_required_attachments(
        target_tipo="proveedor",
        provider_person_type="persona_moral",
        participant_is_minor=None,
        attachments=[
            _attachment("constancia_situacion_fiscal"),
            _attachment("caratula_estado_cuenta"),
        ],
    )
    assert {item.categoria for item in minimum} == {
        "constancia_situacion_fiscal",
        "caratula_estado_cuenta",
    }


def test_provider_fisica_requires_only_tax_and_bank_minimum_package() -> None:
    validated = svc.validate_required_attachments(
        target_tipo="proveedor",
        provider_person_type="persona_fisica",
        participant_is_minor=None,
        attachments=[
            _attachment("constancia_situacion_fiscal"),
            _attachment("caratula_estado_cuenta"),
        ],
    )

    assert {item.categoria for item in validated} == {
        "constancia_situacion_fiscal",
        "caratula_estado_cuenta",
    }


def test_provider_requires_person_type() -> None:
    with pytest.raises(svc.BeneficiaryOnboardingError) as exc:
        svc.validate_required_attachments(
            target_tipo="proveedor",
            participant_is_minor=None,
            attachments=[_attachment("caratula_estado_cuenta")],
        )

    assert exc.value.code == "missing_provider_person_type"


def test_operator_requires_ine_and_bank_statement_contract_optional() -> None:
    validated = svc.validate_required_attachments(
        target_tipo="operadores_regionales",
        participant_is_minor=None,
        attachments=[
            _attachment("ine_colaborador_externo"),
            _attachment("caratula_estado_cuenta"),
            _attachment("contrato_plataforma"),
        ],
    )
    assert {item.categoria for item in validated} == {
        "ine_colaborador_externo",
        "caratula_estado_cuenta",
        "contrato_plataforma",
    }

    minimum = svc.validate_required_attachments(
        target_tipo="operadores_regionales",
        participant_is_minor=None,
        attachments=[
            _attachment("ine_colaborador_externo"),
            _attachment("caratula_estado_cuenta"),
        ],
    )
    assert {item.categoria for item in minimum} == {
        "ine_colaborador_externo",
        "caratula_estado_cuenta",
    }


def test_participant_adult_requires_ine_bank_statement_and_case_file_credential_optional() -> None:
    with pytest.raises(svc.BeneficiaryOnboardingError) as exc:
        svc.validate_required_attachments(
            target_tipo="participante_torneo",
            participant_is_minor=False,
            attachments=[_attachment("caratula_estado_cuenta")],
        )

    assert exc.value.code == "missing_required_attachment"
    assert "INE del participante" in exc.value.message

    validated = svc.validate_required_attachments(
        target_tipo="participante_torneo",
        participant_is_minor=False,
        attachments=[
            _attachment("ine_participante"),
            _attachment("credencial_participante"),
            _attachment("caratula_estado_cuenta"),
            _attachment("expediente_atencion_medica"),
        ],
    )
    assert {item.categoria for item in validated} == {
        "ine_participante",
        "credencial_participante",
        "caratula_estado_cuenta",
        "expediente_atencion_medica",
    }

    minimum = svc.validate_required_attachments(
        target_tipo="participante_torneo",
        participant_is_minor=False,
        attachments=[
            _attachment("ine_participante"),
            _attachment("caratula_estado_cuenta"),
            _attachment("expediente_atencion_medica"),
        ],
    )
    assert {item.categoria for item in minimum} == {
        "ine_participante",
        "caratula_estado_cuenta",
        "expediente_atencion_medica",
    }


def test_participant_minor_requires_tutor_bank_statement_and_case_file_credential_optional() -> None:
    with pytest.raises(svc.BeneficiaryOnboardingError) as exc:
        svc.validate_required_attachments(
            target_tipo="participante_torneo",
            participant_is_minor=True,
            attachments=[
                _attachment("ine_tutor"),
                _attachment("caratula_estado_cuenta"),
            ],
        )

    assert exc.value.code == "missing_required_attachment"
    assert "Expediente del caso" in exc.value.message


def test_employee_requires_only_bank_statement() -> None:
    with pytest.raises(svc.BeneficiaryOnboardingError) as exc:
        svc.validate_required_attachments(
            target_tipo="empleado",
            participant_is_minor=None,
            attachments=[],
        )

    assert exc.value.code == "missing_required_attachment"
    assert "Carátula del estado de cuenta" in exc.value.message

    validated = svc.validate_required_attachments(
        target_tipo="empleado",
        participant_is_minor=None,
        attachments=[_attachment("caratula_estado_cuenta")],
    )
    assert [item.categoria for item in validated] == ["caratula_estado_cuenta"]


@pytest.mark.asyncio
async def test_create_request_persists_required_attachments(monkeypatch):
    requester_id = uuid4()
    approver_id = uuid4()
    requester = SimpleNamespace(
        id=requester_id,
        rol="empleado",
        departamento="Operaciones",
        correo="azuniga@plataformasports.com",
        aprobador_id=approver_id,
    )
    monkeypatch.setattr(svc, "_ensure_no_active_duplicate", AsyncMock())
    monkeypatch.setattr(svc, "_notify_employee", AsyncMock())
    added = []

    async def fake_flush():
        for obj in added:
            if isinstance(obj, BeneficiaryOnboardingRequest) and obj.id is None:
                obj.id = uuid4()
            if isinstance(obj, BeneficiaryOnboardingAttachment) and obj.id is None:
                obj.id = uuid4()

    session = SimpleNamespace(
        add=lambda obj: added.append(obj),
        get=AsyncMock(return_value=SimpleNamespace(id=approver_id)),
        flush=AsyncMock(side_effect=fake_flush),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await svc.create_beneficiary_onboarding_request(
        session,
        requester=requester,
        payload=svc.BeneficiaryOnboardingInput(
            target_tipo="participante_torneo",
            nombre="Participante Bimbo",
            cuenta_clabe="123456789012345678",
            participant_is_minor=True,
        ),
        attachments=[
            _attachment("ine_tutor"),
            _attachment("credencial_participante"),
            _attachment("caratula_estado_cuenta"),
            _attachment("expediente_atencion_medica"),
        ],
    )

    persisted = [
        item for item in added if isinstance(item, BeneficiaryOnboardingAttachment)
    ]
    assert result.participant_is_minor is True
    assert {item.categoria for item in persisted} == {
        "ine_tutor",
        "credencial_participante",
        "caratula_estado_cuenta",
        "expediente_atencion_medica",
    }


def test_any_active_employee_can_request_beneficiary_onboarding() -> None:
    assert svc.can_create_beneficiary_onboarding_request(
        SimpleNamespace(id=uuid4(), rol="empleado", departamento="Administración", activo=True)
    )
    assert not svc.can_create_beneficiary_onboarding_request(
        SimpleNamespace(id=uuid4(), rol="empleado", departamento="Operaciones", activo=False)
    )


@pytest.mark.asyncio
async def test_create_request_loads_area_approver_from_persisted_employee(monkeypatch):
    requester_id = uuid4()
    approver_id = uuid4()
    requester = SimpleNamespace(
        id=requester_id,
        rol="empleado",
        departamento="Administración",
        correo="usuario@plataformasports.com",
        activo=True,
        aprobador_id=None,
    )
    persisted_requester = SimpleNamespace(id=requester_id, aprobador_id=approver_id)
    approver = SimpleNamespace(id=approver_id)
    monkeypatch.setattr(svc, "_ensure_no_active_duplicate", AsyncMock())
    monkeypatch.setattr(svc, "_notify_employee", AsyncMock())
    added = []

    async def fake_flush():
        for obj in added:
            if isinstance(obj, BeneficiaryOnboardingRequest) and obj.id is None:
                obj.id = uuid4()
            if isinstance(obj, BeneficiaryOnboardingAttachment) and obj.id is None:
                obj.id = uuid4()

    session = SimpleNamespace(
        add=lambda obj: added.append(obj),
        get=AsyncMock(side_effect=[persisted_requester, approver]),
        flush=AsyncMock(side_effect=fake_flush),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await svc.create_beneficiary_onboarding_request(
        session,
        requester=requester,
        payload=svc.BeneficiaryOnboardingInput(
            target_tipo="empleado",
            nombre="Empleado con cuenta",
            cuenta_clabe="123456789012345678",
        ),
        attachments=[_attachment("caratula_estado_cuenta")],
    )

    assert result.area_approver_id == approver_id
    assert session.get.await_count == 2


@pytest.mark.asyncio
async def test_create_request_still_fails_when_persisted_employee_has_no_area_approver():
    requester_id = uuid4()
    requester = SimpleNamespace(
        id=requester_id,
        rol="empleado",
        departamento="Administración",
        correo="usuario@plataformasports.com",
        activo=True,
        aprobador_id=None,
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=requester_id, aprobador_id=None))
    )

    with pytest.raises(svc.BeneficiaryOnboardingError) as exc:
        await svc.create_beneficiary_onboarding_request(
            session,
            requester=requester,
            payload=svc.BeneficiaryOnboardingInput(
                target_tipo="empleado",
                nombre="Empleado sin aprobador",
                cuenta_clabe="123456789012345678",
            ),
            attachments=[_attachment("caratula_estado_cuenta")],
        )

    assert exc.value.code == "missing_area_approver"


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
