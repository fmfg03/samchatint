from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import PrestamoAbono, SolicitudPrestamo
from devnous.gastos.services.access_control_service import is_superadmin_role


PRESTAMO_STATUS_BORRADOR = "borrador"
PRESTAMO_STATUS_ENVIADA = "enviada"
PRESTAMO_STATUS_CANCELADA = "cancelada"
PRESTAMO_STATUS_APROBADA = "aprobada"
PRESTAMO_STATUS_RECHAZADA = "rechazada"
PRESTAMO_STATUS_EN_PROCESO_PAGO = "en_proceso_de_pago"
PRESTAMO_STATUS_PAGADA = "pagada"
PRESTAMO_STATUS_LIQUIDADA = "liquidada"

PRESTAMO_ABONO_STATUS_ENVIADO = "enviado"
PRESTAMO_ABONO_STATUS_APROBADO = "aprobado"
PRESTAMO_ABONO_STATUS_RECHAZADO = "rechazado"

PRESTAMO_BENEFICIARIO_PROPIO = "propio"
PRESTAMO_BENEFICIARIO_EMPLEADO = "empleado"
PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL = "operador_regional"
PRESTAMO_BENEFICIARIO_PROVEEDOR = "proveedor"

PRESTAMO_BENEFICIARIO_TYPES = frozenset(
    {
        PRESTAMO_BENEFICIARIO_PROPIO,
        PRESTAMO_BENEFICIARIO_EMPLEADO,
        PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL,
        PRESTAMO_BENEFICIARIO_PROVEEDOR,
    }
)
PRESTAMO_EDITABLE_STATUSES = frozenset({PRESTAMO_STATUS_BORRADOR})
PRESTAMO_CANCELABLE_STATUSES = frozenset(
    {PRESTAMO_STATUS_BORRADOR, PRESTAMO_STATUS_ENVIADA}
)

PRESTAMO_SANTANDER_CUENTA_CODIGO = "1120-001-001"
PRESTAMO_SANTANDER_CUENTA_NOMBRE = "BANCO SANTANDER 65506206424"
PRESTAMO_DEUDORES_EMPLEADOS_PREFIX = "1170-001"
PRESTAMO_DEUDORES_DIRECTORES_PREFIX = "1170-002"
PRESTAMO_DEUDORES_PROVEEDORES_PREFIX = "1170-003"

PRESTAMO_VIEW_ALL_ENV_KEYS = (
    "SAMCHAT_PRESTAMO_VIEW_ALL_EMPLOYEE_IDS",
    "PRESTAMO_VIEW_ALL_EMPLOYEE_IDS",
)
PRESTAMO_APPROVER_ENV_KEYS = (
    "SAMCHAT_PRESTAMO_APPROVER_EMPLOYEE_IDS",
    "PRESTAMO_APPROVER_EMPLOYEE_IDS",
)
PRESTAMO_ABONO_APPROVER_ENV_KEYS = (
    "SAMCHAT_PRESTAMO_ABONO_APPROVER_EMPLOYEE_IDS",
    "PRESTAMO_ABONO_APPROVER_EMPLOYEE_IDS",
)

DEFAULT_PRESTAMO_VIEW_ALL_EMPLOYEE_IDS = frozenset(
    {
        "6380f16d-2b89-491c-8457-c5b80c319a0f",  # Benjamin
        "e3d13040-2360-420f-98a1-516440ef63c3",  # Juan Pablo
    }
)
DEFAULT_PRESTAMO_VIEW_ALL_EMAILS = frozenset(
    {
        "bjimenez@plataformasports.com",
        "jlopez@plataformasports.com",
        "otrujillo@plataformasports.com",
        "laorozco@plataformasports.com",
    }
)
DEFAULT_PRESTAMO_VIEW_ALL_NAMES = frozenset(
    {
        "luis angel orozco colin",
        "jose odilon trujillo macedo",
        "federico gonzalez nava",
        "federico gonzalez niembro",
        "benjamin jimenez",
        "juan pablo lopez",
        "juan pablo lopez romero",
    }
)
DEFAULT_PRESTAMO_APPROVER_NAMES = frozenset(
    {
        "federico gonzalez nava",
        "luis angel orozco colin",
    }
)
DEFAULT_PRESTAMO_ABONO_APPROVER_NAMES = frozenset(
    {
        "benjamin jimenez",
        "jaqueline",
        "jacqueline",
        "daniel",
    }
)


class PrestamoWorkflowError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PrestamoWorkflowPermissionError(PrestamoWorkflowError):
    pass


class PrestamoWorkflowValidationError(PrestamoWorkflowError):
    pass


@dataclass(frozen=True)
class PrestamoCreatePayload:
    solicitante_empleado_id: UUID
    beneficiario_tipo: str
    monto_solicitado: Any
    motivo: str
    numero_referencia: str
    beneficiario_empleado_id: Optional[UUID] = None
    beneficiario_proveedor_cliente_id: Optional[UUID] = None
    beneficiario_nombre_snapshot: Optional[str] = None
    banco_beneficiario: Optional[str] = None
    cuenta_beneficiario: Optional[str] = None
    currency: str = "MXN"


@dataclass(frozen=True)
class PrestamoAbonoApplication:
    monto_reportado: Decimal
    monto_aplicado: Decimal
    monto_excedente: Decimal
    saldo_antes: Decimal
    saldo_despues: Decimal
    requires_excess_confirmation: bool


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.strip().lower().split())


def _parse_ids_from_env(keys: Iterable[str]) -> set[str]:
    ids: set[str] = set()
    for key in keys:
        raw = os.getenv(key, "")
        for item in raw.replace(";", ",").split(","):
            normalized = item.strip().lower()
            if normalized:
                ids.add(normalized)
    return ids


def _employee_id(empleado: Any) -> str:
    return str(getattr(empleado, "id", "") or "").strip().lower()


def _employee_email(empleado: Any) -> str:
    return str(getattr(empleado, "correo", "") or "").strip().lower()


def _employee_matches_named_access(
    empleado: Any,
    allowed_names: Iterable[str],
) -> bool:
    normalized = _normalize_text(getattr(empleado, "nombre", ""))
    if not normalized:
        return False
    for allowed_raw in allowed_names:
        allowed = _normalize_text(allowed_raw)
        if (
            normalized == allowed
            or normalized.startswith(f"{allowed} ")
            or allowed.startswith(f"{normalized} ")
        ):
            return True
    return False


def _has_allowed_id(
    empleado: Any,
    *,
    defaults: Iterable[str],
    env_keys: Iterable[str],
    allowed_ids: Optional[Iterable[Any]] = None,
) -> bool:
    empleado_id = _employee_id(empleado)
    if not empleado_id:
        return False
    configured = {
        str(item).strip().lower()
        for item in defaults
        if str(item).strip()
    }
    configured.update(_parse_ids_from_env(env_keys))
    for item in allowed_ids or []:
        normalized = str(item or "").strip().lower()
        if normalized:
            configured.add(normalized)
    return empleado_id in configured


def can_view_all_prestamos(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> bool:
    if empleado is None or getattr(empleado, "activo", True) is False:
        return False
    if is_superadmin_role(getattr(empleado, "rol", None)):
        return True
    if _has_allowed_id(
        empleado,
        defaults=DEFAULT_PRESTAMO_VIEW_ALL_EMPLOYEE_IDS,
        env_keys=PRESTAMO_VIEW_ALL_ENV_KEYS,
        allowed_ids=allowed_ids,
    ):
        return True
    if _employee_email(empleado) in DEFAULT_PRESTAMO_VIEW_ALL_EMAILS:
        return True
    return _employee_matches_named_access(
        empleado,
        DEFAULT_PRESTAMO_VIEW_ALL_NAMES,
    )


def can_view_prestamo(empleado: Any, prestamo: SolicitudPrestamo) -> bool:
    if empleado is None or getattr(empleado, "activo", True) is False:
        return False
    if can_view_all_prestamos(empleado):
        return True
    return _employee_id(empleado) == str(
        getattr(prestamo, "solicitante_empleado_id", "") or ""
    ).lower()


def can_approve_prestamo(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> bool:
    if empleado is None or getattr(empleado, "activo", True) is False:
        return False
    if is_superadmin_role(getattr(empleado, "rol", None)):
        return True
    if _has_allowed_id(
        empleado,
        defaults=(),
        env_keys=PRESTAMO_APPROVER_ENV_KEYS,
        allowed_ids=allowed_ids,
    ):
        return True
    return _employee_matches_named_access(
        empleado,
        DEFAULT_PRESTAMO_APPROVER_NAMES,
    )


def can_approve_prestamo_abono(
    empleado: Any,
    *,
    allowed_ids: Optional[Iterable[Any]] = None,
) -> bool:
    if empleado is None or getattr(empleado, "activo", True) is False:
        return False
    if is_superadmin_role(getattr(empleado, "rol", None)):
        return True
    if _has_allowed_id(
        empleado,
        defaults=(),
        env_keys=PRESTAMO_ABONO_APPROVER_ENV_KEYS,
        allowed_ids=allowed_ids,
    ):
        return True
    return _employee_matches_named_access(
        empleado,
        DEFAULT_PRESTAMO_ABONO_APPROVER_NAMES,
    )


def _money(value: Any, field_name: str = "monto") -> Decimal:
    try:
        amount = Decimal(str(value or "0")).quantize(
            Decimal("0.01"),
            ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError) as exc:
        raise PrestamoWorkflowValidationError(
            "invalid_amount",
            f"{field_name} no es valido.",
        ) from exc
    if amount <= Decimal("0.00"):
        raise PrestamoWorkflowValidationError(
            "invalid_amount",
            f"{field_name} debe ser mayor a cero.",
        )
    return amount


def _clean_required_text(value: Any, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise PrestamoWorkflowValidationError(
            "missing_required_field",
            f"{field_name} es obligatorio.",
        )
    return cleaned


def validate_prestamo_payload(payload: PrestamoCreatePayload) -> Decimal:
    beneficiario_tipo = _normalize_text(payload.beneficiario_tipo).replace(
        " ",
        "_",
    )
    if beneficiario_tipo not in PRESTAMO_BENEFICIARIO_TYPES:
        raise PrestamoWorkflowValidationError(
            "invalid_beneficiary_type",
            "Selecciona un tipo de beneficiario valido.",
        )
    targets = [
        bool(payload.beneficiario_empleado_id),
        bool(payload.beneficiario_proveedor_cliente_id),
    ]
    if beneficiario_tipo == PRESTAMO_BENEFICIARIO_PROPIO and any(targets):
        raise PrestamoWorkflowValidationError(
            "invalid_beneficiary_selection",
            "La solicitud propia no debe tener beneficiario tercero.",
        )
    if (
        beneficiario_tipo == PRESTAMO_BENEFICIARIO_EMPLEADO
        and not payload.beneficiario_empleado_id
    ):
        raise PrestamoWorkflowValidationError(
            "missing_beneficiary",
            "Selecciona el empleado beneficiario.",
        )
    if (
        beneficiario_tipo
        in {
            PRESTAMO_BENEFICIARIO_OPERADOR_REGIONAL,
            PRESTAMO_BENEFICIARIO_PROVEEDOR,
        }
        and not payload.beneficiario_proveedor_cliente_id
    ):
        raise PrestamoWorkflowValidationError(
            "missing_beneficiary",
            "Selecciona el beneficiario registrado.",
        )
    if (
        payload.beneficiario_empleado_id
        and payload.beneficiario_proveedor_cliente_id
    ):
        raise PrestamoWorkflowValidationError(
            "invalid_beneficiary_selection",
            "Selecciona solo un tipo de beneficiario.",
        )
    _clean_required_text(payload.numero_referencia, "numero_referencia")
    _clean_required_text(payload.motivo, "motivo")
    return _money(payload.monto_solicitado, "monto_solicitado")


def build_prestamo_from_payload(
    payload: PrestamoCreatePayload,
) -> SolicitudPrestamo:
    monto = validate_prestamo_payload(payload)
    beneficiario_tipo = _normalize_text(payload.beneficiario_tipo).replace(
        " ",
        "_",
    )
    return SolicitudPrestamo(
        numero_referencia=_clean_required_text(
            payload.numero_referencia,
            "numero_referencia",
        ),
        solicitante_empleado_id=payload.solicitante_empleado_id,
        beneficiario_tipo=beneficiario_tipo,
        beneficiario_empleado_id=payload.beneficiario_empleado_id,
        beneficiario_proveedor_cliente_id=(
            payload.beneficiario_proveedor_cliente_id
        ),
        beneficiario_nombre_snapshot=str(
            payload.beneficiario_nombre_snapshot or ""
        ).strip()
        or None,
        banco_beneficiario=(
            str(payload.banco_beneficiario or "").strip() or None
        ),
        cuenta_beneficiario=(
            str(payload.cuenta_beneficiario or "").strip() or None
        ),
        monto_solicitado=monto,
        saldo_pendiente=monto,
        currency=(str(payload.currency or "MXN").strip().upper() or "MXN")[:3],
        motivo=_clean_required_text(payload.motivo, "motivo"),
        estado=PRESTAMO_STATUS_BORRADOR,
    )


async def create_prestamo(
    session: AsyncSession,
    payload: PrestamoCreatePayload,
) -> SolicitudPrestamo:
    prestamo = build_prestamo_from_payload(payload)
    session.add(prestamo)
    await session.flush()
    return prestamo


def can_edit_prestamo(prestamo: SolicitudPrestamo) -> bool:
    return getattr(prestamo, "estado", None) in PRESTAMO_EDITABLE_STATUSES


def submit_prestamo(
    prestamo: SolicitudPrestamo,
    actor: Any,
    *,
    now: Optional[datetime] = None,
) -> SolicitudPrestamo:
    if _employee_id(actor) != str(prestamo.solicitante_empleado_id).lower():
        raise PrestamoWorkflowPermissionError(
            "not_requester",
            "Solo el solicitante puede enviar esta solicitud.",
        )
    if prestamo.estado != PRESTAMO_STATUS_BORRADOR:
        raise PrestamoWorkflowValidationError(
            "not_editable",
            "La solicitud ya fue enviada y no puede editarse ni reenviarse.",
        )
    prestamo.estado = PRESTAMO_STATUS_ENVIADA
    prestamo.enviado_en = now or datetime.now(timezone.utc)
    return prestamo


def cancel_prestamo(
    prestamo: SolicitudPrestamo,
    actor: Any,
    *,
    now: Optional[datetime] = None,
) -> SolicitudPrestamo:
    if _employee_id(actor) != str(prestamo.solicitante_empleado_id).lower():
        raise PrestamoWorkflowPermissionError(
            "not_requester",
            "Solo el solicitante puede cancelar esta solicitud.",
        )
    if prestamo.estado not in PRESTAMO_CANCELABLE_STATUSES:
        raise PrestamoWorkflowValidationError(
            "not_cancelable",
            "Solo se puede cancelar antes de aprobacion.",
        )
    prestamo.estado = PRESTAMO_STATUS_CANCELADA
    prestamo.cancelado_por_empleado_id = getattr(actor, "id", None)
    prestamo.cancelado_en = now or datetime.now(timezone.utc)
    return prestamo


def compute_abono_application(
    *,
    saldo_pendiente: Any,
    monto_reportado: Any,
    excess_confirmed: bool = False,
) -> PrestamoAbonoApplication:
    saldo = Decimal(str(saldo_pendiente or "0")).quantize(
        Decimal("0.01"),
        ROUND_HALF_UP,
    )
    monto = _money(monto_reportado, "monto_reportado")
    if saldo <= Decimal("0.00"):
        raise PrestamoWorkflowValidationError(
            "loan_already_settled",
            "El prestamo ya no tiene saldo pendiente.",
        )
    if monto > saldo and not excess_confirmed:
        return PrestamoAbonoApplication(
            monto_reportado=monto,
            monto_aplicado=saldo,
            monto_excedente=monto - saldo,
            saldo_antes=saldo,
            saldo_despues=Decimal("0.00"),
            requires_excess_confirmation=True,
        )
    monto_aplicado = min(monto, saldo)
    saldo_despues = max(Decimal("0.00"), saldo - monto_aplicado)
    return PrestamoAbonoApplication(
        monto_reportado=monto,
        monto_aplicado=monto_aplicado,
        monto_excedente=max(Decimal("0.00"), monto - saldo),
        saldo_antes=saldo,
        saldo_despues=saldo_despues,
        requires_excess_confirmation=False,
    )


def build_abono_from_application(
    prestamo: SolicitudPrestamo,
    actor: Any,
    application: PrestamoAbonoApplication,
    *,
    comprobante_filename: Optional[str] = None,
    comprobante_storage_key: Optional[str] = None,
    comentario: Optional[str] = None,
) -> PrestamoAbono:
    return PrestamoAbono(
        prestamo_id=prestamo.id,
        registrado_por_empleado_id=getattr(actor, "id", None),
        monto_reportado=application.monto_reportado,
        monto_aplicado=application.monto_aplicado,
        monto_excedente=application.monto_excedente,
        saldo_antes=application.saldo_antes,
        saldo_despues=application.saldo_despues,
        estado=PRESTAMO_ABONO_STATUS_ENVIADO,
        excedente_confirmado=application.monto_excedente > Decimal("0.00"),
        comprobante_filename=str(comprobante_filename or "").strip() or None,
        comprobante_storage_key=(
            str(comprobante_storage_key or "").strip() or None
        ),
        comentario=str(comentario or "").strip() or None,
    )
