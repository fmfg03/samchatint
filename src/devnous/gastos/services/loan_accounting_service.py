from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from devnous.gastos.models import (
    AccountingPoliza,
    CuentaContable,
    Empleado,
    PrestamoAbono,
    ProveedorCliente,
    SolicitudPrestamo,
)
from devnous.gastos.services.employee_debtor_accounting_service import (
    DebtorPostingResult,
    _create_poliza,
    _debtor_account_match_score,
    _event_poliza_number,
    resolve_default_bank_account,
    resolve_employee_debtor_account,
)
from devnous.gastos.services.loan_request_service import (
    PRESTAMO_BENEFICIARIO_EMPLEADO,
    PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL,
    PRESTAMO_BENEFICIARIO_PROPIO,
    PRESTAMO_BENEFICIARIO_PROVEEDOR,
    PRESTAMO_DEUDORES_EMPLEADOS_PREFIX,
    PRESTAMO_DEUDORES_PROVEEDORES_PREFIX,
    PRESTAMO_DEUDORES_DIRECTORES_PREFIX,
    PRESTAMO_STATUS_PAGADA,
    PRESTAMO_STATUS_LIQUIDADA,
    PRESTAMO_ABONO_STATUS_APROBADO,
    PrestamoWorkflowPermissionError,
    PrestamoWorkflowValidationError,
)
from devnous.gastos.services.payment_run_service import (
    can_confirm_payment_run_payment,
)


MONEY_QUANT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )


def _beneficiario_nombre(prestamo: SolicitudPrestamo) -> str:
    snapshot = str(
        getattr(prestamo, "beneficiario_nombre_snapshot", "") or ""
    ).strip()
    if snapshot:
        return snapshot
    empleado = getattr(prestamo, "beneficiario_empleado", None)
    if empleado is not None and getattr(empleado, "nombre", None):
        return str(empleado.nombre)
    proveedor = getattr(prestamo, "beneficiario_proveedor_cliente", None)
    if proveedor is not None and getattr(proveedor, "nombre", None):
        return str(proveedor.nombre)
    solicitante = getattr(prestamo, "solicitante", None)
    if solicitante is not None and getattr(solicitante, "nombre", None):
        return str(solicitante.nombre)
    return "Beneficiario prestamo"


def _loan_debtor_prefixes(prestamo: SolicitudPrestamo) -> tuple[str, ...]:
    if prestamo.beneficiario_tipo == PRESTAMO_BENEFICIARIO_PROVEEDOR:
        return (f"{PRESTAMO_DEUDORES_PROVEEDORES_PREFIX}-",)
    if prestamo.beneficiario_tipo == PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL:
        return (f"{PRESTAMO_DEUDORES_EMPLEADOS_PREFIX}-",)
    empleado = (
        getattr(prestamo, "beneficiario_empleado", None)
        or getattr(prestamo, "solicitante", None)
    )
    name = str(
        getattr(empleado, "nombre", "")
        or getattr(prestamo, "beneficiario_nombre_snapshot", "")
        or ""
    ).lower()
    if any(
        marker in name
        for marker in (
            "federico gonzalez",
            "jose odilon",
            "odilon trujillo",
            "luis angel",
        )
    ):
        return (
            f"{PRESTAMO_DEUDORES_DIRECTORES_PREFIX}-",
            f"{PRESTAMO_DEUDORES_EMPLEADOS_PREFIX}-",
        )
    return (f"{PRESTAMO_DEUDORES_EMPLEADOS_PREFIX}-",)


def is_valid_prestamo_debtor_account(
    prestamo: SolicitudPrestamo,
    account: CuentaContable,
) -> bool:
    if account is None or not getattr(account, "activo", True):
        return False
    code = str(getattr(account, "codigo", "") or "")
    return any(
        code.startswith(prefix)
        for prefix in _loan_debtor_prefixes(prestamo)
    )


def assign_prestamo_debtor_account(
    prestamo: SolicitudPrestamo,
    actor: Any,
    account: CuentaContable,
) -> SolicitudPrestamo:
    if not can_confirm_payment_run_payment(actor):
        raise PrestamoWorkflowPermissionError(
            "not_accounting",
            "Solo Contabilidad puede asignar la cuenta de deudor.",
        )
    if not is_valid_prestamo_debtor_account(prestamo, account):
        raise PrestamoWorkflowValidationError(
            "invalid_debtor_account",
            "La cuenta seleccionada no corresponde al bloque de deudores del "
            "beneficiario.",
        )
    prestamo.cuenta_deudor_contable_id = account.id
    prestamo.cuenta_deudor_contable = account
    return prestamo


async def _existing_poliza(
    session: AsyncSession,
    *,
    origen: str,
    numero_poliza: str,
) -> Optional[AccountingPoliza]:
    result = await session.execute(
        select(AccountingPoliza)
        .options(selectinload(AccountingPoliza.lines))
        .where(
            AccountingPoliza.origen == origen,
            AccountingPoliza.numero_poliza == numero_poliza,
        )
    )
    return result.scalar_one_or_none()


async def resolve_prestamo_debtor_account(
    session: AsyncSession,
    prestamo: SolicitudPrestamo,
) -> Optional[CuentaContable]:
    configured = getattr(prestamo, "cuenta_deudor_contable", None)
    if configured is not None and is_valid_prestamo_debtor_account(
        prestamo,
        configured,
    ):
        return configured
    if getattr(prestamo, "cuenta_deudor_contable_id", None):
        configured = await session.get(
            CuentaContable,
            prestamo.cuenta_deudor_contable_id,
        )
        if configured is not None and is_valid_prestamo_debtor_account(
            prestamo,
            configured,
        ):
            return configured
    if prestamo.beneficiario_tipo in {
        PRESTAMO_BENEFICIARIO_PROPIO,
        PRESTAMO_BENEFICIARIO_EMPLEADO,
    }:
        empleado = (
            getattr(prestamo, "beneficiario_empleado", None)
            or getattr(prestamo, "solicitante", None)
        )
        if empleado is None:
            empleado_id = (
                getattr(prestamo, "beneficiario_empleado_id", None)
                or getattr(prestamo, "solicitante_empleado_id", None)
            )
            if empleado_id:
                empleado = await session.get(Empleado, empleado_id)
        if empleado is not None:
            debtor = await resolve_employee_debtor_account(session, empleado)
            if debtor is not None and is_valid_prestamo_debtor_account(
                prestamo,
                debtor,
            ):
                return debtor
    if (
        prestamo.beneficiario_tipo
        in {PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL, PRESTAMO_BENEFICIARIO_PROVEEDOR}
        and getattr(prestamo, "beneficiario_proveedor_cliente", None) is None
        and getattr(prestamo, "beneficiario_proveedor_cliente_id", None)
    ):
        proveedor = await session.get(
            ProveedorCliente,
            prestamo.beneficiario_proveedor_cliente_id,
        )
        if proveedor is not None:
            prestamo.beneficiario_proveedor_cliente = proveedor
    beneficiary_name = _beneficiario_nombre(prestamo)
    prefixes = _loan_debtor_prefixes(prestamo)
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True),
            or_(
                *(
                    CuentaContable.codigo.like(f"{prefix}%")
                    for prefix in prefixes
                )
            ),
        )
        .order_by(CuentaContable.codigo.asc())
    )
    candidates = list(result.scalars().all())
    scored = [
        (
            _debtor_account_match_score(beneficiary_name, account.nombre),
            account,
        )
        for account in candidates
    ]
    scored = [(score, account) for score, account in scored if score > 0]
    if not scored:
        return None
    best = max(score for score, _account in scored)
    matches = [account for score, account in scored if score == best]
    return matches[0] if len(matches) == 1 else None


async def autofill_prestamo_debtor_account(
    session: AsyncSession,
    prestamo: SolicitudPrestamo,
) -> Optional[CuentaContable]:
    debtor = await resolve_prestamo_debtor_account(session, prestamo)
    if debtor is None:
        return None
    prestamo.cuenta_deudor_contable_id = debtor.id
    prestamo.cuenta_deudor_contable = debtor
    return debtor


def _loan_meta(prestamo: SolicitudPrestamo, event: str) -> dict[str, Any]:
    return {
        "origin": event,
        "prestamo_id": str(prestamo.id),
        "numero_referencia": prestamo.numero_referencia,
        "beneficiario_tipo": prestamo.beneficiario_tipo,
    }


async def ensure_prestamo_payment_posting(
    session: AsyncSession,
    *,
    prestamo: SolicitudPrestamo,
) -> DebtorPostingResult:
    if prestamo.estado != PRESTAMO_STATUS_PAGADA:
        return DebtorPostingResult(status="skipped", reason="loan_not_paid")
    numero_poliza = _event_poliza_number("PREST-PAY", prestamo.id)
    existing = await _existing_poliza(
        session,
        origen="prestamo_pago",
        numero_poliza=numero_poliza,
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)
    debtor = await resolve_prestamo_debtor_account(session, prestamo)
    if debtor is None:
        return DebtorPostingResult(
            status="pending",
            reason="missing_loan_debtor_account",
        )
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(
            status="pending",
            reason="missing_santander_account",
        )
    amount = _money(prestamo.monto_solicitado)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    concepto = f"Prestamo pagado - {prestamo.numero_referencia or prestamo.id}"
    meta = _loan_meta(prestamo, "prestamo_pago")
    poliza = await _create_poliza(
        session,
        origen="prestamo_pago",
        numero_poliza=numero_poliza,
        fecha=prestamo.pagado_en or datetime.utcnow(),
        beneficiario_nombre=_beneficiario_nombre(prestamo),
        concepto=concepto,
        lines=[
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_deudor"},
            },
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_banco"},
            },
        ],
    )
    prestamo.cuenta_deudor_contable_id = debtor.id
    prestamo.banco_cuenta_contable_id = bank.id
    return DebtorPostingResult(
        status="created",
        poliza=poliza,
        debtor_account=debtor,
        bank_account=bank,
    )


async def ensure_prestamo_abono_posting(
    session: AsyncSession,
    *,
    abono: PrestamoAbono,
) -> DebtorPostingResult:
    prestamo = getattr(abono, "prestamo", None)
    if prestamo is None:
        return DebtorPostingResult(status="pending", reason="missing_loan")
    if abono.estado != PRESTAMO_ABONO_STATUS_APROBADO:
        return DebtorPostingResult(
            status="skipped",
            reason="abono_not_approved",
        )
    if prestamo.estado not in {
        PRESTAMO_STATUS_PAGADA,
        PRESTAMO_STATUS_LIQUIDADA,
    }:
        return DebtorPostingResult(
            status="skipped",
            reason="loan_not_repayable",
        )
    numero_poliza = _event_poliza_number("PREST-ABN", abono.id)
    existing = await _existing_poliza(
        session,
        origen="prestamo_abono",
        numero_poliza=numero_poliza,
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)
    debtor = await resolve_prestamo_debtor_account(session, prestamo)
    if debtor is None:
        return DebtorPostingResult(
            status="pending",
            reason="missing_loan_debtor_account",
        )
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(
            status="pending",
            reason="missing_santander_account",
        )
    amount = _money(abono.monto_aplicado)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    concepto = (
        f"Abono a prestamo - {prestamo.numero_referencia or prestamo.id}"
    )
    meta = {
        **_loan_meta(prestamo, "prestamo_abono"),
        "abono_id": str(abono.id),
    }
    poliza = await _create_poliza(
        session,
        origen="prestamo_abono",
        numero_poliza=numero_poliza,
        fecha=abono.aprobado_en or datetime.utcnow(),
        beneficiario_nombre=_beneficiario_nombre(prestamo),
        concepto=concepto,
        lines=[
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_banco"},
            },
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_deudor"},
            },
        ],
    )
    prestamo.cuenta_deudor_contable_id = debtor.id
    prestamo.banco_cuenta_contable_id = bank.id
    return DebtorPostingResult(
        status="created",
        poliza=poliza,
        debtor_account=debtor,
        bank_account=bank,
    )
