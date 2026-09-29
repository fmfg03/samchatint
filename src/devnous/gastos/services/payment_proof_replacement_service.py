"""Audited replacement of a paid Payment Run document's proof."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Adjunto, Aprobacion, Documento
from .documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
    validate_solicitud_terceros_attachment,
)


async def replace_payment_run_proof(
    session: AsyncSession,
    *,
    documento: Documento,
    previous_id: UUID,
    actor_id: UUID,
    attachment: SolicitudTercerosAttachment,
    reason: str,
) -> Adjunto:
    """Retire one current proof and insert its successor in one transaction.

    The caller checks Contabilidad authority and commits only after this returns.
    This deliberately does not touch payment state, date, amount, or postings.
    """
    if documento.tipo != "SOLICITUD" or documento.estado != "pagado" or not documento.pagado_en:
        raise SolicitudValidationError(
            "not_paid", "Solo se puede sustituir el comprobante de una solicitud pagada."
        )
    if not reason.strip():
        raise SolicitudValidationError(
            "replacement_reason_required", "Indica el motivo de la sustitución."
        )
    result = await session.execute(
        select(Adjunto).where(
            Adjunto.id == previous_id,
            Adjunto.documento_id == documento.id,
            Adjunto.categoria == "comprobante_pago",
            Adjunto.activo.is_(True),
        ).with_for_update()
    )
    previous = result.scalar_one_or_none()
    if previous is None:
        raise SolicitudValidationError(
            "proof_not_current", "El comprobante ya fue sustituido o no pertenece a la solicitud."
        )
    raw, mime, filename, category = validate_solicitud_terceros_attachment(attachment)
    if category != "comprobante_pago":
        raise SolicitudValidationError("invalid_proof", "Archivo de comprobante inválido.")
    now = datetime.now(timezone.utc)
    replacement = Adjunto(
        id=uuid4(),
        documento_id=documento.id,
        ruta_archivo=base64.b64encode(raw).decode("ascii"),
        tipo_archivo=mime,
        mime_type=mime,
        nombre_archivo=filename,
        categoria="comprobante_pago",
        origen="payment_run_replacement",
        activo=True,
    )
    session.add(replacement)
    await session.flush()
    previous.activo = False
    previous.sustituido_en = now
    previous.sustituido_por_adjunto_id = replacement.id
    previous.sustituido_por_empleado_id = actor_id
    session.add(Aprobacion(
        tipo_entidad="documento",
        entidad_id=documento.id,
        aprobador_id=actor_id,
        accion="sustituir_comprobante_pago",
        comentario=(
            f"Motivo: {reason.strip()[:500]}. Adjunto anterior {previous.id} "
            f"({previous.nombre_archivo}); nuevo {replacement.id} ({filename})."
        ),
        fecha=now,
    ))
    return replacement
