"""
Cuenta de Gastos settlement service (saldar cuenta).

Handles direction-aware reimbursements and devoluciones anchored on a CuentaDeGastos:
- Direction is derived from the live cuenta saldo (not user input).
- Full settlements only: monto must equal abs(saldo_raw) at submit time.
- A comprobante (PDF/JPG/PNG) attachment is required and linked via Adjunto.reembolso_id.
- Concurrency: SELECT ... FOR UPDATE on the cuenta row serializes writers.
- Duplicate active rows are prevented by the partial unique index on reembolsos
  (migration v1.0.24).
- The underlying INFORME documento.estado is deliberately NOT mutated here.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    Reembolso,
)
from ..utils.receipt_bytes import (
    MAX_SOLICITUD_PDF_BYTES,
    create_adjunto_record,
    resolve_media_type,
)
from .amex_expense_service import compute_informe_saldo, employee_paid_sql_condition

FINANCE_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})

ALLOWED_COMPROBANTE_MIME = frozenset(
    {"application/pdf", "image/jpeg", "image/png"}
)

# Reuse the same size ceiling used for SOLICITUD PDFs. Comprobantes are comparable artifacts.
MAX_COMPROBANTE_BYTES = MAX_SOLICITUD_PDF_BYTES

MONEY_QUANT = Decimal("0.01")


class CuentaSettlementError(Exception):
    """Base settlement error with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CuentaSettlementValidationError(CuentaSettlementError):
    """Input or state validation failed (bad monto, missing comprobante, stale saldo, ...)."""


class CuentaSettlementPermissionError(CuentaSettlementError):
    """Actor is not authorized for this settlement direction."""


@dataclass(slots=True)
class CuentaSettlementResult:
    cuenta: CuentaDeGastos
    reembolso: Reembolso
    tipo: str
    saldo_gross_before: float
    saldo_after: float


def _to_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _quantize_money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _parse_fecha(fecha: Optional[str | date | datetime]) -> datetime:
    if isinstance(fecha, datetime):
        return fecha
    if isinstance(fecha, date):
        return datetime.combine(fecha, datetime.min.time())
    if not fecha:
        return datetime.combine(date.today(), datetime.min.time())
    raw = str(fecha).strip()
    if not raw:
        return datetime.combine(date.today(), datetime.min.time())
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise CuentaSettlementValidationError(
        "invalid_fecha_pago",
        "Fecha de pago con formato inválido. Usa YYYY-MM-DD.",
    )


async def _load_actor(session: AsyncSession, actor_id: UUID) -> Optional[Empleado]:
    return await session.get(Empleado, actor_id)


async def _sum_active_gastos(session: AsyncSession, cuenta_id: UUID) -> Decimal:
    result = await session.execute(
        select(func.coalesce(func.sum(ExpenseReport.gasto_cantidad), 0)).where(
            ExpenseReport.cuenta_gastos_id == cuenta_id,
            ExpenseReport.estado_gasto != "cancelado",
            employee_paid_sql_condition(),
        )
    )
    return _quantize_money(result.scalar_one() or 0)


async def _sum_requested_solicitudes(session: AsyncSession, cuenta_id: UUID) -> Decimal:
    result = await session.execute(
        select(func.coalesce(func.sum(Documento.monto_solicitado), 0)).where(
            Documento.cuenta_gastos_id == cuenta_id,
            Documento.tipo == "SOLICITUD",
            Documento.estado == "pagado",
        )
    )
    return _quantize_money(result.scalar_one() or 0)


async def _load_informe_documento(
    session: AsyncSession, cuenta_id: UUID
) -> Optional[Documento]:
    result = await session.execute(
        select(Documento).where(
            Documento.cuenta_gastos_id == cuenta_id,
            Documento.tipo == "INFORME",
        )
    )
    return result.scalars().first()


async def _sum_active_settlements(
    session: AsyncSession, cuenta_id: UUID
) -> tuple[Decimal, int]:
    """Return (total absolute monto of non-cancelled reembolsos, count of active rows)."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(Reembolso.monto), 0),
            func.count(Reembolso.id),
        ).where(
            Reembolso.cuenta_gastos_id == cuenta_id,
            Reembolso.estado != "cancelado",
        )
    )
    row = result.one()
    total = _quantize_money(row[0] or 0)
    count = int(row[1] or 0)
    return total, count


def _derive_tipo_from_saldo_gross(saldo_gross: Decimal) -> str:
    """saldo_gross > 0  -> employee owes company -> 'devolucion'.
    saldo_gross < 0  -> company owes employee -> 'reembolso'."""
    if saldo_gross > 0:
        return "devolucion"
    if saldo_gross < 0:
        return "reembolso"
    raise CuentaSettlementValidationError(
        "saldo_zero",
        "La cuenta ya está saldada. No hay saldo pendiente por liquidar.",
    )


def _check_permission(actor: Empleado, tipo: str, cuenta: CuentaDeGastos) -> None:
    rol = (actor.rol or "").strip().lower()
    if tipo == "reembolso":
        if rol not in FINANCE_ROLES:
            raise CuentaSettlementPermissionError(
                "insufficient_role",
                "Solo finanzas puede registrar un reembolso al empleado.",
            )
        return
    # devolucion
    if actor.id == cuenta.empleado_id or rol in FINANCE_ROLES:
        return
    raise CuentaSettlementPermissionError(
        "access_denied",
        "Solo el dueño del informe o finanzas pueden registrar una devolución.",
    )


def _validate_comprobante(
    *,
    comprobante_bytes: Optional[bytes],
    comprobante_mime: Optional[str],
    comprobante_filename: Optional[str],
) -> tuple[bytes, str, str]:
    if not comprobante_bytes:
        raise CuentaSettlementValidationError(
            "missing_comprobante",
            "Adjunta el comprobante de la transferencia (PDF, JPG o PNG).",
        )
    if len(comprobante_bytes) > MAX_COMPROBANTE_BYTES:
        raise CuentaSettlementValidationError(
            "comprobante_too_large",
            "El comprobante excede el tamaño máximo permitido.",
        )
    resolved_mime = (comprobante_mime or "").strip().lower() or resolve_media_type(
        comprobante_filename, comprobante_bytes
    )
    if resolved_mime not in ALLOWED_COMPROBANTE_MIME:
        # Sniff from bytes as a second chance.
        sniffed = resolve_media_type(comprobante_filename, comprobante_bytes)
        if sniffed in ALLOWED_COMPROBANTE_MIME:
            resolved_mime = sniffed
        else:
            raise CuentaSettlementValidationError(
                "invalid_comprobante_mime",
                "El comprobante debe ser PDF, JPG o PNG.",
            )
    safe_filename = (comprobante_filename or "comprobante")[:500]
    if "." not in safe_filename:
        if resolved_mime == "application/pdf":
            safe_filename += ".pdf"
        elif resolved_mime == "image/jpeg":
            safe_filename += ".jpg"
        elif resolved_mime == "image/png":
            safe_filename += ".png"
    return comprobante_bytes, resolved_mime, safe_filename


async def register_cuenta_settlement(
    session: AsyncSession,
    *,
    cuenta_id: UUID | str,
    actor_id: UUID | str,
    monto: float | str | Decimal,
    moneda: str = "MXN",
    metodo_pago: Optional[str] = None,
    fecha_pago: Optional[str | date | datetime] = None,
    referencia_pago: Optional[str] = None,
    notas: Optional[str] = None,
    comprobante_bytes: Optional[bytes] = None,
    comprobante_filename: Optional[str] = None,
    comprobante_mime: Optional[str] = None,
    saldo_snapshot: Optional[float | str | Decimal] = None,
) -> CuentaSettlementResult:
    """
    Register a full-settlement reimbursement / devolution on a CuentaDeGastos.

    Raises CuentaSettlementValidationError / CuentaSettlementPermissionError.
    """
    cuenta_uuid = _to_uuid(cuenta_id)
    actor_uuid = _to_uuid(actor_id)

    actor = await _load_actor(session, actor_uuid)
    if actor is None:
        raise CuentaSettlementValidationError("actor_not_found", "Usuario no encontrado.")

    # Lock the cuenta row to serialize concurrent settlement attempts.
    locked = await session.execute(
        select(CuentaDeGastos)
        .where(CuentaDeGastos.id == cuenta_uuid)
        .with_for_update()
    )
    cuenta = locked.scalar_one_or_none()
    if cuenta is None:
        raise CuentaSettlementValidationError(
            "cuenta_not_found", "Informe de gastos no encontrado."
        )

    informe_doc = await _load_informe_documento(session, cuenta_uuid)
    if informe_doc is None:
        raise CuentaSettlementValidationError(
            "missing_informe_documento",
            "La cuenta de gastos no tiene un documento informe vinculado.",
        )

    total_gastos = await _sum_active_gastos(session, cuenta_uuid)
    total_solicitado = await _sum_requested_solicitudes(session, cuenta_uuid)
    settled_total, active_count = await _sum_active_settlements(session, cuenta_uuid)

    saldo_breakdown = compute_informe_saldo(
        employee_paid=float(total_gastos),
        monto_entregado=float(total_solicitado),
        settled_amount=float(settled_total),
    )
    saldo_gross = _quantize_money(saldo_breakdown.saldo_gross)
    saldo_raw = _quantize_money(saldo_breakdown.saldo)

    if active_count > 0:
        raise CuentaSettlementValidationError(
            "active_settlement_exists",
            "Ya existe una liquidación activa para esta cuenta. Cancélala antes de registrar otra.",
        )

    tipo = _derive_tipo_from_saldo_gross(saldo_raw)
    _check_permission(actor, tipo, cuenta)

    expected_abs = abs(saldo_raw)
    try:
        monto_decimal = _quantize_money(monto)
    except (ValueError, ArithmeticError) as exc:
        raise CuentaSettlementValidationError(
            "invalid_monto", "El monto ingresado no es válido."
        ) from exc
    if monto_decimal <= 0:
        raise CuentaSettlementValidationError(
            "invalid_monto", "El monto debe ser mayor a cero."
        )
    if monto_decimal != expected_abs:
        raise CuentaSettlementValidationError(
            "saldo_changed",
            (
                "El saldo cambió desde que cargaste la forma. "
                f"Saldo actual: {expected_abs}. Vuelve a abrir la pantalla para continuar."
            ),
        )

    if saldo_snapshot is not None:
        try:
            snapshot_decimal = _quantize_money(saldo_snapshot)
        except (ValueError, ArithmeticError):
            snapshot_decimal = None
        if snapshot_decimal is not None and snapshot_decimal != expected_abs:
            raise CuentaSettlementValidationError(
                "saldo_changed",
                "El saldo cambió. Refresca la pantalla antes de registrar la liquidación.",
            )

    raw_bytes, resolved_mime, safe_filename = _validate_comprobante(
        comprobante_bytes=comprobante_bytes,
        comprobante_mime=comprobante_mime,
        comprobante_filename=comprobante_filename,
    )

    fecha_pago_dt = _parse_fecha(fecha_pago)

    moneda_clean = (moneda or "MXN").strip().upper() or "MXN"
    metodo_clean = (metodo_pago or "").strip() or None
    referencia_clean = (referencia_pago or "").strip() or None
    notas_clean = (notas or "").strip() or None

    reembolso = Reembolso(
        empleado_id=cuenta.empleado_id,
        documento_id=informe_doc.id,
        cuenta_gastos_id=cuenta.id,
        pagador_empleado_id=actor.id,
        tipo=tipo,
        monto=monto_decimal,
        moneda=moneda_clean,
        metodo_pago=metodo_clean,
        fecha_pago=fecha_pago_dt,
        referencia_pago=referencia_clean,
        notas=notas_clean,
        estado="pagado",
        creado_en=datetime.utcnow(),
    )
    session.add(reembolso)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise CuentaSettlementValidationError(
            "active_settlement_exists",
            "Otra liquidación activa acaba de ser registrada. Refresca la pantalla.",
        ) from exc

    comprobante_b64 = base64.b64encode(raw_bytes).decode("ascii")
    await create_adjunto_record(
        session,
        reembolso_id=reembolso.id,
        ruta_archivo=comprobante_b64,
        tipo_archivo=resolved_mime,
        nombre_archivo=safe_filename,
        mime_type=resolved_mime,
        categoria="comprobante_reembolso",
        origen="user_upload",
    )

    await session.commit()
    await session.refresh(reembolso)
    await session.refresh(cuenta)

    saldo_after = Decimal("0.00")
    return CuentaSettlementResult(
        cuenta=cuenta,
        reembolso=reembolso,
        tipo=tipo,
        saldo_gross_before=float(saldo_gross),
        saldo_after=float(saldo_after),
    )


async def cancel_cuenta_settlement(
    session: AsyncSession,
    *,
    cuenta_id: UUID | str,
    reembolso_id: UUID | str,
    actor_id: UUID | str,
    motivo: str,
) -> Reembolso:
    """
    Cancel an existing reembolso/devolucion (finanzas / admin only). Adjunto stays attached.
    """
    cuenta_uuid = _to_uuid(cuenta_id)
    reembolso_uuid = _to_uuid(reembolso_id)
    actor_uuid = _to_uuid(actor_id)

    actor = await _load_actor(session, actor_uuid)
    if actor is None:
        raise CuentaSettlementValidationError("actor_not_found", "Usuario no encontrado.")
    if (actor.rol or "").strip().lower() not in FINANCE_ROLES:
        raise CuentaSettlementPermissionError(
            "insufficient_role",
            "Solo finanzas puede cancelar una liquidación.",
        )

    motivo_clean = (motivo or "").strip()
    if not motivo_clean:
        raise CuentaSettlementValidationError(
            "missing_motivo",
            "Debes indicar el motivo de la cancelación.",
        )

    # Lock the cuenta first so readers computing saldo see a consistent state.
    locked_cuenta = await session.execute(
        select(CuentaDeGastos)
        .where(CuentaDeGastos.id == cuenta_uuid)
        .with_for_update()
    )
    cuenta = locked_cuenta.scalar_one_or_none()
    if cuenta is None:
        raise CuentaSettlementValidationError(
            "cuenta_not_found", "Informe de gastos no encontrado."
        )

    result = await session.execute(
        select(Reembolso)
        .where(
            Reembolso.id == reembolso_uuid,
            Reembolso.cuenta_gastos_id == cuenta_uuid,
        )
        .with_for_update()
    )
    reembolso = result.scalar_one_or_none()
    if reembolso is None:
        raise CuentaSettlementValidationError(
            "reembolso_not_found", "Liquidación no encontrada para esta cuenta."
        )
    if reembolso.estado == "cancelado":
        raise CuentaSettlementValidationError(
            "already_cancelled", "Esta liquidación ya está cancelada."
        )

    reembolso.estado = "cancelado"
    reembolso.cancelado_en = datetime.utcnow()
    reembolso.cancelado_por_id = actor.id
    reembolso.motivo_cancelacion = motivo_clean

    await session.commit()
    await session.refresh(reembolso)
    return reembolso


async def compute_cuenta_saldo_adjustments(
    session: AsyncSession,
    cuenta_id: UUID | str,
) -> tuple[float, int]:
    """
    Public helper reused by the cuenta detail UI to compute the absolute total of
    non-cancelled settlements and the count of active rows.
    """
    total, count = await _sum_active_settlements(session, _to_uuid(cuenta_id))
    return float(total), count
