"""Factual report approval evidence; no financial decision or posting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Adjunto,
    AmexReportSourceEvidence,
    CuentaDeGastos,
    ExpenseReport,
)
from ..utils.receipt_bytes import get_adjunto_columns
from .amex_recognition_service import load_expenses, review_source_key


def build_amex_report_source_snapshot(expenses: list[Any]) -> dict[str, Any]:
    """Hash actual source facts deterministically without guessing fiscal data."""
    partidas = []
    fields = (
        "id",
        "informe_documento_id",
        "cuenta_gastos_id",
        "estado_gasto",
        "origen",
        "pagado_con_amex_empresa",
        "metodo_pago",
        "ultimos_4_digitos",
        "gasto_cantidad",
        "currency",
        "fecha",
        "cfdi_report_id",
        "numero_factura",
        "cfdi_uuid_manual",
        "iva",
        "propina_no_deducible",
        "budget_concept_id",
    )
    for expense in sorted(expenses, key=lambda row: str(row.id)):
        facts = {field: getattr(expense, field, None) for field in fields}
        facts["source_key"] = review_source_key(expense)
        cfdi = getattr(expense, "cfdi_report", None)
        facts["cfdi"] = (
            {
                key: getattr(cfdi, key, None)
                for key in (
                    "id",
                    "cfdi_uuid",
                    "fecha",
                    "subtotal",
                    "descuento",
                    "total",
                    "moneda",
                    "tipo_cambio",
                    "tipo_de_comprobante",
                    "metodo_pago",
                    "total_impuestos_trasladados",
                    "impuestos_detalle",
                    "conceptos",
                )
            }
            if cfdi is not None
            else None
        )
        # Set only by explicit schema-safe loading, never lazy relationship IO.
        facts["attachments"] = vars(expense).get("_amex_evidence_attachments")
        # A legacy receipt's bytes are not copied into immutable evidence.
        receipt = vars(expense).get("archivo_data")
        facts["legacy_receipt_sha256"] = (
            hashlib.sha256(str(receipt).encode()).hexdigest() if receipt else None
        )
        partidas.append(facts)
    encoded = json.dumps(partidas, sort_keys=True, default=str, separators=(",", ":"))
    return {
        "source_key": hashlib.sha256(encoded.encode()).hexdigest(),
        "partidas": json.loads(encoded),
    }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def create_operational_report_evidence(
    session: AsyncSession,
    *,
    informe: Any,
    approval: Any,
    expenses: list[ExpenseReport],
) -> AmexReportSourceEvidence | None:
    """Attest a real final approval inside its transaction, never commit here."""
    if not any(is_amex_report_source(row) for row in expenses):
        return None
    if (
        informe.tipo != "INFORME"
        or informe.estado != "aprobado"
        or approval.tipo_entidad != "documento"
        or approval.accion != "aprobar"
        or approval.entidad_id != informe.id
        or _utc(informe.aprobado_en) != _utc(approval.fecha)
    ):
        raise ValueError("invalid_amex_final_approval_proof")
    existing = (
        await session.execute(
            select(AmexReportSourceEvidence).where(
                AmexReportSourceEvidence.approval_id == approval.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    version = (
        await session.execute(
            select(func.coalesce(func.max(AmexReportSourceEvidence.version), 0)).where(
                AmexReportSourceEvidence.informe_id == informe.id,
                AmexReportSourceEvidence.kind == "operational_approval",
            )
        )
    ).scalar_one()
    snapshot = build_amex_report_source_snapshot(expenses)
    evidence = AmexReportSourceEvidence(
        id=uuid4(),
        informe_id=informe.id,
        kind="operational_approval",
        approval_id=approval.id,
        actor_id=approval.aprobador_id,
        created_at=approval.fecha,
        version=int(version) + 1,
        source_key=snapshot["source_key"],
        snapshot_json=snapshot,
        decisions_json=None,
        request_payload_hash=None,
        reason=None,
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def record_amex_final_report_approval(
    session: AsyncSession,
    *,
    informe: Any,
    approval: Any,
) -> AmexReportSourceEvidence | None:
    """Snapshot all active report facts when the real final approval completes."""
    if informe.tipo != "INFORME" or informe.estado != "aprobado":
        return None
    if informe.cuenta_gastos_id is None:
        return None
    any_amex = (
        await session.execute(
            select(ExpenseReport.id)
            .where(
                ExpenseReport.cuenta_gastos_id == informe.cuenta_gastos_id,
                ExpenseReport.estado_gasto != "cancelado",
                (
                    ExpenseReport.pagado_con_amex_empresa.is_(True)
                    | (
                        ExpenseReport.pagado_con_amex_empresa.is_(None)
                        & (ExpenseReport.origen == "amex_batch")
                    )
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if any_amex is None:
        return None
    await session.execute(
        select(CuentaDeGastos.id)
        .where(CuentaDeGastos.id == informe.cuenta_gastos_id)
        .with_for_update()
    )
    ids = list(
        (
            await session.execute(
                select(ExpenseReport.id)
                .where(
                    ExpenseReport.cuenta_gastos_id == informe.cuenta_gastos_id,
                    ExpenseReport.estado_gasto != "cancelado",
                )
                .order_by(ExpenseReport.id)
            )
        )
        .scalars()
        .all()
    )
    expenses = await load_expenses(session, ids)
    await load_amex_report_attachment_evidence(session, expenses, locked=True)
    return await create_operational_report_evidence(
        session,
        informe=informe,
        approval=approval,
        expenses=expenses,
    )


def is_amex_report_source(expense: Any) -> bool:
    """Preserve the legacy statement fallback without guessing a missing card."""
    return expense.pagado_con_amex_empresa is True or (
        expense.pagado_con_amex_empresa is None
        and getattr(expense, "origen", None) == "amex_batch"
    )


async def load_amex_report_attachment_evidence(
    session: AsyncSession, expenses: list[Any], *, locked: bool = False
) -> None:
    """Explicitly load active attachment identity/hash, not bytes in snapshots."""
    if not expenses:
        return
    available = await get_adjunto_columns(session)
    if not {"id", "gasto_id", "ruta_archivo"}.issubset(available):
        raise ValueError("amex_attachment_evidence_schema_missing")
    names = (
        "id",
        "gasto_id",
        "ruta_archivo",
        "categoria",
        "mime_type",
        "tipo_archivo",
        "nombre_archivo",
        "activo",
    )
    query = (
        select(
            *[
                (
                    getattr(Adjunto, name)
                    if name in available
                    else literal(None).label(name)
                )
                for name in names
            ]
        )
        .where(Adjunto.gasto_id.in_([row.id for row in expenses]))
        .order_by(Adjunto.id)
    )
    if "activo" in available:
        query = query.where(Adjunto.activo.is_(True))
    if locked:
        query = query.with_for_update()
    by_expense: dict[Any, list[dict[str, Any]]] = {row.id: [] for row in expenses}
    for row in (await session.execute(query)).mappings().all():
        facts = {
            key: row[key] for key in names if key not in {"gasto_id", "ruta_archivo"}
        }
        facts["payload_sha256"] = hashlib.sha256(
            str(row["ruta_archivo"]).encode()
        ).hexdigest()
        by_expense[row["gasto_id"]].append(facts)
    for expense in expenses:
        expense._amex_evidence_attachments = by_expense[expense.id]
