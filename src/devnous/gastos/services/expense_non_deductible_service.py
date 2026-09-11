"""Auditable lifecycle for an expense's non-deductible proof."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Adjunto, Aprobacion

NON_DEDUCTIBLE_PROOF_CATEGORY = "comprobante_no_deducible"


class NonDeductibleProofError(ValueError):
    """A proof lifecycle action cannot be completed safely."""


async def get_active_non_deductible_proof(
    session: AsyncSession, gasto_id: UUID
) -> Optional[Adjunto]:
    result = await session.execute(
        select(Adjunto)
        .where(
            Adjunto.gasto_id == gasto_id,
            Adjunto.categoria == NON_DEDUCTIBLE_PROOF_CATEGORY,
            Adjunto.activo.is_(True),
        )
        .order_by(Adjunto.subido_en.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def replace_non_deductible_proof(
    session: AsyncSession,
    *,
    gasto_id: UUID,
    actor_id: UUID,
    ruta_archivo: str,
    mime_type: str,
    nombre_archivo: str,
) -> Adjunto:
    """Insert the new proof and retire the previous current proof atomically."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Adjunto)
        .where(
            Adjunto.gasto_id == gasto_id,
            Adjunto.categoria == NON_DEDUCTIBLE_PROOF_CATEGORY,
            Adjunto.activo.is_(True),
        )
        .with_for_update()
    )
    previous = result.scalar_one_or_none()
    if previous:
        previous.activo = False
        previous.sustituido_en = now
        previous.sustituido_por_empleado_id = actor_id
        await session.flush()
    replacement = Adjunto(
        gasto_id=gasto_id,
        ruta_archivo=ruta_archivo,
        tipo_archivo=mime_type,
        nombre_archivo=nombre_archivo,
        mime_type=mime_type,
        categoria=NON_DEDUCTIBLE_PROOF_CATEGORY,
        origen="user_upload",
        activo=True,
    )
    session.add(replacement)
    await session.flush()
    if previous:
        previous.sustituido_por_adjunto_id = replacement.id
    session.add(
        Aprobacion(
            tipo_entidad="gasto",
            entidad_id=gasto_id,
            aprobador_id=actor_id,
            accion="reemplazar_comprobante_no_deducible" if previous else "adjuntar_comprobante_no_deducible",
            comentario=(
                f"Comprobante no deducible vigente: {nombre_archivo}."
                + (f" Sustituye adjunto {previous.id}." if previous else "")
            ),
            fecha=now,
        )
    )
    return replacement


async def logically_delete_non_deductible_proof(
    session: AsyncSession,
    *,
    gasto_id: UUID,
    actor_id: UUID,
    motivo: str,
) -> Adjunto:
    """Retire the active proof while retaining its file and audit history."""
    reason = (motivo or "").strip()
    if not reason:
        raise NonDeductibleProofError("Indique el motivo para eliminar el comprobante no deducible.")
    proof = await get_active_non_deductible_proof(session, gasto_id)
    if not proof:
        raise NonDeductibleProofError("No hay un comprobante no deducible vigente para eliminar.")
    now = datetime.now(timezone.utc)
    proof.activo = False
    proof.eliminado_en = now
    proof.eliminado_por_id = actor_id
    proof.motivo_eliminacion = reason
    session.add(
        Aprobacion(
            tipo_entidad="gasto",
            entidad_id=gasto_id,
            aprobador_id=actor_id,
            accion="eliminar_comprobante_no_deducible",
            comentario=reason,
            fecha=now,
        )
    )
    return proof
