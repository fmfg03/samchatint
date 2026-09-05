"""Payment Run operational view service.

This module intentionally does not register document payments. Closing a
payment run captures an accounting-facing operational cutoff only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from devnous.gastos.models import Documento, PaymentRunClosureItem
from devnous.gastos.services.customer_success_audit import (
    is_superadmin_role,
    record_customer_success_audit_event,
)
from devnous.gastos.services.document_amount_service import (
    EMPLOYEE_REIMBURSEMENT_PREFIX,
    resolve_payable_document_amount,
)


PAYMENT_RUN_MANAGER_ENV_KEYS = (
    "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
    "PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
)
PAYMENT_RUN_PAYMENT_CONFIRMER_ENV_KEYS = (
    "SAMCHAT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS",
    "PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS",
)
DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS = frozenset(
    {
        "6380f16d-2b89-491c-8457-c5b80c319a0f",
        "e3d13040-2360-420f-98a1-516440ef63c3",
    }
)
DEFAULT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS = frozenset(
    {
        "6380f16d-2b89-491c-8457-c5b80c319a0f",
        "d87a03c1-7023-4b25-9867-2e2e8301e2aa",
        "11bb2f54-363e-49b0-8e12-12e36b51e84a",
        "2c85b3ca-9f4b-49b7-be2e-2e36ef981479",
    }
)


class PaymentRunPermissionError(Exception):
    """Raised when an empleado cannot use the Payment Run module."""

    message = "No tienes facultad para consultar o cerrar Payment Run."


class PaymentRunPaymentPermissionError(PaymentRunPermissionError):
    """Raised when an empleado cannot confirm a payment-run payment."""

    message = "Solo Contabilidad puede marcar solicitudes como pagadas."


class PaymentRunValidationError(Exception):
    """Raised when a requested Payment Run operation is invalid."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PaymentRunCloseResult:
    closure_id: str
    item_count: int
    total_amount: Decimal
    references: list[str]


def _parse_manager_ids_from_env() -> set[str]:
    return _parse_ids_from_env(PAYMENT_RUN_MANAGER_ENV_KEYS)


def _parse_payment_confirmer_ids_from_env() -> set[str]:
    return _parse_ids_from_env(PAYMENT_RUN_PAYMENT_CONFIRMER_ENV_KEYS)


def _parse_ids_from_env(keys: Iterable[str]) -> set[str]:
    ids: set[str] = set()
    for key in keys:
        raw = os.getenv(key, "")
        for item in raw.replace(";", ",").split(","):
            normalized = item.strip().lower()
            if normalized:
                ids.add(normalized)
    return ids


def configured_payment_run_manager_ids(
    extra_ids: Optional[Iterable[Any]] = None,
) -> set[str]:
    ids = set(DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS)
    ids.update(_parse_manager_ids_from_env())
    for item in extra_ids or []:
        normalized = str(item or "").strip().lower()
        if normalized:
            ids.add(normalized)
    return ids


def configured_payment_run_payment_confirmer_ids(
    extra_ids: Optional[Iterable[Any]] = None,
) -> set[str]:
    ids = set(DEFAULT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS)
    ids.update(_parse_payment_confirmer_ids_from_env())
    for item in extra_ids or []:
        normalized = str(item or "").strip().lower()
        if normalized:
            ids.add(normalized)
    return ids


def can_manage_payment_run(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> bool:
    if is_superadmin_role(getattr(empleado, "rol", None)):
        return True
    empleado_id = str(getattr(empleado, "id", "") or "").strip().lower()
    if not empleado_id:
        return False
    return empleado_id in configured_payment_run_manager_ids(allowed_ids)


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _employee_permissions(empleado: Any) -> set[str]:
    raw = getattr(empleado, "effective_permissions", None)
    if raw is None:
        raw = getattr(empleado, "permissions", None)
    if raw is None:
        return set()
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    try:
        return {str(item or "").strip().lower() for item in raw if str(item or "").strip()}
    except TypeError:
        return set()


def _has_any_permission(empleado: Any, *permissions: str) -> bool:
    employee_permissions = _employee_permissions(empleado)
    if "*" in employee_permissions or "admin.*" in employee_permissions:
        return True
    for permission in permissions:
        required = permission.strip().lower()
        if required in employee_permissions:
            return True
        parts = required.split(".")
        for i in range(len(parts), 0, -1):
            if ".".join(parts[:i]) + ".*" in employee_permissions:
                return True
    return False


def can_confirm_payment_run_payment(empleado: Any) -> bool:
    """Only Contabilidad can attach proof and mark Payment Run items as paid."""
    if is_superadmin_role(getattr(empleado, "rol", None)):
        return True
    empleado_id = str(getattr(empleado, "id", "") or "").strip().lower()
    if empleado_id in configured_payment_run_payment_confirmer_ids():
        return True
    if _has_any_permission(
        empleado,
        "contabilidad.pagos.marcar_pagado",
        "accounting.payments.mark_paid",
        "admin.contabilidad.manage",
        "contabilidad.manage",
    ):
        return True
    rol = _normalized_text(getattr(empleado, "rol", ""))
    departamento = _normalized_text(getattr(empleado, "departamento", ""))
    return rol in {"contabilidad", "contador", "conta", "accounting"} or departamento in {
        "contabilidad",
        "conta",
        "accounting",
    }


def can_access_payment_run(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> bool:
    """Allow managers to cut runs, Finance to view, and Accounting to pay."""
    if can_manage_payment_run(empleado, allowed_ids=allowed_ids):
        return True
    if can_confirm_payment_run_payment(empleado):
        return True
    rol = _normalized_text(getattr(empleado, "rol", ""))
    departamento = _normalized_text(getattr(empleado, "departamento", ""))
    return rol in {"finanzas", "admin", "superadmin", "super_admin"} or departamento == "finanzas"


def require_payment_run_payment_confirmation(empleado: Any) -> None:
    if not can_confirm_payment_run_payment(empleado):
        raise PaymentRunPaymentPermissionError()


def require_payment_run_access(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> None:
    if not can_access_payment_run(empleado, allowed_ids=allowed_ids):
        raise PaymentRunPermissionError()


def require_payment_run_manager(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> None:
    if not can_manage_payment_run(empleado, allowed_ids=allowed_ids):
        raise PaymentRunPermissionError()


def _parse_uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def parse_payment_run_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise PaymentRunValidationError("Captura fecha_pago.")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise PaymentRunValidationError("fecha_pago no es valida.") from exc


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _document_amount(documento: Documento) -> Decimal:
    amount = resolve_payable_document_amount(documento)
    if amount is None:
        reference = documento.numero_referencia or str(documento.id)
        raise PaymentRunValidationError(
            f"{reference} es un reembolso sin monto_total; "
            "requiere conciliacion."
        )
    return amount


async def ensure_payment_run_schema(session: AsyncSession) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS payment_run_closures (
            id UUID PRIMARY KEY,
            status VARCHAR(20) NOT NULL DEFAULT 'closed',
            run_date DATE NULL,
            total_amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT NULL,
            closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_by_empleado_id UUID NULL REFERENCES empleados(id)
                ON DELETE SET NULL,
            metadata JSONB NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS payment_run_closure_items (
            id UUID PRIMARY KEY,
            closure_id UUID NOT NULL REFERENCES payment_run_closures(id)
                ON DELETE CASCADE,
            documento_id UUID NOT NULL REFERENCES documentos(id)
                ON DELETE RESTRICT,
            numero_referencia VARCHAR(200) NULL,
            fecha_pago DATE NULL,
            monto NUMERIC(18, 2) NOT NULL DEFAULT 0,
            currency VARCHAR(3) NOT NULL DEFAULT 'MXN',
            estado_documento VARCHAR(50) NULL,
            snapshot JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        (
            "CREATE INDEX IF NOT EXISTS ix_payment_run_closures_closed_at "
            "ON payment_run_closures(closed_at DESC)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_payment_run_closure_items_closure "
            "ON payment_run_closure_items(closure_id)"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ux_payment_run_closure_items_documento "
            "ON payment_run_closure_items(documento_id)"
        ),
    ]
    for statement in statements:
        await session.execute(text(statement))


def _status_for_row(
    row: dict[str, Any],
    *,
    today: Optional[date] = None,
) -> str:
    estado = str(row.get("estado") or "").lower()
    if row.get("pagado_en") or estado == "pagado":
        return "pagada"
    if estado == "en_proceso_pago" or row.get("closure_id"):
        return "en proceso de pago"
    fecha_pago = row.get("fecha_pago")
    today = today or date.today()
    if isinstance(fecha_pago, date) and fecha_pago < today:
        return "vencida"
    return "programada"


async def list_payment_run_items(
    session: AsyncSession,
    *,
    status_filter: str = "pendientes",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    query: Optional[str] = None,
    limit: int = 250,
) -> list[dict[str, Any]]:
    await ensure_payment_run_schema(session)
    filters = ["d.tipo = 'SOLICITUD'"]
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 250), 500))}
    normalized_status = (status_filter or "pendientes").strip().lower()
    if normalized_status in {"pendientes", "abiertas"}:
        filters.append("d.estado = 'aprobado'")
        filters.append("d.pagado_en IS NULL")
        filters.append("ci.documento_id IS NULL")
    elif normalized_status in {"cerradas", "en_proceso", "en_proceso_pago"}:
        filters.append("d.estado = 'en_proceso_pago'")
        filters.append("d.pagado_en IS NULL")
    elif normalized_status == "pagadas":
        filters.append("(d.estado = 'pagado' OR d.pagado_en IS NOT NULL)")
    elif normalized_status != "todas":
        filters.append("d.estado = 'aprobado'")
        filters.append("d.pagado_en IS NULL")
        filters.append("ci.documento_id IS NULL")

    if date_from:
        filters.append("d.fecha_pago >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("d.fecha_pago <= :date_to")
        params["date_to"] = date_to
    if query:
        filters.append(
            """
            (
                d.numero_referencia ILIKE :query
                OR d.concepto_pago ILIKE :query
                OR CAST(d.referencia_operaciones AS TEXT) ILIKE :query
                OR e.nombre ILIKE :query
                OR b.nombre ILIKE :query
                OR pc.nombre ILIKE :query
            )
            """
        )
        params["query"] = f"%{query.strip()}%"

    result = await session.execute(
        text(
            f"""
            WITH closure_items AS (
                SELECT DISTINCT ON (i.documento_id)
                    i.documento_id,
                    i.closure_id,
                    c.closed_at,
                    c.run_date
                FROM payment_run_closure_items i
                JOIN payment_run_closures c ON c.id = i.closure_id
                ORDER BY i.documento_id, c.closed_at DESC
            )
            SELECT
                d.id,
                d.numero_referencia,
                d.referencia_operaciones,
                d.estado,
                d.fecha_pago,
                d.aprobado_en,
                d.pagado_en,
                d.pago_urgente,
                d.concepto_pago,
                d.currency,
                CASE
                    WHEN LOWER(TRIM(COALESCE(d.concepto_pago, '')))
                         LIKE :reimbursement_prefix
                    THEN d.monto_total
                    ELSE COALESCE(d.monto_total, d.monto_solicitado, 0)
                END AS monto,
                e.nombre AS solicitante_nombre,
                b.nombre AS beneficiario_nombre,
                pc.nombre AS proveedor_nombre,
                ci.closure_id,
                ci.closed_at,
                ci.run_date
            FROM documentos d
            JOIN empleados e ON e.id = d.empleado_id
            LEFT JOIN empleados b ON b.id = d.beneficiario_empleado_id
            LEFT JOIN proveedores_clientes pc ON pc.id = d.proveedor_cliente_id
            LEFT JOIN closure_items ci ON ci.documento_id = d.id
            WHERE {" AND ".join(filters)}
            ORDER BY
                d.fecha_pago NULLS LAST,
                d.aprobado_en NULLS LAST,
                d.creado_en DESC
            LIMIT :limit
            """
        ),
        {
            **params,
            "reimbursement_prefix": f"{EMPLOYEE_REIMBURSEMENT_PREFIX}%",
        },
    )
    rows = []
    for mapping in result.mappings().all():
        row = dict(mapping)
        row["status"] = _status_for_row(row)
        row["can_edit_fecha_pago"] = row["status"] in {"programada", "vencida"}
        row["can_close"] = row["status"] in {"programada", "vencida"}
        row["can_upload_payment_proof"] = row["status"] == "en proceso de pago"
        row["amount_issue"] = (
            "Reembolso sin monto_total; requiere conciliacion."
            if row.get("monto") is None
            else None
        )
        if row["amount_issue"]:
            row["can_close"] = False
        row["monto"] = _money(row.get("monto"))
        rows.append(row)
    return rows


async def list_payment_run_closures(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    await ensure_payment_run_schema(session)
    result = await session.execute(
        text(
            """
            SELECT
                c.id,
                c.status,
                c.run_date,
                c.total_amount,
                c.item_count,
                c.closed_at,
                c.notes,
                e.nombre AS closed_by_nombre
            FROM payment_run_closures c
            LEFT JOIN empleados e ON e.id = c.closed_by_empleado_id
            ORDER BY c.closed_at DESC
            LIMIT :limit
            """
        ),
        {"limit": max(1, min(int(limit or 20), 100))},
    )
    rows = []
    for mapping in result.mappings().all():
        row = dict(mapping)
        row["total_amount"] = _money(row.get("total_amount"))
        rows.append(row)
    return rows


async def update_payment_run_fecha_pago(
    session: AsyncSession,
    *,
    documento_id: Any,
    fecha_pago: Any,
    actor_id: Any,
    request: Optional[Any] = None,
) -> Documento:
    await ensure_payment_run_schema(session)
    parsed_id = _parse_uuid(documento_id)
    parsed_fecha = parse_payment_run_date(fecha_pago)
    documento = await session.get(Documento, parsed_id)
    if not documento:
        raise PaymentRunValidationError("SOLICITUD no encontrada.")
    if documento.tipo != "SOLICITUD":
        raise PaymentRunValidationError("Solo se puede editar SOLICITUD.")
    if documento.estado != "aprobado" or documento.pagado_en is not None:
        raise PaymentRunValidationError(
            "Solo se edita fecha_pago en solicitudes aprobadas pendientes."
        )

    closed = await session.scalar(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM payment_run_closure_items
                WHERE documento_id = :documento_id
            )
            """
        ),
        {"documento_id": str(parsed_id)},
    )
    if closed:
        raise PaymentRunValidationError(
            "La solicitud ya esta cerrada en un corte."
        )

    before_fecha = documento.fecha_pago
    documento.fecha_pago = parsed_fecha
    await record_customer_success_audit_event(
        session,
        action="payment_run.fecha_pago_updated",
        actor_empleado_id=actor_id,
        documento_id=documento.id,
        documento_referencia=documento.numero_referencia,
        entity_type="documento",
        entity_id=documento.id,
        request=request,
        summary=(
            "Payment Run fecha_pago actualizada para "
            f"{documento.numero_referencia}"
        ),
        metadata={
            "before_fecha_pago": (
                before_fecha.isoformat() if before_fecha else None
            ),
            "after_fecha_pago": parsed_fecha.isoformat(),
        },
    )
    await session.commit()
    return documento


async def close_payment_run(
    session: AsyncSession,
    *,
    document_ids: Iterable[Any],
    actor_id: Any,
    notes: Optional[str] = None,
    run_date: Optional[Any] = None,
    request: Optional[Any] = None,
) -> PaymentRunCloseResult:
    await ensure_payment_run_schema(session)
    parsed_ids = [
        _parse_uuid(item)
        for item in document_ids
        if str(item or "").strip()
    ]
    if not parsed_ids:
        raise PaymentRunValidationError("Selecciona al menos una SOLICITUD.")

    result = await session.execute(
        select(Documento)
        .options(
            selectinload(Documento.empleado),
            selectinload(Documento.proveedor_cliente),
            selectinload(Documento.beneficiario_empleado),
        )
        .where(Documento.id.in_(parsed_ids))
    )
    documentos = list(result.scalars().all())
    found_ids = {doc.id for doc in documentos}
    missing = [str(item) for item in parsed_ids if item not in found_ids]
    if missing:
        raise PaymentRunValidationError(
            f"SOLICITUD no encontrada: {missing[0]}"
        )

    closed_result = await session.execute(
        select(PaymentRunClosureItem.documento_id).where(
            PaymentRunClosureItem.documento_id.in_(parsed_ids)
        )
    )
    already_closed = {str(row[0]) for row in closed_result.fetchall()}
    if already_closed:
        raise PaymentRunValidationError(
            "Hay solicitudes ya cerradas: "
            f"{', '.join(sorted(already_closed)[:3])}"
        )

    for documento in documentos:
        if documento.tipo != "SOLICITUD":
            raise PaymentRunValidationError(
                "Solo se pueden cerrar SOLICITUDES."
            )
        if documento.estado != "aprobado" or documento.pagado_en is not None:
            ref = documento.numero_referencia or str(documento.id)
            raise PaymentRunValidationError(
                f"{ref} no esta aprobada pendiente de pago."
            )

    closure_id = uuid4()
    parsed_run_date = parse_payment_run_date(run_date) if run_date else None
    total = sum((_document_amount(doc) for doc in documentos), Decimal("0.00"))
    references = [str(doc.numero_referencia or doc.id) for doc in documentos]
    await session.execute(
        text(
            """
            INSERT INTO payment_run_closures (
                id, status, run_date, total_amount, item_count, notes,
                closed_at, closed_by_empleado_id, metadata
            ) VALUES (
                :id, 'closed', :run_date, :total_amount, :item_count, :notes,
                NOW(), :closed_by_empleado_id, CAST(:metadata AS JSONB)
            )
            """
        ),
        {
            "id": str(closure_id),
            "run_date": parsed_run_date,
            "total_amount": str(total),
            "item_count": len(documentos),
            "notes": (notes or "").strip() or None,
            "closed_by_empleado_id": str(actor_id),
            "metadata": json.dumps({"references": references}, default=str),
        },
    )
    for documento in documentos:
        snapshot = {
            "documento_id": str(documento.id),
            "numero_referencia": documento.numero_referencia,
            "estado": documento.estado,
            "fecha_pago": (
                documento.fecha_pago.isoformat()
                if documento.fecha_pago
                else None
            ),
            "monto": str(_document_amount(documento)),
            "currency": documento.currency or "MXN",
            "solicitante": getattr(documento.empleado, "nombre", None),
            "beneficiario": getattr(
                documento.beneficiario_empleado,
                "nombre",
                None,
            ),
            "proveedor": getattr(documento.proveedor_cliente, "nombre", None),
        }
        documento.estado = "en_proceso_pago"
        session.add(documento)
        await session.execute(
            text(
                """
                INSERT INTO payment_run_closure_items (
                    id, closure_id, documento_id, numero_referencia,
                    fecha_pago,
                    monto, currency, estado_documento, snapshot, created_at
                ) VALUES (
                    :id, :closure_id, :documento_id, :numero_referencia,
                    :fecha_pago, :monto, :currency, :estado_documento,
                    CAST(:snapshot AS JSONB), NOW()
                )
                """
            ),
            {
                "id": str(uuid4()),
                "closure_id": str(closure_id),
                "documento_id": str(documento.id),
                "numero_referencia": documento.numero_referencia,
                "fecha_pago": documento.fecha_pago,
                "monto": str(_document_amount(documento)),
                "currency": documento.currency or "MXN",
                "estado_documento": documento.estado,
                "snapshot": json.dumps(snapshot, default=str),
            },
        )

    await record_customer_success_audit_event(
        session,
        action="payment_run.closed",
        actor_empleado_id=actor_id,
        entity_type="payment_run_closure",
        entity_id=closure_id,
        request=request,
        summary=f"Payment Run cerrado con {len(documentos)} solicitudes.",
        metadata={
            "closure_id": str(closure_id),
            "item_count": len(documentos),
            "total_amount": str(total),
            "references": references,
        },
    )
    await session.commit()
    return PaymentRunCloseResult(
        closure_id=str(closure_id),
        item_count=len(documentos),
        total_amount=total,
        references=references,
    )


async def get_payment_run_closure(
    session: AsyncSession,
    *,
    closure_id: Any,
) -> Optional[dict[str, Any]]:
    await ensure_payment_run_schema(session)
    parsed_id = _parse_uuid(closure_id)
    header_result = await session.execute(
        text(
            """
            SELECT
                c.id,
                c.status,
                c.run_date,
                c.total_amount,
                c.item_count,
                c.closed_at,
                c.notes,
                e.nombre AS closed_by_nombre
            FROM payment_run_closures c
            LEFT JOIN empleados e ON e.id = c.closed_by_empleado_id
            WHERE c.id = :closure_id
            """
        ),
        {"closure_id": str(parsed_id)},
    )
    header = header_result.mappings().first()
    if not header:
        return None

    items_result = await session.execute(
        text(
            """
            SELECT
                i.documento_id,
                i.numero_referencia,
                i.fecha_pago,
                i.monto,
                i.currency,
                i.estado_documento,
                i.snapshot,
                d.pagado_en,
                d.gasto_generado_id
            FROM payment_run_closure_items i
            LEFT JOIN documentos d ON d.id = i.documento_id
            WHERE i.closure_id = :closure_id
            ORDER BY i.fecha_pago NULLS LAST, i.numero_referencia
            """
        ),
        {"closure_id": str(parsed_id)},
    )
    closure = dict(header)
    closure["total_amount"] = _money(closure.get("total_amount"))
    closure["items"] = []
    for mapping in items_result.mappings().all():
        item = dict(mapping)
        item["monto"] = _money(item.get("monto"))
        closure["items"].append(item)
    return closure
