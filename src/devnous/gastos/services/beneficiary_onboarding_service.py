"""Controlled onboarding workflow for beneficiary/payment destination registry."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Aprobacion,
    BeneficiaryOnboardingAttachment,
    BeneficiaryOnboardingRequest,
    Empleado,
    ProveedorCliente,
)
from ..utils.receipt_bytes import MAX_SOLICITUD_ATTACHMENT_BYTES
from .telegram_outbox_service import deliver_telegram_notification


VALID_BENEFICIARY_TARGET_TYPES = {
    "proveedor",
    "empleado",
    "operadores_regionales",
    "participante_torneo",
}

BENEFICIARY_TARGET_TYPE_LABELS = {
    "proveedor": "Proveedor",
    "empleado": "Empleado",
    "operadores_regionales": "Operador Regional",
    "participante_torneo": "Participante de Torneos",
}

VALID_PROVIDER_PERSON_TYPES = {
    "persona_moral",
    "persona_fisica",
}

PROVIDER_PERSON_TYPE_LABELS = {
    "persona_moral": "Persona Moral",
    "persona_fisica": "Persona Física",
}

BENEFICIARY_ATTACHMENT_LABELS = {
    "constancia_situacion_fiscal": "Constancia de Situación Fiscal",
    "comprobante_domicilio_fiscal_comercial": (
        "Comprobante de Domicilio Fiscal y Comercial"
    ),
    "caratula_estado_cuenta": "Carátula del estado de cuenta",
    "ine_apoderado_legal": "INE del apoderado legal",
    "contrato_convenio_plataforma": "Contrato o convenio con Plataforma Sports",
    "ine_titular_constancia": "INE del titular de la constancia fiscal",
    "ine_colaborador_externo": "INE del colaborador externo",
    "contrato_plataforma": "Contrato con Plataforma Sports",
    "ine_participante": "INE del participante",
    "ine_tutor": "INE del tutor",
    "credencial_participante": "Credencial del torneo o escolar",
    "expediente_atencion_medica": "Expediente del caso de atención médica",
}

VALID_BENEFICIARY_ATTACHMENT_CATEGORIES = set(BENEFICIARY_ATTACHMENT_LABELS)

ALLOWED_BENEFICIARY_ATTACHMENT_MIME_PREFIXES = ("image/",)
ALLOWED_BENEFICIARY_ATTACHMENT_MIME_TYPES = {
    "application/pdf",
}

FINAL_REVIEWER_EMAILS = {
    "bjimenez@plataformasports.com",
    "jlopez@plataformasports.com",
}


class BeneficiaryOnboardingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class BeneficiaryOnboardingInput:
    target_tipo: str
    nombre: str
    rfc: Optional[str] = None
    banco: Optional[str] = None
    cuenta_clabe: Optional[str] = None
    cuenta_bancaria: Optional[str] = None
    entidad_region: Optional[str] = None
    empleado_id: Optional[UUID] = None
    torneo_id: Optional[UUID] = None
    provider_person_type: Optional[str] = None
    participant_is_minor: Optional[bool] = None
    notas: Optional[str] = None


@dataclass(slots=True)
class BeneficiaryOnboardingAttachmentInput:
    categoria: str
    filename: str
    mime_type: str
    raw_bytes: bytes


def normalize_beneficiary_target_type(value: str) -> str:
    target = (value or "").strip().lower()
    if target not in VALID_BENEFICIARY_TARGET_TYPES:
        raise BeneficiaryOnboardingError("invalid_type", "Tipo de alta inválido.")
    return target


def normalize_provider_person_type(
    target_tipo: str, value: Optional[str]
) -> Optional[str]:
    clean = (value or "").strip().lower()
    if target_tipo != "proveedor":
        return None
    if clean not in VALID_PROVIDER_PERSON_TYPES:
        raise BeneficiaryOnboardingError(
            "missing_provider_person_type",
            "Indique si el proveedor es Persona Moral o Persona Física.",
        )
    return clean


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    clean = (value or "").strip()
    return clean or None


def normalize_clabe(value: Optional[str]) -> Optional[str]:
    clean = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not clean:
        return None
    if len(clean) != 18:
        raise BeneficiaryOnboardingError(
            "invalid_clabe",
            "La cuenta CLABE debe tener exactamente 18 dígitos.",
        )
    return clean


def normalize_beneficiary_attachment_category(value: str) -> str:
    category = (value or "").strip().lower()
    if category not in VALID_BENEFICIARY_ATTACHMENT_CATEGORIES:
        raise BeneficiaryOnboardingError(
            "invalid_attachment_category",
            "Tipo de archivo de alta inválido.",
        )
    return category


def required_attachment_categories(
    *,
    target_tipo: str,
    participant_is_minor: Optional[bool],
    provider_person_type: Optional[str] = None,
) -> set[str]:
    if target_tipo == "proveedor":
        person_type = normalize_provider_person_type(
            target_tipo,
            provider_person_type,
        )
        required = {
            "constancia_situacion_fiscal",
            "comprobante_domicilio_fiscal_comercial",
            "caratula_estado_cuenta",
            "contrato_convenio_plataforma",
        }
        if person_type == "persona_moral":
            required.add("ine_apoderado_legal")
        else:
            required.add("ine_titular_constancia")
        return required
    if target_tipo == "operadores_regionales":
        return {
            "ine_colaborador_externo",
            "caratula_estado_cuenta",
            "contrato_plataforma",
        }
    if target_tipo == "participante_torneo":
        if participant_is_minor is None:
            raise BeneficiaryOnboardingError(
                "missing_participant_age",
                "Indique si el participante es mayor o menor de edad.",
            )
        required = {
            "credencial_participante",
            "caratula_estado_cuenta",
            "expediente_atencion_medica",
        }
        if participant_is_minor:
            required.add("ine_tutor")
        else:
            required.add("ine_participante")
        return required
    return {"caratula_estado_cuenta"}


def _validate_attachment_input(
    attachment: BeneficiaryOnboardingAttachmentInput,
) -> BeneficiaryOnboardingAttachmentInput:
    category = normalize_beneficiary_attachment_category(attachment.categoria)
    filename = normalize_optional_text(attachment.filename) or "adjunto"
    raw = attachment.raw_bytes or b""
    if not raw:
        raise BeneficiaryOnboardingError(
            "empty_attachment",
            f"El archivo {BENEFICIARY_ATTACHMENT_LABELS[category]} está vacío.",
        )
    if len(raw) > MAX_SOLICITUD_ATTACHMENT_BYTES:
        raise BeneficiaryOnboardingError(
            "attachment_too_large",
            "Cada archivo debe pesar máximo 15 MB.",
        )
    mime_type = (attachment.mime_type or "").split(";", 1)[0].strip().lower()
    if not mime_type:
        mime_type = "application/octet-stream"
    is_allowed = mime_type in ALLOWED_BENEFICIARY_ATTACHMENT_MIME_TYPES or any(
        mime_type.startswith(prefix)
        for prefix in ALLOWED_BENEFICIARY_ATTACHMENT_MIME_PREFIXES
    )
    if not is_allowed:
        raise BeneficiaryOnboardingError(
            "invalid_attachment_type",
            "Los documentos de alta deben ser PDF o imagen.",
        )
    return BeneficiaryOnboardingAttachmentInput(
        categoria=category,
        filename=filename,
        mime_type=mime_type,
        raw_bytes=raw,
    )


def validate_required_attachments(
    *,
    target_tipo: str,
    participant_is_minor: Optional[bool],
    provider_person_type: Optional[str] = None,
    attachments: list[BeneficiaryOnboardingAttachmentInput],
) -> list[BeneficiaryOnboardingAttachmentInput]:
    validated = [_validate_attachment_input(item) for item in attachments]
    present = {item.categoria for item in validated}
    required = required_attachment_categories(
        target_tipo=target_tipo,
        provider_person_type=provider_person_type,
        participant_is_minor=participant_is_minor,
    )
    missing = required - present
    if missing:
        labels = ", ".join(
            BENEFICIARY_ATTACHMENT_LABELS[category]
            for category in sorted(missing)
        )
        raise BeneficiaryOnboardingError(
            "missing_required_attachment",
            f"Faltan documentos obligatorios: {labels}.",
        )
    return validated


def is_final_beneficiary_reviewer(empleado: Empleado) -> bool:
    email = (getattr(empleado, "correo", "") or "").strip().lower()
    return email in FINAL_REVIEWER_EMAILS


def can_create_beneficiary_onboarding_request(empleado: Empleado) -> bool:
    if empleado is None:
        return False
    if getattr(empleado, "activo", True) is False:
        return False
    return bool(getattr(empleado, "id", None))


async def _resolve_area_approver_id(
    session: AsyncSession, requester: Empleado
) -> Optional[UUID]:
    approver_id = getattr(requester, "aprobador_id", None)
    if approver_id:
        return approver_id
    requester_id = getattr(requester, "id", None)
    if requester_id is None:
        return None
    persisted_requester = await session.get(Empleado, requester_id)
    return getattr(persisted_requester, "aprobador_id", None) if persisted_requester else None


async def _load_onboarding_request(
    session: AsyncSession, request_id: UUID
) -> Optional[BeneficiaryOnboardingRequest]:
    result = await session.execute(
        select(BeneficiaryOnboardingRequest)
        .options(
            selectinload(BeneficiaryOnboardingRequest.requested_by),
            selectinload(BeneficiaryOnboardingRequest.area_approver),
            selectinload(BeneficiaryOnboardingRequest.final_approved_by),
            selectinload(BeneficiaryOnboardingRequest.created_proveedor_cliente),
            selectinload(BeneficiaryOnboardingRequest.attachments),
        )
        .where(BeneficiaryOnboardingRequest.id == request_id)
    )
    return result.scalar_one_or_none()


async def list_beneficiary_onboarding_requests(
    session: AsyncSession,
    *,
    actor: Empleado,
    scope: str,
) -> list[BeneficiaryOnboardingRequest]:
    stmt = (
        select(BeneficiaryOnboardingRequest)
        .options(
            selectinload(BeneficiaryOnboardingRequest.requested_by),
            selectinload(BeneficiaryOnboardingRequest.area_approver),
            selectinload(BeneficiaryOnboardingRequest.created_proveedor_cliente),
            selectinload(BeneficiaryOnboardingRequest.attachments),
        )
        .order_by(BeneficiaryOnboardingRequest.creado_en.desc())
        .limit(200)
    )
    if scope == "mine":
        stmt = stmt.where(
            BeneficiaryOnboardingRequest.requested_by_empleado_id == actor.id
        )
    elif scope == "area":
        stmt = stmt.where(
            BeneficiaryOnboardingRequest.area_approver_id == actor.id,
            BeneficiaryOnboardingRequest.status == "pendiente_area",
        )
    elif scope == "final":
        if not is_final_beneficiary_reviewer(actor):
            return []
        stmt = stmt.where(
            BeneficiaryOnboardingRequest.status == "pendiente_revision_final"
        )
    else:
        raise BeneficiaryOnboardingError("invalid_scope", "Bandeja inválida.")
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _ensure_no_active_duplicate(
    session: AsyncSession,
    *,
    target_tipo: str,
    nombre: str,
    rfc: Optional[str],
    cuenta_clabe: Optional[str],
    cuenta_bancaria: Optional[str],
) -> None:
    duplicate_checks = []
    if cuenta_clabe:
        duplicate_checks.append(ProveedorCliente.cuenta_clabe == cuenta_clabe)
    if cuenta_bancaria:
        duplicate_checks.append(ProveedorCliente.cuenta_bancaria == cuenta_bancaria)
    if rfc:
        duplicate_checks.append(
            and_(ProveedorCliente.tipo == target_tipo, func.lower(ProveedorCliente.rfc) == rfc.lower())
        )
    if nombre:
        duplicate_checks.append(
            and_(
                ProveedorCliente.tipo == target_tipo,
                func.lower(func.trim(ProveedorCliente.nombre)) == nombre.lower(),
            )
        )
    if not duplicate_checks:
        return
    result = await session.execute(
        select(ProveedorCliente.id)
        .where(ProveedorCliente.activo.is_(True), or_(*duplicate_checks))
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise BeneficiaryOnboardingError(
            "duplicate_registry",
            "Ya existe un registro activo con esos datos en el padrón.",
        )


async def _final_reviewers(session: AsyncSession) -> list[Empleado]:
    result = await session.execute(
        select(Empleado).where(
            Empleado.activo.is_(True),
            func.lower(Empleado.correo).in_(tuple(FINAL_REVIEWER_EMAILS)),
        )
    )
    return list(result.scalars().all())


def _onboarding_summary(request: BeneficiaryOnboardingRequest) -> str:
    target = BENEFICIARY_TARGET_TYPE_LABELS.get(request.target_tipo, request.target_tipo)
    lines = [
        f"Tipo: {target}",
        f"Nombre: {request.nombre}",
        f"Banco: {request.banco or '-'}",
        f"CLABE: {request.cuenta_clabe or '-'}",
        f"Cuenta: {request.cuenta_bancaria or '-'}",
        f"RFC: {request.rfc or '-'}",
        f"Entidad/Region: {request.entidad_region or '-'}",
    ]
    provider_person_type = getattr(request, "provider_person_type", None)
    if provider_person_type:
        lines.insert(
            1,
            "Subtipo proveedor: "
            + PROVIDER_PERSON_TYPE_LABELS.get(
                provider_person_type, provider_person_type
            ),
        )
    if request.notas:
        lines.append(f"Notas: {request.notas}")
    return "\n".join(lines)


def _create_attachment_model(
    request_id: UUID,
    attachment: BeneficiaryOnboardingAttachmentInput,
) -> BeneficiaryOnboardingAttachment:
    return BeneficiaryOnboardingAttachment(
        request_id=request_id,
        categoria=attachment.categoria,
        ruta_archivo=base64.b64encode(attachment.raw_bytes).decode("ascii"),
        tipo_archivo=attachment.mime_type,
        mime_type=attachment.mime_type,
        nombre_archivo=attachment.filename,
    )


def can_access_beneficiary_onboarding_request(
    actor: Empleado,
    request: BeneficiaryOnboardingRequest,
) -> bool:
    role = (getattr(actor, "rol", "") or "").strip().lower()
    if role in {"finanzas", "admin", "superadmin", "super_admin"}:
        return True
    if is_final_beneficiary_reviewer(actor):
        return True
    actor_id = getattr(actor, "id", None)
    return actor_id in {
        request.requested_by_empleado_id,
        request.area_approver_id,
    }


async def _notify_employee(
    session: AsyncSession,
    *,
    empleado: Optional[Empleado],
    notification_type: str,
    header: str,
    text: str,
) -> None:
    if empleado is None:
        return
    chat_id = (
        int(empleado.telegram_user_id)
        if getattr(empleado, "telegram_user_id", None) is not None
        else None
    )
    await deliver_telegram_notification(
        session,
        notification_type=notification_type,
        header_text=header,
        text=f"{header}\n\n{text}",
        chat_id=chat_id,
        recipient_empleado_id=empleado.id,
    )


async def create_beneficiary_onboarding_request(
    session: AsyncSession,
    *,
    requester: Empleado,
    payload: BeneficiaryOnboardingInput,
    attachments: Optional[list[BeneficiaryOnboardingAttachmentInput]] = None,
) -> BeneficiaryOnboardingRequest:
    if not can_create_beneficiary_onboarding_request(requester):
        raise BeneficiaryOnboardingError(
            "forbidden",
            "No tienes permiso para solicitar altas de beneficiarios.",
        )
    target_tipo = normalize_beneficiary_target_type(payload.target_tipo)
    nombre = normalize_optional_text(payload.nombre)
    if not nombre:
        raise BeneficiaryOnboardingError("missing_name", "El nombre es requerido.")
    cuenta_clabe = normalize_clabe(payload.cuenta_clabe)
    cuenta_bancaria = normalize_optional_text(payload.cuenta_bancaria)
    if not cuenta_clabe and not cuenta_bancaria:
        raise BeneficiaryOnboardingError(
            "missing_account",
            "Debe capturar CLABE o cuenta bancaria.",
        )
    area_approver_id = await _resolve_area_approver_id(session, requester)
    if not area_approver_id:
        raise BeneficiaryOnboardingError(
            "missing_area_approver",
            "El solicitante no tiene aprobador de área configurado.",
        )
    participant_is_minor = (
        bool(payload.participant_is_minor)
        if target_tipo == "participante_torneo"
        else None
    )
    provider_person_type = normalize_provider_person_type(
        target_tipo, payload.provider_person_type
    )
    validated_attachments = validate_required_attachments(
        target_tipo=target_tipo,
        provider_person_type=provider_person_type,
        participant_is_minor=participant_is_minor,
        attachments=list(attachments or []),
    )
    await _ensure_no_active_duplicate(
        session,
        target_tipo=target_tipo,
        nombre=nombre,
        rfc=normalize_optional_text(payload.rfc),
        cuenta_clabe=cuenta_clabe,
        cuenta_bancaria=cuenta_bancaria,
    )
    request = BeneficiaryOnboardingRequest(
        requested_by_empleado_id=requester.id,
        area_approver_id=area_approver_id,
        target_tipo=target_tipo,
        nombre=nombre,
        rfc=normalize_optional_text(payload.rfc),
        banco=normalize_optional_text(payload.banco),
        cuenta_clabe=cuenta_clabe,
        cuenta_bancaria=cuenta_bancaria,
        entidad_region=normalize_optional_text(payload.entidad_region),
        provider_person_type=provider_person_type,
        empleado_id=payload.empleado_id,
        torneo_id=payload.torneo_id,
        participant_is_minor=participant_is_minor,
        notas=normalize_optional_text(payload.notas),
        status="pendiente_area",
    )
    session.add(request)
    await session.flush()
    for attachment in validated_attachments:
        session.add(_create_attachment_model(request.id, attachment))
    session.add(
        Aprobacion(
            tipo_entidad="beneficiary_onboarding",
            entidad_id=request.id,
            aprobador_id=requester.id,
            accion="enviar",
            comentario="Solicitud de alta enviada a autorizacion de area.",
        )
    )
    await session.flush()
    approver = await session.get(Empleado, area_approver_id)
    await _notify_employee(
        session,
        empleado=approver,
        notification_type="beneficiary_onboarding_area_review",
        header="Nueva alta de beneficiario por autorizar",
        text=_onboarding_summary(request),
    )
    await session.commit()
    await session.refresh(request)
    return request


async def approve_beneficiary_onboarding_area(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: Empleado,
    comment: Optional[str] = None,
) -> BeneficiaryOnboardingRequest:
    request = await _load_onboarding_request(session, request_id)
    if request is None:
        raise BeneficiaryOnboardingError("not_found", "Solicitud no encontrada.")
    if request.status != "pendiente_area":
        raise BeneficiaryOnboardingError("invalid_status", "La solicitud no está pendiente de área.")
    if request.area_approver_id != actor.id:
        raise BeneficiaryOnboardingError("forbidden", "Solo el aprobador de área puede autorizar.")

    request.status = "pendiente_revision_final"
    request.area_decision_comment = normalize_optional_text(comment)
    request.area_decided_at = datetime.utcnow()
    request.actualizado_en = datetime.utcnow()
    session.add(
        Aprobacion(
            tipo_entidad="beneficiary_onboarding",
            entidad_id=request.id,
            aprobador_id=actor.id,
            accion="aprobar_area",
            comentario=request.area_decision_comment,
        )
    )
    await session.flush()
    reviewers = await _final_reviewers(session)
    for reviewer in reviewers:
        await _notify_employee(
            session,
            empleado=reviewer,
            notification_type="beneficiary_onboarding_final_review",
            header="Alta de beneficiario pendiente de palomita final",
            text=_onboarding_summary(request),
        )
    await session.commit()
    await session.refresh(request)
    return request


async def reject_beneficiary_onboarding_area(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: Empleado,
    comment: Optional[str],
) -> BeneficiaryOnboardingRequest:
    request = await _load_onboarding_request(session, request_id)
    if request is None:
        raise BeneficiaryOnboardingError("not_found", "Solicitud no encontrada.")
    if request.status != "pendiente_area":
        raise BeneficiaryOnboardingError("invalid_status", "La solicitud no está pendiente de área.")
    if request.area_approver_id != actor.id:
        raise BeneficiaryOnboardingError("forbidden", "Solo el aprobador de área puede rechazar.")
    request.status = "rechazada_area"
    request.area_decision_comment = normalize_optional_text(comment)
    request.area_decided_at = datetime.utcnow()
    request.actualizado_en = datetime.utcnow()
    session.add(
        Aprobacion(
            tipo_entidad="beneficiary_onboarding",
            entidad_id=request.id,
            aprobador_id=actor.id,
            accion="rechazar_area",
            comentario=request.area_decision_comment,
        )
    )
    if request.requested_by is None and request.requested_by_empleado_id:
        request.requested_by = await session.get(Empleado, request.requested_by_empleado_id)
    await _notify_employee(
        session,
        empleado=request.requested_by,
        notification_type="beneficiary_onboarding_decision",
        header="Alta de beneficiario rechazada por el área",
        text=_onboarding_summary(request),
    )
    await session.commit()
    await session.refresh(request)
    return request


async def approve_beneficiary_onboarding_final(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: Empleado,
    comment: Optional[str] = None,
) -> BeneficiaryOnboardingRequest:
    request = await _load_onboarding_request(session, request_id)
    if request is None:
        raise BeneficiaryOnboardingError("not_found", "Solicitud no encontrada.")
    if request.status != "pendiente_revision_final":
        raise BeneficiaryOnboardingError("invalid_status", "La solicitud no está pendiente de revisión final.")
    if not is_final_beneficiary_reviewer(actor):
        raise BeneficiaryOnboardingError("forbidden", "No tienes permiso para dar la palomita final.")
    await _ensure_no_active_duplicate(
        session,
        target_tipo=request.target_tipo,
        nombre=request.nombre,
        rfc=request.rfc,
        cuenta_clabe=request.cuenta_clabe,
        cuenta_bancaria=request.cuenta_bancaria,
    )
    proveedor = ProveedorCliente(
        tipo=request.target_tipo,
        nombre=request.nombre,
        rfc=request.rfc,
        banco=request.banco,
        cuenta_clabe=request.cuenta_clabe,
        cuenta_bancaria=request.cuenta_bancaria,
        entidad_region=request.entidad_region,
        empleado_id=request.empleado_id,
        activo=True,
    )
    session.add(proveedor)
    await session.flush()
    request.status = "aprobada_registrada"
    request.final_approved_by_empleado_id = actor.id
    request.created_proveedor_cliente_id = proveedor.id
    request.final_decision_comment = normalize_optional_text(comment)
    request.final_decided_at = datetime.utcnow()
    request.actualizado_en = datetime.utcnow()
    session.add(
        Aprobacion(
            tipo_entidad="beneficiary_onboarding",
            entidad_id=request.id,
            aprobador_id=actor.id,
            accion="aprobar_final",
            comentario=request.final_decision_comment,
        )
    )
    if request.requested_by is None and request.requested_by_empleado_id:
        request.requested_by = await session.get(Empleado, request.requested_by_empleado_id)
    await _notify_employee(
        session,
        empleado=request.requested_by,
        notification_type="beneficiary_onboarding_decision",
        header="Alta de beneficiario aprobada y registrada",
        text=_onboarding_summary(request),
    )
    await session.commit()
    await session.refresh(request)
    return request


async def reject_beneficiary_onboarding_final(
    session: AsyncSession,
    *,
    request_id: UUID,
    actor: Empleado,
    comment: Optional[str],
) -> BeneficiaryOnboardingRequest:
    request = await _load_onboarding_request(session, request_id)
    if request is None:
        raise BeneficiaryOnboardingError("not_found", "Solicitud no encontrada.")
    if request.status != "pendiente_revision_final":
        raise BeneficiaryOnboardingError("invalid_status", "La solicitud no está pendiente de revisión final.")
    if not is_final_beneficiary_reviewer(actor):
        raise BeneficiaryOnboardingError("forbidden", "No tienes permiso para rechazar la revisión final.")
    request.status = "rechazada_final"
    request.final_approved_by_empleado_id = actor.id
    request.final_decision_comment = normalize_optional_text(comment)
    request.final_decided_at = datetime.utcnow()
    request.actualizado_en = datetime.utcnow()
    session.add(
        Aprobacion(
            tipo_entidad="beneficiary_onboarding",
            entidad_id=request.id,
            aprobador_id=actor.id,
            accion="rechazar_final",
            comentario=request.final_decision_comment,
        )
    )
    if request.requested_by is None and request.requested_by_empleado_id:
        request.requested_by = await session.get(Empleado, request.requested_by_empleado_id)
    await _notify_employee(
        session,
        empleado=request.requested_by,
        notification_type="beneficiary_onboarding_decision",
        header="Alta de beneficiario rechazada en revisión final",
        text=_onboarding_summary(request),
    )
    await session.commit()
    await session.refresh(request)
    return request
