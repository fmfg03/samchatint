from __future__ import annotations

import base64
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date
from typing import Dict, Optional, Sequence
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from ..models import (
    Aprobacion,
    Adjunto,
    CFDIReport,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    ProveedorCliente,
    RFCConfig,
    Tournament,
)
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
    CFDIConflictError,
    CFDIIngestionError,
    find_blocking_cfdi_usage,
    find_blocking_cfdi_evidence,
    lock_cfdi_identity,
    validate_cfdi_material_identity,
    ingest_cfdi_from_upload,
)
from .cfdi_upload_resolver import merge_cfdi_upload_bytes, resolve_cfdi_upload
from .documento_semantics import approval_subject_empleado_id
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


_CFDI_PAYMENT_RESERVING_STATES = frozenset(
    {
        "control_presupuestal",
        "enviado",
        "aprobado",
        "en_proceso_pago",
        "pagado",
        "cerrado",
        "reembolsado",
        "aplicado",
        "liquidado",
    }
)
_CFDI_MONEY_CENT = Decimal("0.01")


def _cfdi_money(value: object, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_CFDI_MONEY_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_cfdi_amount",
            f"No se pudo validar el {field} fiscal de la factura.",
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise SolicitudValidationError(
            "invalid_cfdi_amount",
            f"El {field} fiscal de la factura no es válido.",
        )
    return amount


def shared_cfdi_remaining_amount(
    *,
    invoice_total: object,
    reserved_amounts: Sequence[object],
    requested_amount: object,
) -> Decimal:
    """Return invoice balance after enforcing the shared-CFDI amount invariant."""
    invoice = _cfdi_money(invoice_total, field="total")
    if invoice <= 0:
        raise SolicitudValidationError(
            "invalid_cfdi_amount",
            "La factura compartida no tiene un total fiscal válido para calcular el saldo.",
        )
    reserved = sum(
        (_cfdi_money(amount, field="monto reservado") for amount in reserved_amounts),
        Decimal("0.00"),
    )
    requested = _cfdi_money(requested_amount, field="monto solicitado")
    remaining = (invoice - reserved).quantize(_CFDI_MONEY_CENT, rounding=ROUND_HALF_UP)
    if remaining <= 0:
        raise SolicitudValidationError(
            "cfdi_fully_allocated",
            (
                "La factura ya está cubierta por solicitudes o pagos previos "
                f"(${reserved:,.2f} de ${invoice:,.2f})."
            ),
        )
    if requested > remaining:
        raise SolicitudValidationError(
            "cfdi_amount_exceeds_remaining",
            (
                f"El monto solicitado (${requested:,.2f}) excede el saldo disponible "
                f"de la factura (${remaining:,.2f}). Ajuste la solicitud al saldo restante."
            ),
        )
    return remaining


async def validate_shared_cfdi_payment_amount(
    session: AsyncSession,
    *,
    cfdi_report: object,
    requested_amount: object,
    exclude_documento_id: Optional[UUID] = None,
    exclude_expense_id: Optional[UUID] = None,
) -> Decimal:
    """Atomically enforce that shared CFDI requests never exceed fiscal total."""
    report_id = getattr(cfdi_report, "id", None)
    fiscal_uuid = getattr(cfdi_report, "cfdi_uuid", None)
    if report_id is None and not fiscal_uuid:
        raise SolicitudValidationError(
            "invalid_cfdi_amount",
            "No se pudo identificar la factura compartida para validar su saldo.",
        )
    if fiscal_uuid:
        fiscal_uuid = normalize_cfdi_uuid_to_canonical(fiscal_uuid)
        await lock_cfdi_identity(session, fiscal_uuid)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"shared_cfdi_amount:{report_id or fiscal_uuid}"},
    )
    late_reservation = (
        select(Aprobacion.id)
        .where(
            Aprobacion.tipo_entidad == "documento",
            Aprobacion.accion == "adjuntar_factura",
            Aprobacion.comentario == fiscal_uuid,
            Aprobacion.entidad_id == Documento.id,
        )
        .exists()
        if fiscal_uuid
        else false()
    )
    invoice_reservation = or_(
        Documento.cfdi_report_id == report_id if report_id else false(),
        late_reservation,
    )
    documento_conditions = [
        invoice_reservation,
        Documento.estado.in_(_CFDI_PAYMENT_RESERVING_STATES),
        Documento.monto_solicitado.is_not(None),
    ]
    if exclude_documento_id is not None:
        documento_conditions.append(Documento.id != exclude_documento_id)
    documento_result = await session.execute(
        select(Documento.monto_solicitado).where(and_(*documento_conditions))
    )
    # A request amount is the reservation authority. An ExpenseReport linked to
    # that same Documento commonly carries the CFDI's full fiscal total, so adding
    # both would double-count a partial payment. An expense can be linked to both
    # its paid request and an INFORME; exclude it when any linked Documento has
    # already supplied the reservation.
    reservation_document_exists = select(Documento.id).where(
        invoice_reservation,
        Documento.estado.in_(_CFDI_PAYMENT_RESERVING_STATES),
        Documento.monto_solicitado.is_not(None),
        or_(
            Documento.id == ExpenseReport.documento_id,
            Documento.id == ExpenseReport.solicitud_documento_id,
            Documento.id == ExpenseReport.informe_documento_id,
        ),
    )
    if exclude_documento_id is not None:
        reservation_document_exists = reservation_document_exists.where(
            Documento.id != exclude_documento_id
        )
    expense_conditions = [
        ExpenseReport.cfdi_report_id == report_id if report_id else false(),
        ExpenseReport.estado_gasto != "cancelado",
        ~reservation_document_exists.exists(),
    ]
    if exclude_expense_id is not None:
        expense_conditions.append(ExpenseReport.id != exclude_expense_id)
    expense_result = await session.execute(
        select(
            ExpenseReport.gasto_cantidad
            - func.coalesce(ExpenseReport.propina_no_deducible, 0)
        ).where(and_(*expense_conditions))
    )
    return shared_cfdi_remaining_amount(
        invoice_total=getattr(cfdi_report, "total", None),
        reserved_amounts=(
            documento_result.scalars().all() + expense_result.scalars().all()
        ),
        requested_amount=requested_amount,
    )


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
    client_submission_id: Optional[UUID] = None
    can_disclose_cfdi_conflict: bool = False
    is_supplier_advance: bool = False
    supplier_advance_due_date: Optional[date] = None


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
    allow_closed_cuenta: bool = False


def _parse_optional_budget_concept_uuid(value: Optional[str]) -> Optional[UUID]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except (TypeError, ValueError) as exc:
        raise SolicitudValidationError(
            "invalid_budget_concept",
            "El concepto no es válida.",
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
    client_submission_id: Optional[str] = None,
    can_disclose_cfdi_conflict: bool = False,
    is_supplier_advance: bool = False,
    supplier_advance_due_date: Optional[str] = None,
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
            "La descripción de pago es requerida.",
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

    submission_id: Optional[UUID] = None
    if (client_submission_id or "").strip():
        try:
            submission_id = UUID(str(client_submission_id).strip())
        except (TypeError, ValueError) as exc:
            raise SolicitudValidationError(
                "invalid_submission_id", "El identificador del envío no es válido."
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

    due_date = parse_optional_date(supplier_advance_due_date)
    if is_supplier_advance:
        if normalized_currency != "MXN":
            raise SolicitudValidationError(
                "advance_currency_unsupported", "Los anticipos a proveedores requieren MXN."
            )
        if due_date is None:
            raise SolicitudValidationError(
                "advance_due_date_required",
                "Indique la fecha esperada de comprobación.",
            )
        if (
            cfdi_uuid_manual
            or cfdi_compartido_confirmado
            or any(a.categoria == "cfdi_xml" for a in payload_attachments)
        ):
            raise SolicitudValidationError(
                "advance_invoice_not_allowed",
                "La factura se registra después del pago mediante Comprobar anticipo. Use Materialidades para el soporte inicial.",
            )

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
        client_submission_id=submission_id,
        can_disclose_cfdi_conflict=bool(can_disclose_cfdi_conflict),
        is_supplier_advance=bool(is_supplier_advance),
        supplier_advance_due_date=due_date,
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
    allow_closed_cuenta: bool = False,
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
            "La descripción de pago es requerida.",
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
        allow_closed_cuenta=bool(allow_closed_cuenta),
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
    if payload.is_supplier_advance:
        from .supplier_advance_service import validate_initial_advance

        validate_initial_advance(payload, payload.supplier_advance_due_date)
    if payload.client_submission_id is not None:
        # The lock plus the database unique index makes a duplicate POST from
        # the same browser form a replay, even when both requests arrive at
        # the same time.
        lock_key = f"solicitud:{payload.empleado_id}:{payload.client_submission_id}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )
        existing = await session.execute(
            select(Documento).where(
                Documento.empleado_id == payload.empleado_id,
                Documento.client_submission_id == payload.client_submission_id,
            )
        )
        existing_documento = existing.scalar_one_or_none()
        if existing_documento is not None:
            # This transient marker avoids a second success-audit event in the
            # HTTP route; it is deliberately not persisted.
            existing_documento._idempotent_replay = True
            return existing_documento
    proveedor_result = await session.execute(
        select(ProveedorCliente).where(
            and_(
                ProveedorCliente.id == payload.proveedor_cliente_id,
                ProveedorCliente.activo == True,
                ProveedorCliente.tipo != "empleado",
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
        concept = None
        if payload.budget_concept_id is not None:
            concept = await resolve_budget_concept(
                session,
                budget_concept_id=str(payload.budget_concept_id),
                tournament_id=str(payload.torneo_id),
                tournament_code=None,
                fase=payload.fase,
                budget_direction="expense",
            )
            if concept is None:
                raise SolicitudValidationError(
                    "invalid_budget_concept",
                    "El concepto no corresponde al torneo seleccionado.",
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
        await lock_cfdi_identity(session, payload.cfdi_uuid_manual)
        matched = await find_cfdi_report_by_fiscal_uuid(
            session, payload.cfdi_uuid_manual
        )
        if matched is not None:
            conflict = await find_blocking_cfdi_usage(session, matched.id)
            if conflict is None:
                conflict = await find_blocking_cfdi_evidence(
                    session, payload.cfdi_uuid_manual
                )
            if conflict is not None:
                if not payload.cfdi_compartido_confirmado:
                    message = (
                        "No se puede continuar porque el UUID CFDI ya está reservado "
                        "en otro gasto o solicitud; este control evita duplicar comprobantes. "
                        "Si el vínculo es un duplicado, un superadmin puede revisarlo y "
                        "liberarlo desde la consola de comprobantes."
                    )
                    if (
                        payload.can_disclose_cfdi_conflict
                        or conflict.empleado_id == payload.empleado_id
                    ):
                        message = conflict.message()
                    raise SolicitudValidationError(
                        "duplicate_cfdi",
                        message + " Confirme explícitamente que es una factura compartida para continuar.",
                    )
                await validate_shared_cfdi_payment_amount(
                    session,
                    cfdi_report=matched,
                    requested_amount=payload.monto_solicitado,
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
        client_submission_id=payload.client_submission_id,
        cfdi_report_id=cfdi_report_id,
        is_supplier_advance=payload.is_supplier_advance,
        supplier_advance_due_date=payload.supplier_advance_due_date,
    )
    session.add(documento)
    await session.flush()

    if payload.is_supplier_advance:
        validated_attachments = [
            (raw, mime, name, "supporting")
            for raw, mime, name, _category in validated_attachments
        ]

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


async def _load_cfdi_bytes_from_documento_adjuntos(
    session: AsyncSession,
    documento_id: UUID,
) -> tuple[Optional[bytes], Optional[bytes]]:
    """Load persisted CFDI XML/PDF attachment bytes for one solicitud document."""
    result = await session.execute(
        select(Adjunto.categoria, Adjunto.ruta_archivo)
        .where(
            Adjunto.documento_id == documento_id,
            Adjunto.categoria.in_(["cfdi_xml", "cfdi_pdf"]),
        )
        .order_by(Adjunto.subido_en.asc())
    )
    xml_bytes: Optional[bytes] = None
    pdf_bytes: Optional[bytes] = None
    for categoria, ruta_archivo in result.all():
        if not ruta_archivo:
            continue
        try:
            raw = base64.b64decode(str(ruta_archivo).encode("ascii"))
        except Exception:
            continue
        if categoria == "cfdi_xml" and xml_bytes is None:
            xml_bytes = raw
        elif categoria == "cfdi_pdf" and pdf_bytes is None:
            pdf_bytes = raw
    return xml_bytes, pdf_bytes


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

    if documento.id is not None:
        persisted_xml, persisted_pdf = await _load_cfdi_bytes_from_documento_adjuntos(
            session, documento.id
        )
        xml_bytes, pdf_bytes = merge_cfdi_upload_bytes(
            xml_bytes=xml_bytes,
            pdf_bytes=pdf_bytes,
            extra_xml_bytes=persisted_xml,
            extra_pdf_bytes=persisted_pdf,
        )

    if not xml_bytes and not pdf_bytes:
        return

    try:
        ingestion = await ingest_cfdi_from_upload(
            session,
            xml_bytes=xml_bytes,
            pdf_bytes=pdf_bytes,
            source="user_upload",
            entity=documento,
            numero_referencia=numero_referencia,
            allow_shared=bool(documento.cfdi_compartido_confirmado),
            require_shared_confirmation=True,
        )
        if ingestion is not None and documento.cfdi_compartido_confirmado:
            await validate_shared_cfdi_payment_amount(
                session,
                cfdi_report=ingestion.cfdi_report,
                requested_amount=documento.monto_solicitado,
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
        validate_solicitud_terceros_attachment(attachment) for attachment in attachments
    ]
    numero_referencia = documento.numero_referencia or str(documento.id)
    if any(
        category in {"cfdi_xml", "cfdi_pdf"}
        for _, _, _, category in validated_attachments
    ):
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


async def _validate_late_invoice_evidence(
    session: AsyncSession,
    documento: Documento,
    validated: list[tuple[bytes, str, str, str]],
) -> Optional[str]:
    """Validate late fiscal evidence without linking or rewriting recognized facts."""
    fiscal = [item for item in validated if item[3] in {"cfdi_xml", "cfdi_pdf"}]
    if not fiscal:
        return None
    if any(
        sum(item[3] == category for item in fiscal) > 1
        for category in {"cfdi_xml", "cfdi_pdf"}
    ):
        raise SolicitudValidationError(
            "multiple_invoices",
            "Adjunte una factura por carga, con su XML y PDF por separado.",
        )
    identities = []
    parsed_records = []
    for raw, _, _, category in fiscal:
        resolved, error = resolve_cfdi_upload(
            **{"xml_bytes" if category == "cfdi_xml" else "pdf_bytes": raw}
        )
        if error or resolved is None:
            raise SolicitudValidationError(
                "invalid_cfdi", error or "No se pudo validar la factura."
            )
        data = resolved.parsed
        data["_xml_evidence"] = bool(resolved.xml_text)
        try:
            fiscal_uuid = normalize_cfdi_uuid_to_canonical(data.get("cfdi_uuid"))
        except ValueError as exc:
            raise SolicitudValidationError(
                "invalid_cfdi", "La factura debe contener un UUID válido."
            ) from exc
        await lock_cfdi_identity(session, fiscal_uuid)
        invoice_kind = data.get("tipo_de_comprobante")
        if category == "cfdi_pdf" and not resolved.xml_text:
            # Text-only PDF extraction cannot establish invoice type. Its XML
            # counterpart must already exist, or be supplied in this batch.
            xml_candidates = [item[0] for item in fiscal if item[3] == "cfdi_xml"]
            xml_rows = (
                (
                    await session.execute(
                        select(Adjunto.ruta_archivo).where(
                            Adjunto.documento_id == documento.id,
                            Adjunto.categoria.in_(["cfdi_xml", "cfdi_xml_evidence"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            xml_candidates.extend(base64.b64decode(value) for value in xml_rows)
            for candidate in xml_candidates:
                xml_resolved, xml_error = resolve_cfdi_upload(xml_bytes=candidate)
                if (
                    not xml_error
                    and xml_resolved
                    and normalize_cfdi_uuid_to_canonical(
                        xml_resolved.parsed.get("cfdi_uuid")
                    )
                    == fiscal_uuid
                ):
                    try:
                        validate_cfdi_material_identity(
                            SimpleNamespace(**xml_resolved.parsed),
                            data,
                            xml_evidence=False,
                        )
                    except CFDIConflictError as exc:
                        raise SolicitudValidationError(
                            "invoice_pair_mismatch",
                            "El PDF contradice los datos fiscales de su XML.",
                        ) from exc
                    # Text PDF defaults/derived values are not fiscal evidence.
                    # The matched XML supplies all mandatory missing fields.
                    data = {**xml_resolved.parsed, "_xml_evidence": False}
                    invoice_kind = data.get("tipo_de_comprobante")
                    break
            if not invoice_kind:
                raise SolicitudValidationError(
                    "invoice_xml_required",
                    "Adjunte primero el XML timbrado de esta factura para validar su PDF.",
                )
        if invoice_kind != "I":
            raise SolicitudValidationError(
                "invalid_invoice", "Se requiere una factura de ingreso timbrada."
            )
        provider = await session.get(ProveedorCliente, documento.proveedor_cliente_id)
        issuer = str(data.get("emisor_rfc") or "").strip().upper()
        if not provider or not provider.rfc or issuer != provider.rfc.strip().upper():
            raise SolicitudValidationError(
                "issuer_mismatch",
                "El RFC emisor debe coincidir con el proveedor de la solicitud.",
            )
        receiver = str(data.get("receptor_rfc") or "").strip().upper()
        receivers = (
            (
                await session.execute(
                    select(RFCConfig.tax_id).where(RFCConfig.active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        if not receiver or receiver not in {str(r).strip().upper() for r in receivers}:
            raise SolicitudValidationError(
                "receiver_mismatch",
                "El RFC receptor debe corresponder a una razón social activa configurada.",
            )
        currency = str(data.get("moneda") or "").upper()
        if currency != (documento.currency or "MXN").upper():
            raise SolicitudValidationError(
                "currency_mismatch",
                "La moneda de la factura debe coincidir con la solicitud.",
            )
        total = _cfdi_money(data.get("total"), field="total")
        amount = _cfdi_money(documento.monto_solicitado, field="monto_solicitado")
        shared = bool(documento.cfdi_compartido_confirmado)
        conflict = await find_blocking_cfdi_evidence(
            session, fiscal_uuid, exclude_documento_id=documento.id
        )
        if conflict and not shared:
            raise SolicitudValidationError(
                "duplicate_cfdi",
                "La factura ya está reservada como evidencia en otra solicitud.",
            )
        if (
            total <= 0
            or amount <= 0
            or (not shared and abs(total - amount) > Decimal("0.01"))
            or (shared and amount > total)
        ):
            raise SolicitudValidationError(
                "amount_mismatch",
                "El importe de la factura no corresponde a la solicitud; requiere revisión explícita.",
            )
        current_uuid = getattr(documento, "cfdi_uuid_manual", None)
        if (
            current_uuid
            and normalize_cfdi_uuid_to_canonical(current_uuid) != fiscal_uuid
        ):
            raise SolicitudValidationError(
                "invoice_immutable",
                "No se puede sustituir la factura de una solicitud avanzada.",
            )
        report = await find_cfdi_report_by_fiscal_uuid(session, fiscal_uuid)
        if report is not None:
            try:
                validate_cfdi_material_identity(
                    report, data, xml_evidence=data["_xml_evidence"]
                )
            except CFDIConflictError as exc:
                raise SolicitudValidationError(
                    "cfdi_conflict", "El UUID existe con datos fiscales diferentes."
                ) from exc
        if documento.cfdi_report_id:
            current_report = await session.get(CFDIReport, documento.cfdi_report_id)
            if (
                current_report is None
                or normalize_cfdi_uuid_to_canonical(current_report.cfdi_uuid)
                != fiscal_uuid
            ):
                raise SolicitudValidationError(
                    "invoice_immutable",
                    "No se puede sustituir el CFDI vinculado a la solicitud.",
                )
        if report is not None:
            if (
                any(
                    str(getattr(report, field, None) or "").strip().upper() != value
                    for field, value in (
                        ("emisor_rfc", issuer),
                        ("receptor_rfc", receiver),
                        ("moneda", currency),
                    )
                )
                or _cfdi_money(report.total, field="total existente") != total
            ):
                raise SolicitudValidationError(
                    "cfdi_conflict", "El UUID existe con datos fiscales diferentes."
                )
        if report is not None and report.id != documento.cfdi_report_id:
            conflict = await find_blocking_cfdi_usage(
                session, report.id, exclude_documento_id=documento.id
            )
            if conflict and not shared:
                raise SolicitudValidationError(
                    "duplicate_cfdi",
                    "La factura ya está reservada en otro gasto o solicitud.",
                )
        if shared:
            await validate_shared_cfdi_payment_amount(
                session,
                cfdi_report=report or CFDIReport(cfdi_uuid=fiscal_uuid, total=total),
                requested_amount=amount,
                exclude_documento_id=documento.id,
            )
        identities.append((fiscal_uuid, issuer, receiver, total, currency))
        parsed_records.append(data)
    if len(set(identities)) != 1:
        raise SolicitudValidationError(
            "invoice_pair_mismatch",
            "El XML y el PDF deben corresponder a la misma factura.",
        )
    try:
        for data in parsed_records[1:]:
            validate_cfdi_material_identity(
                SimpleNamespace(**parsed_records[0]),
                data,
                xml_evidence=bool(
                    data["_xml_evidence"] and parsed_records[0]["_xml_evidence"]
                ),
            )
    except CFDIConflictError as exc:
        raise SolicitudValidationError(
            "invoice_pair_mismatch",
            "El XML y el PDF contienen datos fiscales diferentes.",
        ) from exc
    # Preserve the first late invoice identity, including files uploaded in separate calls.
    previous = (
        (
            await session.execute(
                select(Adjunto).where(
                    Adjunto.documento_id == documento.id,
                    Adjunto.categoria.in_(["cfdi_xml_evidence", "cfdi_pdf_evidence"]),
                )
            )
        )
        .scalars()
        .all()
    )
    for attachment in previous:
        raw = base64.b64decode(attachment.ruta_archivo)
        key = (
            "xml_bytes" if attachment.categoria == "cfdi_xml_evidence" else "pdf_bytes"
        )
        resolved, error = resolve_cfdi_upload(**{key: raw})
        if error or resolved is None:
            raise SolicitudValidationError(
                "invoice_evidence_invalid",
                "La evidencia fiscal anterior requiere revisión.",
            )
        data = resolved.parsed
        try:
            validate_cfdi_material_identity(
                SimpleNamespace(**data),
                parsed_records[0],
                xml_evidence=bool(
                    resolved.xml_text and parsed_records[0]["_xml_evidence"]
                ),
            )
        except CFDIConflictError as exc:
            raise SolicitudValidationError(
                "invoice_immutable",
                "La evidencia fiscal anterior contiene datos diferentes; requiere revisión contable.",
            ) from exc
        previous_uuid = normalize_cfdi_uuid_to_canonical(data.get("cfdi_uuid"))
        if not resolved.xml_text and previous_uuid == identities[0][0]:
            # Explicit PDF fields were compared above; inferred parser defaults
            # cannot establish a different fiscal identity on a later retry.
            continue
        identity = (
            previous_uuid,
            str(data.get("emisor_rfc") or "").strip().upper(),
            str(data.get("receptor_rfc") or "").strip().upper(),
            _cfdi_money(data.get("total"), field="total"),
            str(data.get("moneda") or "").upper(),
        )
        if identity != identities[0]:
            raise SolicitudValidationError(
                "invoice_immutable",
                "No se puede sustituir la factura posterior; requiere revisión contable explícita.",
            )
    return identities[0][0]


async def add_solicitud_documento_adjuntos(
    session: AsyncSession,
    *,
    documento: Documento,
    attachments: list[SolicitudTercerosAttachment],
    commit: bool = True,
    evidence_only: bool = False,
    actor_id: Optional[UUID] = None,
) -> int:
    """Append validated attachments to an existing SOLICITUD document."""
    # Never let another caller silently re-link a classified/authorized request.
    evidence_only = evidence_only or (
        (getattr(documento, "estado", None) or "")
        not in {"borrador", "control_presupuestal"}
        or bool(getattr(documento, "budget_concept_id", None))
    )
    if getattr(documento, "is_supplier_advance", False) or getattr(
        documento, "supplier_advance_id", None
    ):
        if any((a.categoria or "").strip().lower() in {"cfdi_xml", "cfdi_pdf"} for a in attachments):
            raise SolicitudValidationError(
                "advance_invoice_immutable",
                "Registre cada factura desde Comprobar anticipo; no sustituya la evidencia fiscal de un movimiento existente.",
            )
    if not attachments:
        return 0
    if evidence_only or actor_id is not None:
        validated = [validate_solicitud_terceros_attachment(a) for a in attachments]
        evidence_uuid = None
        if evidence_only:
            if (
                any(
                    category in {"cfdi_xml", "cfdi_pdf"}
                    for _, _, _, category in validated
                )
                and actor_id is None
            ):
                raise SolicitudValidationError(
                    "evidence_actor_required",
                    "La carga fiscal posterior requiere un actor auditado.",
                )
            evidence_uuid = await _validate_late_invoice_evidence(
                session, documento, validated
            )
        elif any(
            category in {"cfdi_xml", "cfdi_pdf"} for _, _, _, category in validated
        ):
            await _ingest_solicitud_cfdi_from_attachments(
                session,
                documento=documento,
                validated_attachments=validated,
                numero_referencia=documento.numero_referencia or str(documento.id),
            )
        existing = (
            (
                await session.execute(
                    select(Adjunto).where(Adjunto.documento_id == documento.id)
                )
            )
            .scalars()
            .all()
        )
        known = {(a.categoria, a.ruta_archivo) for a in existing}
        count = 0
        for raw, mime, filename, category in validated:
            stored_category = (
                category + "_evidence"
                if evidence_only and category in {"cfdi_xml", "cfdi_pdf"}
                else category
            )
            encoded = base64.b64encode(raw).decode("ascii")
            if (stored_category, encoded) in known:
                continue
            await create_adjunto_record(
                session,
                documento_id=documento.id,
                ruta_archivo=encoded,
                tipo_archivo=mime,
                mime_type=mime,
                categoria=stored_category,
                origen="document_upload",
                nombre_archivo=filename,
            )
            known.add((stored_category, encoded))
            count += 1
        if count and actor_id is not None:
            session.add(
                Aprobacion(
                    tipo_entidad="documento",
                    entidad_id=documento.id,
                    aprobador_id=actor_id,
                    accion="adjuntar_soporte",
                    comentario=f"{count} archivo(s) anexado(s)."
                    + (
                        " Evidencia pendiente de revisión contable; sin cambios al pago o contabilidad."
                        if evidence_only
                        else ""
                    ),
                )
            )
            if evidence_uuid:
                session.add(
                    Aprobacion(
                        tipo_entidad="documento",
                        entidad_id=documento.id,
                        aprobador_id=actor_id,
                        accion="adjuntar_factura",
                        comentario=evidence_uuid,
                    )
                )
        if commit:
            await session.commit()
        return count
    await _persist_solicitud_terceros_adjuntos(
        session,
        documento=documento,
        attachments=attachments,
    )
    if commit:
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
    if categoria in {"cfdi_xml", "cfdi_pdf"}:
        document = await session.get(Documento, documento_id)
        if document is not None and getattr(document, "supplier_advance_id", None):
            raise SolicitudValidationError(
                "advance_invoice_immutable",
                "La evidencia fiscal vinculada requiere un ajuste contable autorizado antes de sustituirse.",
            )
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
    """Update an editable SOLICITUD a terceros before budget concept assignment.

    Rejected solicitudes return to borrador when saved so the owner can re-send.
    """
    # Match support upload's document -> UUID order and validate current state
    # while holding the document lock, before assigning any fiscal link.
    locked_id = await session.scalar(
        select(Documento.id).where(Documento.id == documento.id).with_for_update()
    )
    if locked_id is None:
        raise SolicitudValidationError("invalid_documento", "Solicitud no encontrada.")
    await session.refresh(documento, ["estado", "budget_concept_id", "cfdi_report_id"])
    if getattr(documento, "supplier_advance_id", None):
        raise SolicitudValidationError(
            "advance_invoice_immutable",
            "La comprobación vinculada no admite edición. Rechace o cancele el movimiento y registre una nueva comprobación.",
        )
    if getattr(documento, "is_supplier_advance", False):
        from .supplier_advance_service import require_supplier_advance_mxn

        require_supplier_advance_mxn(payload)
        payload.is_supplier_advance = True
        payload.supplier_advance_due_date = documento.supplier_advance_due_date
        if (
            payload.cfdi_uuid_manual
            or payload.cfdi_compartido_confirmado
            or any(a.categoria == "cfdi_xml" for a in payload.attachments)
        ):
            raise SolicitudValidationError(
                "advance_invoice_not_allowed",
                "Use Comprobar anticipo después del pago.",
            )
        payload.attachments = [
            SolicitudTercerosAttachment(
                raw_bytes=a.raw_bytes,
                filename=a.filename,
                mime_type=a.mime_type,
                categoria="supporting",
            )
            for a in payload.attachments
        ]
        if payload.pdf_bytes and not any(
            a.raw_bytes == payload.pdf_bytes for a in payload.attachments
        ):
            payload.attachments.append(
                SolicitudTercerosAttachment(
                    raw_bytes=payload.pdf_bytes,
                    filename=payload.pdf_filename or "soporte.pdf",
                    mime_type="application/pdf",
                    categoria="supporting",
                )
            )
        payload.pdf_bytes = None
    if documento.tipo != "SOLICITUD":
        raise SolicitudValidationError(
            "invalid_documento",
            "Solo se pueden editar solicitudes.",
        )
    estado_norm = (documento.estado or "").strip().lower()
    editable_before_budget = (
        estado_norm == "borrador"
        or estado_norm == "control_presupuestal"
    ) and not getattr(documento, "budget_concept_id", None)
    editable_rejected = estado_norm == "rechazado"
    if not editable_before_budget:
        if not editable_rejected:
            raise SolicitudValidationError(
                "invalid_estado",
                "Solo se pueden editar solicitudes antes de que Control Presupuestal asigne concepto.",
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
                ProveedorCliente.tipo != "empleado",
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
        concept = None
        if payload.budget_concept_id is not None:
            concept = await resolve_budget_concept(
                session,
                budget_concept_id=str(payload.budget_concept_id),
                tournament_id=str(payload.torneo_id),
                tournament_code=None,
                fase=payload.fase,
                budget_direction="expense",
            )
            if concept is None:
                raise SolicitudValidationError(
                    "invalid_budget_concept",
                    "El concepto no corresponde al torneo seleccionado.",
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

    if payload.cfdi_uuid_manual:
        await lock_cfdi_identity(session, payload.cfdi_uuid_manual)
        matched_cfdi = await find_cfdi_report_by_fiscal_uuid(
            session, payload.cfdi_uuid_manual
        )
        if matched_cfdi is not None:
            if documento.cfdi_report_id != matched_cfdi.id:
                owns_evidence = await session.scalar(
                    select(Aprobacion.id)
                    .where(
                        Aprobacion.tipo_entidad == "documento",
                        Aprobacion.entidad_id == documento.id,
                        Aprobacion.accion == "adjuntar_factura",
                    )
                    .limit(1)
                )
                if owns_evidence:
                    raise SolicitudValidationError(
                        "duplicate_cfdi",
                        "La evidencia posterior requiere revisión contable "
                        "antes de vincularla.",
                    )
                reservation = await find_blocking_cfdi_evidence(
                    session, payload.cfdi_uuid_manual
                )
                if reservation:
                    if not payload.cfdi_compartido_confirmado:
                        raise SolicitudValidationError(
                            "duplicate_cfdi",
                            "El UUID tiene evidencia reservada; "
                            "confirme una factura compartida.",
                        )
                    await validate_shared_cfdi_payment_amount(
                        session,
                        cfdi_report=matched_cfdi,
                        requested_amount=payload.monto_solicitado,
                        exclude_documento_id=documento.id,
                    )
            documento.cfdi_report_id = matched_cfdi.id
        documento.cfdi_uuid_manual = payload.cfdi_uuid_manual

    if documento.cfdi_report_id and payload.cfdi_compartido_confirmado:
        cfdi_report = await session.get(CFDIReport, documento.cfdi_report_id)
        if cfdi_report is None:
            raise SolicitudValidationError(
                "invalid_cfdi_amount",
                "No se encontró la factura compartida vinculada a la solicitud.",
            )
        await validate_shared_cfdi_payment_amount(
            session,
            cfdi_report=cfdi_report,
            requested_amount=payload.monto_solicitado,
            exclude_documento_id=documento.id,
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
        documento.budget_concept_id = None

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

    if cuenta.estado == "cerrada" and not payload.allow_closed_cuenta:
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
        budget_direction="expense",
    )
    if payload.budget_concept_id is not None and concept is None:
        raise SolicitudValidationError(
            "invalid_budget_concept",
            "El concepto no corresponde al torneo del informe.",
        )

    cuenta_provider_beneficiary_id = getattr(
        cuenta, "beneficiario_proveedor_cliente_id", None
    )
    cuenta_employee_beneficiary_id = getattr(cuenta, "beneficiario_empleado_id", None)
    is_operator_beneficiary = (
        cuenta_provider_beneficiary_id is not None
        and cuenta_employee_beneficiary_id is None
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

    if is_operator_beneficiary and (
        selected_proveedor is None
        or selected_proveedor.id != cuenta_provider_beneficiary_id
    ):
        raise SolicitudValidationError(
            "invalid_proveedor",
            "Seleccione la cuenta bancaria del operador regional beneficiario.",
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
        beneficiario_empleado_id=(
            None
            if is_operator_beneficiary
            else (cuenta_employee_beneficiary_id or payload.empleado_id)
        ),
        proveedor_cliente_id=(
            selected_proveedor.id if selected_proveedor is not None else None
        ),
        beneficiario_proveedor_cliente_id=cuenta_provider_beneficiary_id,
        beneficiario_alterno_tipo=getattr(cuenta, "beneficiario_alterno_tipo", None),
        cuenta_gastos_id=cuenta.id,
        budget_concept_id=payload.budget_concept_id,
        referencia_base=cuenta.referencia_base,
        torneo_id=getattr(cuenta, "torneo_id", None),
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

    subject_ids_by_doc: Dict[UUID, UUID] = {}
    for doc in documentos:
        if doc.id in latest_by_doc:
            continue
        subject_id = approval_subject_empleado_id(doc)
        if subject_id:
            subject_ids_by_doc[doc.id] = subject_id

    assigned_by_subject: Dict[UUID, str] = {}
    if subject_ids_by_doc:
        employees_result = await session.execute(
            select(Empleado)
            .options(selectinload(Empleado.aprobador))
            .where(Empleado.id.in_(set(subject_ids_by_doc.values())))
        )
        for empleado in employees_result.scalars().all():
            assigned = empleado.aprobador
            if assigned and assigned.nombre:
                assigned_by_subject[empleado.id] = assigned.nombre

    display_by_doc: Dict[UUID, str] = {}
    for doc in documentos:
        if doc.id in latest_by_doc:
            display_by_doc[doc.id] = latest_by_doc[doc.id]
            continue
        subject_id = subject_ids_by_doc.get(doc.id)
        display_by_doc[doc.id] = assigned_by_subject.get(subject_id, "—")
    return display_by_doc
