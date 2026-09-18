"""Superadmin-only, auditable release of duplicate CFDI links."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Aprobacion,
    CFDIDuplicateReleaseOperation,
    CFDIDuplicateReleaseOperationItem,
    CFDIReport,
    Documento,
    ExpenseReport,
)
from .cfdi_expense_link_service import normalize_cfdi_uuid_to_canonical
from .customer_success_audit import is_superadmin_role
from .customer_success_audit import record_customer_success_audit_event


class CFDIDuplicateReleaseError(ValueError):
    pass


PROTECTED_DOCUMENT_STATES = {"aprobado", "en_proceso_pago", "pagado"}
RELEASABLE_DOCUMENT_STATES = {"borrador", "rechazado", "control_presupuestal", "enviado"}


@dataclass(frozen=True)
class ReleasePreview:
    cfdi_uuid: str
    cfdi_report_id: Optional[UUID]
    eligible: bool
    reason: str
    references: tuple[str, ...]


def normalize_uuid_batch(raw_values: Iterable[str]) -> list[str]:
    values: list[str] = []
    for raw in raw_values:
        for token in str(raw or "").replace("\n", ",").split(","):
            candidate = token.strip()
            if not candidate:
                continue
            try:
                normalized = normalize_cfdi_uuid_to_canonical(candidate)
            except ValueError as exc:
                raise CFDIDuplicateReleaseError(f"UUID CFDI inválido: {candidate}") from exc
            if normalized not in values:
                values.append(normalized)
    if not values:
        raise CFDIDuplicateReleaseError("Captura al menos un UUID CFDI.")
    return values


def selection_hash(uuids: Iterable[str]) -> str:
    return hashlib.sha256("|".join(sorted(uuids)).encode()).hexdigest()


def _release_block_reason(
    documents: Iterable[Documento], expenses: Iterable[ExpenseReport]
) -> Optional[str]:
    """Return the durable workflow/accounting condition that forbids release."""
    states = {str(document.estado or "") for document in documents}
    if states & PROTECTED_DOCUMENT_STATES:
        return "Vinculado a documento con pago, aprobación o reversa formal requerida."
    if states and not states.issubset(RELEASABLE_DOCUMENT_STATES):
        return "Estado documental no elegible para liberación."
    if any(str(expense.coi_estado or "").lower() == "contabilizado" for expense in expenses):
        return "El gasto ya está contabilizado; requiere reversa contable formal."
    return None


async def preview_duplicate_releases(
    session: AsyncSession, uuids: Iterable[str]
) -> list[ReleasePreview]:
    previews: list[ReleasePreview] = []
    for cfdi_uuid in normalize_uuid_batch(uuids):
        report = (
            await session.execute(select(CFDIReport).where(CFDIReport.cfdi_uuid == cfdi_uuid))
        ).scalar_one_or_none()
        if report is None:
            previews.append(ReleasePreview(cfdi_uuid, None, False, "UUID no encontrado.", ()))
            continue
        documents = (
            await session.execute(select(Documento).where(Documento.cfdi_report_id == report.id))
        ).scalars().all()
        expenses = (
            await session.execute(select(ExpenseReport).where(ExpenseReport.cfdi_report_id == report.id))
        ).scalars().all()
        linked_document_ids = {
            document_id
            for expense in expenses
            for document_id in (expense.documento_id, expense.informe_documento_id)
            if document_id
        }
        linked_documents = (
            await session.execute(select(Documento).where(Documento.id.in_(linked_document_ids)))
        ).scalars().all() if linked_document_ids else []
        all_documents = {document.id: document for document in [*documents, *linked_documents]}
        refs = tuple(sorted(str(doc.numero_referencia or doc.id) for doc in all_documents.values()))
        block_reason = _release_block_reason(all_documents.values(), expenses)
        if block_reason:
            previews.append(ReleasePreview(cfdi_uuid, report.id, False, block_reason, refs))
        elif not documents and not expenses:
            previews.append(ReleasePreview(cfdi_uuid, report.id, False, "El UUID no tiene vínculo que liberar.", refs))
        else:
            previews.append(ReleasePreview(cfdi_uuid, report.id, True, "Vínculo duplicado liberable.", refs))
    return previews


async def apply_duplicate_releases(
    session: AsyncSession,
    *,
    actor: Any,
    uuids: Iterable[str],
    motivo: str,
    idempotency_key: UUID,
) -> CFDIDuplicateReleaseOperation:
    if not is_superadmin_role(getattr(actor, "rol", None)):
        raise CFDIDuplicateReleaseError("Solo superadmin puede liberar comprobantes duplicados.")
    normalized = normalize_uuid_batch(uuids)
    reason = str(motivo or "").strip()
    if not reason:
        raise CFDIDuplicateReleaseError("El motivo de liberación es obligatorio.")
    digest = selection_hash(normalized)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"cfdi-release-operation:{actor.id}:{idempotency_key}"},
    )
    existing = (
        await session.execute(
            select(CFDIDuplicateReleaseOperation).where(
                CFDIDuplicateReleaseOperation.actor_empleado_id == actor.id,
                CFDIDuplicateReleaseOperation.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.selection_hash != digest:
            raise CFDIDuplicateReleaseError("La llave de idempotencia no corresponde a esta selección.")
        return existing

    previews = await preview_duplicate_releases(session, normalized)
    blocked = [preview for preview in previews if not preview.eligible]
    if blocked:
        raise CFDIDuplicateReleaseError("No se aplicó ningún cambio: " + "; ".join(f"{row.cfdi_uuid}: {row.reason}" for row in blocked))

    operation = CFDIDuplicateReleaseOperation(
        id=uuid4(), actor_empleado_id=actor.id, idempotency_key=idempotency_key,
        selection_hash=digest, motivo=reason,
    )
    session.add(operation)
    for preview in previews:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"cfdi-reservation:{preview.cfdi_report_id}"})
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"cfdi-release:{preview.cfdi_report_id}"})
        expenses = (
            await session.execute(select(ExpenseReport).where(ExpenseReport.cfdi_report_id == preview.cfdi_report_id).with_for_update())
        ).scalars().all()
        document_ids = [
            document_id
            for expense in expenses
            for document_id in (expense.documento_id, expense.informe_documento_id)
            if document_id
        ]
        documents = (
            await session.execute(
                select(Documento)
                .where(or_(Documento.cfdi_report_id == preview.cfdi_report_id, Documento.id.in_(document_ids or [None])))
                .with_for_update()
            )
        ).scalars().all()
        block_reason = _release_block_reason(documents, expenses)
        if block_reason:
            raise CFDIDuplicateReleaseError(
                f"No se aplicó ningún cambio: {preview.cfdi_uuid}: {block_reason}"
            )
        before = {"documentos": [str(item.id) for item in documents], "gastos": [str(item.id) for item in expenses]}
        for document in documents:
            if document.estado in {"control_presupuestal", "enviado"}:
                document.estado = "cancelado"
            document.cfdi_report_id = None
            document.cfdi_uuid_manual = None
            document.cfdi_compartido_confirmado = False
            session.add(Aprobacion(tipo_entidad="documento", entidad_id=document.id, aprobador_id=actor.id, accion="liberar_cfdi_duplicado", comentario=reason))
        for expense in expenses:
            expense.cfdi_report_id = None
            expense.cfdi_uuid_manual = None
            expense.cfdi_compartido_confirmado = False
            session.add(Aprobacion(tipo_entidad="gasto", entidad_id=expense.id, aprobador_id=actor.id, accion="liberar_cfdi_duplicado", comentario=reason))
        session.add(CFDIDuplicateReleaseOperationItem(id=uuid4(), operation_id=operation.id, cfdi_report_id=preview.cfdi_report_id, cfdi_uuid=preview.cfdi_uuid, resultado="liberado", before_json=before, after_json={"documentos": "desvinculados", "gastos": "desvinculados"}))
    await session.commit()
    await record_customer_success_audit_event(
        session,
        action="cfdi.duplicate_link_released",
        actor_empleado_id=actor.id,
        entity_type="cfdi_duplicate_release_operation",
        entity_id=operation.id,
        summary=f"Liberación de {len(previews)} UUID CFDI duplicado(s)",
        metadata={"operation_id": str(operation.id), "uuids": normalized, "motivo": reason},
        commit=True,
    )
    return operation
