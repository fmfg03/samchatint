from __future__ import annotations

import base64
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from ..models import Aprobacion, Adjunto, CuentaDeGastos, Documento, Empleado, ProveedorCliente, Tournament
from ..expense_metadata import normalize_categories, normalize_currency, normalize_edition
from .tournament_project_visibility import visibility_validation_error
from ..utils.receipt_bytes import (
    ALLOWED_SOLICITUD_ATTACHMENT_MIME_TYPES,
    MAX_SOLICITUD_ATTACHMENT_BYTES,
    MAX_SOLICITUD_PDF_BYTES,
    create_adjunto_record,
    is_pdf_content,
    resolve_media_type,
)
from .cfdi_expense_link_service import (
    find_cfdi_report_by_fiscal_uuid,
    normalize_cfdi_uuid_to_canonical,
)
from .cfdi_ingestion_service import (
    CFDIDuplicateLinkError,
    CFDIIngestionError,
    has_existing_cfdi_usage,
    ingest_cfdi_from_upload,
)
from samchat.budgets.service import resolve_budget_concept

logger = logging.getLogger(__name__)

_REFERENCIA_OPERACIONES_ADVISORY_LOCK_KEY = 5_842_910_472_931
_OPERACIONES_DEPARTAMENTO = "operaciones"


def _normalize_departamento(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def empleado_allocates_referencia_operaciones(empleado: object) -> bool:
    """True when the creator belongs to Operaciones and should receive a global RO."""
    departamento = _normalize_departamento(getattr(empleado, "departamento", None))
    return departamento == _OPERACIONES_DEPARTAMENTO


def referencia_operaciones_form_display(empleado: object) -> tuple[str, str]:
    """Readonly field copy for solicitud / informe forms: (value, help text)."""
    if empleado_allocates_referencia_operaciones(empleado):
        return (
            "Se asigna automáticamente al guardar",
            "Se asigna automáticamente por el sistema.",
        )
    return (
        "No aplica (solo Operaciones)",
        "Su departamento no usa referencia operaciones; use el número de solicitud "
        "(finanzas) para dar seguimiento.",
    )


async def allocate_referencia_operaciones_for_empleado(
    session: AsyncSession,
    empleado: object,
) -> Optional[str]:
    """Allocate the next RO only for Operaciones creators; otherwise return None."""
    if not empleado_allocates_referencia_operaciones(empleado):
        return None
    return await allocate_next_referencia_operaciones(session)


@dataclass(slots=True)
class SolicitudValidationError(ValueError):
    code: str
    user_message: str

    def __str__(self) -> str:
        return self.user_message


@dataclass(slots=True)
class SolicitudTercerosPayload:
    empleado_id: UUID
    monto_solicitado: float
    proveedor_cliente_id: UUID
    torneo_id: Optional[UUID]
    proyecto_otro: Optional[str]
    concepto_pago: str
    fase: Optional[str] = None
    fecha_pago: Optional[date] = None
    numero_factura: Optional[str] = None
    referencia_pago: Optional[str] = None
    fecha_inicio: Optional[datetime] = None
    fecha_fin: Optional[datetime] = None
    notas: Optional[str] = None
    pdf_bytes: Optional[bytes] = None
    pdf_filename: Optional[str] = None
    cfdi_uuid_manual: Optional[str] = None  # canonical (uppercase) CFDI UUID if known
    attachments: list["SolicitudTercerosAttachment"] = field(default_factory=list)
    categorias: list[str] = field(default_factory=list)
    edicion: Optional[int] = None
    currency: str = "MXN"
    budget_concept_id: Optional[UUID] = None
    pago_urgente: bool = False
    cfdi_compartido_confirmado: bool = False


@dataclass(slots=True)
class SolicitudTercerosAttachment:
    raw_bytes: bytes
    filename: str
    mime_type: Optional[str]
    categoria: str


@dataclass(slots=True)
class SolicitudPersonalPayload:
    cuenta_id: UUID
    empleado_id: UUID
    monto_solicitado: float
    concepto_pago: str
    fecha_pago: Optional[date] = None
    proveedor_cliente_id: Optional[UUID] = None
    budget_concept_id: Optional[UUID] = None
    pago_urgente: bool = False


def _parse_optional_budget_concept_uuid(value: Optional[str]) -> Optional[UUID]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except (TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_budget_concept",
            "La partida presupuestal no es válida.",
        ) from exc


def _parse_optional_datetime(value: Optional[str]) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None


def parse_optional_date(value: Optional[str]) -> Optional[date]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SolicitudValidationError(
            "invalid_fecha_pago",
            "La fecha de pago no es válida.",
        ) from exc


def validate_solicitud_terceros_attachment(
    attachment: SolicitudTercerosAttachment,
) -> tuple[bytes, str, str, str]:
    raw = attachment.raw_bytes or b""
    filename = (attachment.filename or "adjunto").strip() or "adjunto"
    categoria = (attachment.categoria or "supporting").strip().lower()
    if not raw:
        raise SolicitudValidationError(
            "empty_attachment",
            "Uno de los archivos adjuntos está vacío.",
        )

    if categoria == "cfdi_pdf":
        if len(raw) > MAX_SOLICITUD_PDF_BYTES:
            raise SolicitudValidationError(
                "pdf_too_large",
                "El PDF excede el tamaño máximo permitido.",
            )
        if not is_pdf_content(raw):
            raise SolicitudValidationError(
                "invalid_pdf",
                "El archivo debe ser un PDF válido.",
            )
        return raw, "application/pdf", filename[:500], categoria

    if len(raw) > MAX_SOLICITUD_ATTACHMENT_BYTES:
        raise SolicitudValidationError(
            "attachment_too_large",
            "Uno de los archivos adjuntos excede el tamaño máximo permitido.",
        )

    if categoria == "cfdi_xml":
        try:
            ET.fromstring(raw)
        except ET.ParseError as exc:
            raise SolicitudValidationError(
                "invalid_xml",
                "El archivo CFDI XML debe ser un XML válido.",
            ) from exc
        return raw, "application/xml", filename[:500], categoria

    if categoria in {"supporting", "comprobante_pago"}:
        resolved_mime = (attachment.mime_type or "").split(";", 1)[0].strip().lower()
        if not resolved_mime or resolved_mime == "application/octet-stream":
            resolved_mime = resolve_media_type(filename, raw)
        if resolved_mime not in ALLOWED_SOLICITUD_ATTACHMENT_MIME_TYPES:
            sniffed = resolve_media_type(filename, raw)
            if sniffed in ALLOWED_SOLICITUD_ATTACHMENT_MIME_TYPES:
                resolved_mime = sniffed
            else:
                raise SolicitudValidationError(
                    "invalid_attachment_type",
                    "Los anexos deben ser PDF, XML, imagen o documentos comunes.",
                )
        return raw, resolved_mime, filename[:500], categoria

    resolved_mime = (attachment.mime_type or "").split(";", 1)[0].strip().lower()
    if not resolved_mime or resolved_mime == "application/octet-stream":
        resolved_mime = resolve_media_type(filename, raw)
    if resolved_mime not in ALLOWED_SOLICITUD_ATTACHMENT_MIME_TYPES:
        sniffed = resolve_media_type(filename, raw)
        if sniffed in ALLOWED_SOLICITUD_ATTACHMENT_MIME_TYPES:
            resolved_mime = sniffed
        else:
            raise SolicitudValidationError(
                "invalid_attachment_type",
                "Los anexos deben ser PDF, XML, imagen o documentos comunes.",
            )
    return raw, resolved_mime, filename[:500], "supporting"


def _payload_solicitud_terceros_attachments(
    payload: SolicitudTercerosPayload,
) -> list[SolicitudTercerosAttachment]:
    if payload.attachments:
        return list(payload.attachments)
    if not payload.pdf_bytes:
        return []
    return [
        SolicitudTercerosAttachment(
            raw_bytes=payload.pdf_bytes,
            filename=(payload.pdf_filename or "solicitud.pdf"),
            mime_type="application/pdf",
            categoria="cfdi_pdf",
        )
    ]


def build_solicitud_terceros_payload(
    *,
    empleado_id: UUID,
    monto_solicitado: str | float,
    proveedor_cliente_id: str,
    torneo_id: Optional[str],
    proyecto_otro: Optional[str] = None,
    fase: Optional[str] = None,
    concepto_pago: str,
    fecha_pago: Optional[str] = None,
    numero_factura: Optional[str] = None,
    referencia_pago: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    notas: Optional[str] = None,
    pdf_bytes: Optional[bytes] = None,
    pdf_filename: Optional[str] = None,
    attachments: Optional[list[SolicitudTercerosAttachment]] = None,
    cfdi_uuid_manual: Optional[str] = None,
    categorias: Optional[list[str]] = None,
    edicion: object = None,
    currency: Optional[str] = None,
    budget_concept_id: Optional[str] = None,
    pago_urgente: bool = False,
    cfdi_compartido_confirmado: bool = False,
) -> SolicitudTercerosPayload:
    try:
        monto = float(monto_solicitado)
        if monto <= 0:
            raise SolicitudValidationError(
                "invalid_monto",
                "El monto solicitado debe ser mayor a cero.",
            )
    except SolicitudValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_monto",
            "El monto solicitado debe ser un número válido.",
        ) from exc

    proveedor_raw = (proveedor_cliente_id or "").strip()
    if not proveedor_raw:
        raise SolicitudValidationError(
            "missing_proveedor",
            "El proveedor/cliente es requerido.",
        )
    try:
        proveedor_uuid = UUID(proveedor_raw)
    except (TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_proveedor",
            "El ID del proveedor/cliente no es válido.",
        ) from exc

    torneo_raw = (torneo_id or "").strip()
    proyecto_otro_raw = (proyecto_otro or "").strip()
    torneo_uuid: Optional[UUID] = None
    if not torneo_raw:
        raise SolicitudValidationError(
            "missing_torneo",
            "El proyecto es requerido.",
        )
    if torneo_raw == "__otro__":
        if not proyecto_otro_raw:
            raise SolicitudValidationError(
                "missing_proyecto_otro",
                "Debe describir el proyecto cuando selecciona 'Otro'.",
            )
    else:
        try:
            torneo_uuid = UUID(torneo_raw)
        except (TypeError, ValueError) as exc:
            raise SolicitudValidationError(
                "invalid_torneo",
                "El proyecto seleccionado no es válido.",
            ) from exc

    concepto = (concepto_pago or "").strip()
    if not concepto:
        raise SolicitudValidationError(
            "missing_concepto",
            "El concepto de pago es requerido.",
        )

    cfdi_uuid_canonical: Optional[str] = None
    raw_cfdi = (cfdi_uuid_manual or "").strip()
    if raw_cfdi:
        try:
            cfdi_uuid_canonical = normalize_cfdi_uuid_to_canonical(raw_cfdi)
        except ValueError as exc:
            raise SolicitudValidationError(
                "invalid_cfdi_uuid",
                "UUID CFDI inválido. Debe ser un UUID válido (ej: C027C9F4-92CF-4190-BB89-3E76AB2ECA70).",
            ) from exc

    payload_attachments = list(attachments or [])
    if pdf_bytes:
        payload_attachments.insert(
            0,
            SolicitudTercerosAttachment(
                raw_bytes=pdf_bytes,
                filename=(pdf_filename or "solicitud.pdf").strip() or "solicitud.pdf",
                mime_type="application/pdf",
                categoria="cfdi_pdf",
            ),
        )

    try:
        normalized_edition = normalize_edition(edicion, default_current_year=True)
        normalized_currency = normalize_currency(currency)
    except ValueError as exc:
        raise SolicitudValidationError("invalid_metadata", str(exc)) from exc

    return SolicitudTercerosPayload(
        empleado_id=empleado_id,
        monto_solicitado=monto,
        proveedor_cliente_id=proveedor_uuid,
        torneo_id=torneo_uuid,
        proyecto_otro=proyecto_otro_raw or None,
        fase=(fase or "").strip() or None,
        concepto_pago=concepto,
        fecha_pago=parse_optional_date(fecha_pago),
        numero_factura=(numero_factura or "").strip() or None,
        referencia_pago=(referencia_pago or "").strip() or None,
        fecha_inicio=_parse_optional_datetime(fecha_inicio),
        fecha_fin=_parse_optional_datetime(fecha_fin),
        notas=(notas or "").strip() or None,
        pdf_bytes=pdf_bytes,
        pdf_filename=(pdf_filename or "").strip() or None,
        cfdi_uuid_manual=cfdi_uuid_canonical,
        attachments=payload_attachments,
        categorias=list(categorias or []),
        edicion=normalized_edition,
        currency=normalized_currency,
        budget_concept_id=_parse_optional_budget_concept_uuid(budget_concept_id),
        pago_urgente=bool(pago_urgente),
        cfdi_compartido_confirmado=bool(cfdi_compartido_confirmado),
    )


def build_solicitud_personal_payload(
    *,
    cuenta_id: str | UUID,
    empleado_id: UUID,
    monto_solicitado: str | float,
    concepto_pago: str,
    fecha_pago: Optional[str] = None,
    proveedor_cliente_id: Optional[str] = None,
    budget_concept_id: Optional[str] = None,
    pago_urgente: bool = False,
) -> SolicitudPersonalPayload:
    try:
        cuenta_uuid = (
            cuenta_id if isinstance(cuenta_id, UUID) else UUID(str(cuenta_id).strip())
        )
    except (TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_cuenta",
            "La cuenta de gastos no es válida.",
        ) from exc

    try:
        monto = float(monto_solicitado)
    except (TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_monto",
            "Monto inválido.",
        ) from exc
    if monto <= 0:
        raise SolicitudValidationError(
            "invalid_monto",
            "El monto debe ser mayor a cero.",
        )

    concepto = (concepto_pago or "").strip()
    if not concepto:
        raise SolicitudValidationError(
            "missing_concepto",
            "El concepto de pago es requerido.",
        )

    proveedor_uuid: Optional[UUID] = None
    proveedor_raw = (proveedor_cliente_id or "").strip()
    if proveedor_raw:
        try:
            proveedor_uuid = UUID(proveedor_raw)
        except (TypeError, ValueError) as exc:
            raise SolicitudValidationError(
                "invalid_proveedor",
                "El proveedor/cliente no es válido.",
            ) from exc

    return SolicitudPersonalPayload(
        cuenta_id=cuenta_uuid,
        empleado_id=empleado_id,
        monto_solicitado=monto,
        concepto_pago=concepto,
        fecha_pago=parse_optional_date(fecha_pago),
        proveedor_cliente_id=proveedor_uuid,
        budget_concept_id=_parse_optional_budget_concept_uuid(budget_concept_id),
        pago_urgente=bool(pago_urgente),
    )


async def generate_documento_reference_number(
    session: AsyncSession,
    tipo: str,
    empleado_id: UUID,
) -> str:
    """Generate a reference number for a documento."""
    tipo_prefix = "I" if tipo == "INFORME" else "S"
    current_year = datetime.now().year
    year_suffix = str(current_year)[-2:]
    prefix = f"{tipo_prefix}-{year_suffix}"

    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"documento_ref:{prefix}"},
        )
    except Exception as exc:  # pragma: no cover - defensive best-effort
        logger.warning(
            "Could not acquire advisory lock for documento reference generation; "
            "falling back to best-effort allocation",
            extra={"prefix": prefix, "error": str(exc)},
        )

    result = await session.execute(
        select(func.max(Documento.numero_referencia)).where(
            Documento.numero_referencia.like(f"{prefix}%")
        )
    )
    max_ref = result.scalar_one_or_none()

    if max_ref:
        try:
            sequence_str = max_ref.split("-")[1][2:]
            next_sequence = int(sequence_str) + 1
        except (IndexError, ValueError):
            next_sequence = 1
    else:
        next_sequence = 1

    reference_number = f"{prefix}{next_sequence:06d}"
    logger.info(
        "Generated documento reference number %s for tipo %s and empleado %s",
        reference_number,
        tipo,
        empleado_id,
    )
    return reference_number


async def allocate_next_referencia_operaciones(session: AsyncSession) -> str:
    """Allocate the next global Referencia Operaciones counter."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": _REFERENCIA_OPERACIONES_ADVISORY_LOCK_KEY},
    )
    result = await session.execute(
        text(
            """
            SELECT COALESCE(
                MAX(CAST(referencia_operaciones AS BIGINT)),
                0
            ) + 1 AS next_n
            FROM documentos
            WHERE referencia_operaciones ~ '^[0-9]+$'
            """
        )
    )
    next_n = result.scalar_one()
    return str(int(next_n))


async def create_solicitud_terceros_document(
    session: AsyncSession,
    payload: SolicitudTercerosPayload,
) -> Documento:
    """Create a SOLICITUD document for a third-party payment request."""
    proveedor_result = await session.execute(
        select(ProveedorCliente).where(
            and_(
                ProveedorCliente.id == payload.proveedor_cliente_id,
                ProveedorCliente.activo == True,
            )
        )
    )
    proveedor = proveedor_result.scalar_one_or_none()
    if proveedor is None:
        raise SolicitudValidationError(
            "invalid_proveedor",
            "Proveedor/Cliente inválido o inactivo.",
        )

    empleado_result = await session.execute(
        select(Empleado).where(Empleado.id == payload.empleado_id)
    )
    empleado = empleado_result.scalar_one_or_none()
    if empleado is None:
        raise SolicitudValidationError(
            "invalid_empleado",
            "Empleado no encontrado.",
        )

    torneo = None
    if payload.torneo_id is not None:
        torneo_result = await session.execute(
            select(Tournament).where(
                Tournament.id == payload.torneo_id,
                Tournament.active == True,
            )
        )
        torneo = torneo_result.scalar_one_or_none()
        if torneo is None:
            raise SolicitudValidationError(
                "invalid_torneo",
                "El proyecto no existe o no está activo.",
            )
        vis_err = visibility_validation_error(torneo, empleado)
        if vis_err:
            raise SolicitudValidationError("invalid_torneo", vis_err)
        try:
            payload.categorias = normalize_categories(payload.categorias, torneo)
        except ValueError as exc:
            raise SolicitudValidationError("invalid_categorias", str(exc)) from exc
        concept = await resolve_budget_concept(
            session,
            budget_concept_id=str(payload.budget_concept_id) if payload.budget_concept_id else None,
            tournament_id=str(payload.torneo_id),
            tournament_code=None,
            fase=payload.fase,
        )
        if payload.budget_concept_id is None:
            raise SolicitudValidationError(
                "missing_budget_concept",
                "La partida presupuestal es requerida para solicitudes con torneo.",
            )
        if concept is None:
            raise SolicitudValidationError(
                "invalid_budget_concept",
                "La partida presupuestal no corresponde al torneo seleccionado.",
            )
    elif not (payload.proyecto_otro or "").strip():
        raise SolicitudValidationError(
            "missing_torneo",
            "El proyecto es requerido.",
        )
    elif payload.categorias:
        raise SolicitudValidationError(
            "invalid_categorias",
            "Las categorías solo pueden seleccionarse para un proyecto configurado.",
        )

    validated_attachments = [
        validate_solicitud_terceros_attachment(attachment)
        for attachment in _payload_solicitud_terceros_attachments(payload)
    ]

    numero_referencia = await generate_documento_reference_number(
        session,
        "SOLICITUD",
        payload.empleado_id,
    )
    referencia_operaciones = await allocate_referencia_operaciones_for_empleado(
        session, empleado
    )

    cfdi_report_id = None
    if payload.cfdi_uuid_manual:
        matched = await find_cfdi_report_by_fiscal_uuid(
            session, payload.cfdi_uuid_manual
        )
        if matched is not None:
            if (
                await has_existing_cfdi_usage(session, matched.id)
                and not payload.cfdi_compartido_confirmado
            ):
                raise SolicitudValidationError(
                    "duplicate_cfdi",
                    "La factura ya está vinculada a otro gasto o solicitud. "
                    "Confirme explícitamente que es una factura compartida para "
                    "continuar.",
                )
            cfdi_report_id = matched.id

    documento = Documento(
        empleado_id=payload.empleado_id,
        tipo="SOLICITUD",
        numero_referencia=numero_referencia,
        estado="borrador",
        fecha_inicio=payload.fecha_inicio,
        fecha_fin=payload.fecha_fin,
        monto_solicitado=payload.monto_solicitado,
        monto_total=payload.monto_solicitado,
        categorias=payload.categorias,
        edicion=payload.edicion,
        currency=payload.currency,
        torneo_id=payload.torneo_id,
        proyecto_otro=payload.proyecto_otro,
        fase=payload.fase,
        proveedor_cliente_id=payload.proveedor_cliente_id,
        beneficiario_empleado_id=None,
        fecha_pago=None,
        pago_urgente=payload.pago_urgente,
        concepto_pago=payload.concepto_pago,
        numero_factura=payload.numero_factura,
        referencia_pago=payload.referencia_pago,
        referencia_operaciones=referencia_operaciones,
        notas=payload.notas,
        budget_concept_id=payload.budget_concept_id,
        cfdi_uuid_manual=payload.cfdi_uuid_manual,
        cfdi_compartido_confirmado=payload.cfdi_compartido_confirmado,
        cfdi_report_id=cfdi_report_id,
    )
    session.add(documento)
    await session.flush()

    await _ingest_solicitud_cfdi_from_attachments(
        session,
        documento=documento,
        validated_attachments=validated_attachments,
        numero_referencia=numero_referencia,
    )

    for raw_bytes, mime_type, filename, categoria in validated_attachments:
        await create_adjunto_record(
            session,
            documento_id=documento.id,
            ruta_archivo=base64.b64encode(raw_bytes).decode("ascii"),
            tipo_archivo=mime_type,
            mime_type=mime_type,
            categoria=categoria,
            origen="document_upload",
            nombre_archivo=filename,
        )

    await session.commit()
    await session.refresh(documento)
    logger.info(
        "Created SOLICITUD a terceros %s (%s) for empleado %s",
        documento.id,
        numero_referencia,
        payload.empleado_id,
    )
    return documento


async def _ingest_solicitud_cfdi_from_attachments(
    session: AsyncSession,
    *,
    documento: Documento,
    validated_attachments: list[tuple[bytes, str, str, str]],
    numero_referencia: str,
) -> None:
    xml_bytes: Optional[bytes] = None
    pdf_bytes: Optional[bytes] = None
    for raw_bytes, _mime_type, _filename, categoria in validated_attachments:
        if categoria == "cfdi_xml" and xml_bytes is None:
            xml_bytes = raw_bytes
        elif categoria == "cfdi_pdf" and pdf_bytes is None:
            pdf_bytes = raw_bytes

    if not xml_bytes and not pdf_bytes:
        return

    try:
        await ingest_cfdi_from_upload(
            session,
            xml_bytes=xml_bytes,
            pdf_bytes=pdf_bytes,
            source="user_upload",
            entity=documento,
            numero_referencia=numero_referencia,
            allow_shared=bool(documento.cfdi_compartido_confirmado),
            require_shared_confirmation=True,
        )
    except CFDIDuplicateLinkError as exc:
        raise SolicitudValidationError("duplicate_cfdi", str(exc)) from exc
    except CFDIIngestionError as exc:
        code = "invalid_cfdi_xml" if xml_bytes and xml_bytes.strip() else "invalid_cfdi"
        raise SolicitudValidationError(code, str(exc)) from exc


async def _persist_solicitud_terceros_adjuntos(
    session: AsyncSession,
    *,
    documento: Documento,
    attachments: list[SolicitudTercerosAttachment],
) -> None:
    validated_attachments = [
        validate_solicitud_terceros_attachment(attachment)
        for attachment in attachments
    ]
    numero_referencia = documento.numero_referencia or str(documento.id)
    await _ingest_solicitud_cfdi_from_attachments(
        session,
        documento=documento,
        validated_attachments=validated_attachments,
        numero_referencia=numero_referencia,
    )
    for raw_bytes, mime_type, filename, categoria in validated_attachments:
        await create_adjunto_record(
            session,
            documento_id=documento.id,
            ruta_archivo=base64.b64encode(raw_bytes).decode("ascii"),
            tipo_archivo=mime_type,
            mime_type=mime_type,
            categoria=categoria,
            origen="document_upload",
            nombre_archivo=filename,
        )


async def add_solicitud_documento_adjuntos(
    session: AsyncSession,
    *,
    documento: Documento,
    attachments: list[SolicitudTercerosAttachment],
) -> int:
    """Append validated attachments to an existing SOLICITUD document."""
    if not attachments:
        return 0
    await _persist_solicitud_terceros_adjuntos(
        session,
        documento=documento,
        attachments=attachments,
    )
    await session.commit()
    return len(attachments)


async def remove_solicitud_documento_adjunto(
    session: AsyncSession,
    *,
    documento_id: UUID,
    adjunto_id: UUID,
) -> str:
    """Remove one attachment row from a solicitud document."""
    result = await session.execute(
        select(Adjunto).where(
            Adjunto.id == adjunto_id,
            Adjunto.documento_id == documento_id,
        )
    )
    adjunto = result.scalar_one_or_none()
    if adjunto is None:
        raise SolicitudValidationError(
            "adjunto_not_found",
            "El archivo no existe o ya fue eliminado.",
        )

    categoria = (adjunto.categoria or "supporting").strip().lower()
    nombre = (adjunto.nombre_archivo or "archivo").strip() or "archivo"
    await session.delete(adjunto)
    await session.flush()

    if categoria in {"cfdi_xml", "cfdi_pdf"}:
        remaining_cfdi = await session.execute(
            select(Adjunto.id).where(
                Adjunto.documento_id == documento_id,
                Adjunto.categoria.in_(["cfdi_xml", "cfdi_pdf"]),
            ).limit(1)
        )
        if remaining_cfdi.first() is None:
            documento = await session.get(Documento, documento_id)
            if documento is not None:
                documento.cfdi_report_id = None
                documento.cfdi_uuid_manual = None

    await session.commit()
    return nombre


async def update_solicitud_terceros_document(
    session: AsyncSession,
    *,
    documento: Documento,
    payload: SolicitudTercerosPayload,
) -> Documento:
    """Update an editable SOLICITUD a terceros in borrador or rechazado.

    Rejected solicitudes return to borrador when saved so the owner can re-send.
    """
    if documento.tipo != "SOLICITUD":
        raise SolicitudValidationError(
            "invalid_documento",
            "Solo se pueden editar solicitudes.",
        )
    if documento.estado not in {"borrador", "rechazado"}:
        raise SolicitudValidationError(
            "invalid_estado",
            "Solo se pueden editar solicitudes en borrador o rechazadas.",
        )
    if documento.empleado_id != payload.empleado_id:
        raise SolicitudValidationError(
            "invalid_empleado",
            "No tiene permiso para editar esta solicitud.",
        )

    proveedor_result = await session.execute(
        select(ProveedorCliente).where(
            and_(
                ProveedorCliente.id == payload.proveedor_cliente_id,
                ProveedorCliente.activo == True,
            )
        )
    )
    if proveedor_result.scalar_one_or_none() is None:
        raise SolicitudValidationError(
            "invalid_proveedor",
            "Proveedor/Cliente inválido o inactivo.",
        )

    empleado_result = await session.execute(
        select(Empleado).where(Empleado.id == payload.empleado_id)
    )
    empleado = empleado_result.scalar_one_or_none()
    if empleado is None:
        raise SolicitudValidationError(
            "invalid_empleado",
            "Empleado no encontrado.",
        )

    torneo = None
    if payload.torneo_id is not None:
        torneo_result = await session.execute(
            select(Tournament).where(Tournament.id == payload.torneo_id)
        )
        torneo = torneo_result.scalar_one_or_none()
        if torneo is None:
            raise SolicitudValidationError(
                "invalid_torneo",
                "El proyecto no existe.",
            )
        vis_err = visibility_validation_error(torneo, empleado)
        if vis_err:
            raise SolicitudValidationError("invalid_torneo", vis_err)
        try:
            payload.categorias = normalize_categories(payload.categorias, torneo)
        except ValueError as exc:
            raise SolicitudValidationError("invalid_categorias", str(exc)) from exc
        concept = await resolve_budget_concept(
            session,
            budget_concept_id=str(payload.budget_concept_id) if payload.budget_concept_id else None,
            tournament_id=str(payload.torneo_id),
            tournament_code=None,
            fase=payload.fase,
        )
        if payload.budget_concept_id is None:
            raise SolicitudValidationError(
                "missing_budget_concept",
                "La partida presupuestal es requerida para solicitudes con torneo.",
            )
        if concept is None:
            raise SolicitudValidationError(
                "invalid_budget_concept",
                "La partida presupuestal no corresponde al torneo seleccionado.",
            )
    elif not (payload.proyecto_otro or "").strip():
        raise SolicitudValidationError(
            "missing_torneo",
            "El proyecto es requerido.",
        )
    elif payload.categorias:
        raise SolicitudValidationError(
            "invalid_categorias",
            "Las categorías solo pueden seleccionarse para un proyecto configurado.",
        )

    documento.monto_solicitado = payload.monto_solicitado
    documento.monto_total = payload.monto_solicitado
    documento.proveedor_cliente_id = payload.proveedor_cliente_id
    documento.torneo_id = payload.torneo_id
    documento.proyecto_otro = payload.proyecto_otro
    documento.fase = payload.fase
    documento.categorias = payload.categorias
    documento.edicion = payload.edicion
    documento.currency = payload.currency
    documento.budget_concept_id = payload.budget_concept_id
    documento.pago_urgente = payload.pago_urgente
    documento.cfdi_compartido_confirmado = payload.cfdi_compartido_confirmado
    # fecha_pago is assigned on approval only; do not update from form payload.
    documento.concepto_pago = payload.concepto_pago
    documento.numero_factura = payload.numero_factura
    documento.referencia_pago = payload.referencia_pago
    documento.fecha_inicio = payload.fecha_inicio
    documento.fecha_fin = payload.fecha_fin
    documento.notas = payload.notas
    if documento.estado == "rechazado":
        documento.estado = "borrador"
        documento.enviado_en = None

    new_attachments = _payload_solicitud_terceros_attachments(payload)
    if new_attachments:
        await _persist_solicitud_terceros_adjuntos(
            session,
            documento=documento,
            attachments=new_attachments,
        )

    await session.commit()
    await session.refresh(documento)
    return documento


async def create_solicitud_personal_document(
    session: AsyncSession,
    payload: SolicitudPersonalPayload,
) -> Documento:
    """Create a SOLICITUD personal linked to a Cuenta de Gastos."""
    cuenta_result = await session.execute(
        select(CuentaDeGastos)
        .where(CuentaDeGastos.id == payload.cuenta_id)
        .options(
            undefer(CuentaDeGastos.torneo_id),
            undefer(CuentaDeGastos.fase),
        )
    )
    cuenta = cuenta_result.scalar_one_or_none()
    if cuenta is None:
        raise SolicitudValidationError(
            "invalid_cuenta",
            "Informe de Gastos no encontrado.",
        )
    if cuenta.empleado_id != payload.empleado_id:
        raise SolicitudValidationError(
            "invalid_cuenta",
            "La cuenta de gastos no pertenece al usuario actual.",
        )

    empleado_result = await session.execute(
        select(Empleado).where(Empleado.id == payload.empleado_id)
    )
    empleado = empleado_result.scalar_one_or_none()
    if empleado is None:
        raise SolicitudValidationError(
            "invalid_empleado",
            "Empleado no encontrado.",
        )

    if cuenta.estado == "cerrada":
        raise SolicitudValidationError(
            "cuenta_cerrada",
            "La cuenta de gastos está cerrada.",
        )
    concept = await resolve_budget_concept(
        session,
        budget_concept_id=str(payload.budget_concept_id) if payload.budget_concept_id else None,
        tournament_id=str(cuenta.torneo_id) if getattr(cuenta, "torneo_id", None) else None,
        tournament_code=None,
        fase=getattr(cuenta, "fase", None),
    )
    if payload.budget_concept_id is not None and concept is None:
        raise SolicitudValidationError(
            "invalid_budget_concept",
            "La partida presupuestal no corresponde al torneo del informe.",
        )

    selected_proveedor = None
    if payload.proveedor_cliente_id is not None:
        proveedor_result = await session.execute(
            select(ProveedorCliente).where(
                and_(
                    ProveedorCliente.id == payload.proveedor_cliente_id,
                    ProveedorCliente.activo == True,
                )
            )
        )
        selected_proveedor = proveedor_result.scalar_one_or_none()
        if selected_proveedor is None:
            raise SolicitudValidationError(
                "invalid_proveedor",
                "Proveedor/Cliente inválido o inactivo.",
            )

    informe_result = await session.execute(
        select(Documento)
        .where(
            Documento.cuenta_gastos_id == payload.cuenta_id,
            Documento.tipo == "INFORME",
        )
        .order_by(Documento.creado_en.asc())
        .limit(1)
    )
    informe_doc = informe_result.scalar_one_or_none()
    if informe_doc is None:
        raise SolicitudValidationError(
            "missing_informe",
            "Documento de informe no encontrado para esta cuenta.",
        )

    ro_shared = (informe_doc.referencia_operaciones or "").strip()
    if not ro_shared:
        allocated = await allocate_referencia_operaciones_for_empleado(
            session, empleado
        )
        if allocated:
            ro_shared = allocated
            informe_doc.referencia_operaciones = ro_shared

    numero_referencia = await generate_documento_reference_number(
        session,
        "SOLICITUD",
        payload.empleado_id,
    )
    documento = Documento(
        empleado_id=payload.empleado_id,
        tipo="SOLICITUD",
        numero_referencia=numero_referencia,
        estado="borrador",
        monto_solicitado=payload.monto_solicitado,
        monto_total=payload.monto_solicitado,
        concepto_pago=payload.concepto_pago,
        fecha_pago=None,
        pago_urgente=payload.pago_urgente,
        referencia_operaciones=ro_shared or None,
        beneficiario_empleado_id=payload.empleado_id,
        proveedor_cliente_id=(
            selected_proveedor.id if selected_proveedor is not None else None
        ),
        cuenta_gastos_id=cuenta.id,
        budget_concept_id=payload.budget_concept_id,
        referencia_base=cuenta.referencia_base,
        fase=((getattr(cuenta, "fase", None) or "").strip() or None),
        categorias=list(getattr(cuenta, "categorias", None) or []),
        edicion=getattr(cuenta, "edicion", None),
        currency=normalize_currency(getattr(cuenta, "currency", None)),
    )
    session.add(documento)
    await session.commit()
    await session.refresh(documento)
    logger.info(
        "Created SOLICITUD personal %s (%s) for cuenta %s and empleado %s",
        documento.id,
        numero_referencia,
        cuenta.id,
        payload.empleado_id,
    )
    return documento


async def fetch_documento_aprobador_display_batch(
    session: AsyncSession,
    documentos: Sequence[Documento],
) -> Dict[UUID, str]:
    """Resolve Aprobador display names for document list views.

    Prefer the actor on the latest aprobar/rechazar aprobacion; otherwise fall
    back to the solicitante's assigned approver (empleado.aprobador).
    """
    if not documentos:
        return {}

    doc_ids = [doc.id for doc in documentos]
    result = await session.execute(
        select(Aprobacion)
        .options(selectinload(Aprobacion.aprobador))
        .where(
            Aprobacion.tipo_entidad == "documento",
            Aprobacion.entidad_id.in_(doc_ids),
            Aprobacion.accion.in_(("aprobar", "rechazar")),
        )
        .order_by(Aprobacion.fecha.desc())
    )

    latest_by_doc: Dict[UUID, str] = {}
    for aprobacion in result.scalars().all():
        if aprobacion.entidad_id in latest_by_doc:
            continue
        aprobador = aprobacion.aprobador
        if aprobador and aprobador.nombre:
            latest_by_doc[aprobacion.entidad_id] = aprobador.nombre

    display_by_doc: Dict[UUID, str] = {}
    for doc in documentos:
        if doc.id in latest_by_doc:
            display_by_doc[doc.id] = latest_by_doc[doc.id]
            continue
        empleado = doc.empleado
        assigned = getattr(empleado, "aprobador", None) if empleado else None
        if assigned and assigned.nombre:
            display_by_doc[doc.id] = assigned.nombre
        else:
            display_by_doc[doc.id] = "—"
    return display_by_doc
