from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.expense_metadata import (
    normalize_categories,
    normalize_currency,
    normalize_edition,
)
from devnous.gastos.models import (
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    Tournament,
)
from devnous.gastos.services.documento_service import (
    SolicitudTercerosAttachment,
    allocate_referencia_operaciones_for_empleado,
    build_solicitud_personal_payload,
    build_solicitud_terceros_payload,
    create_solicitud_personal_document,
    create_solicitud_terceros_document,
)
from devnous.gastos.services.expense_service import create_expense_from_data
from devnous.gastos.services.tournament_phase_service import get_tournament_etapas
from devnous.gastos.services.tournament_project_visibility import (
    visibility_validation_error,
)

ACCOUNT_TYPES = {"local", "viaje", "nacional", "extranjero"}


@dataclass(frozen=True)
class PersonalReceiptWorkflowResult:
    account: CuentaDeGastos
    report_document: Documento
    expense: ExpenseReport
    payment_request: Documento


async def _employee_and_tournament(
    session: AsyncSession,
    *,
    employee_id: UUID,
    tournament_id: UUID,
) -> tuple[Empleado, Tournament]:
    employee = await session.get(Empleado, employee_id)
    if employee is None:
        raise ValueError("Empleado no encontrado.")
    tournament = await session.get(Tournament, tournament_id)
    if tournament is None or not bool(getattr(tournament, "active", True)):
        raise ValueError("El torneo/proyecto no existe o no está activo.")
    visibility_error = visibility_validation_error(tournament, employee)
    if visibility_error:
        raise ValueError(visibility_error)
    return employee, tournament


async def _unique_reference(session: AsyncSession, employee_id: UUID) -> str:
    for _ in range(25):
        candidate = str(100000 + secrets.randbelow(900000))
        exists = (
            await session.execute(
                select(CuentaDeGastos.id).where(
                    CuentaDeGastos.empleado_id == employee_id,
                    CuentaDeGastos.referencia_base == candidate,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    raise ValueError("No se pudo generar una referencia interna única.")


def _required(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, "", [], {}):
        raise ValueError(f"{key} is required")
    return value


def _parse_expense_date(value: Any) -> datetime:
    raw = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, date_format)
        except ValueError:
            continue
    raise ValueError("La fecha del comprobante no tiene un formato valido.")


async def create_personal_receipt_workflow(
    session: AsyncSession,
    *,
    employee_id: UUID,
    payload: Mapping[str, Any],
    commit: bool = True,
) -> PersonalReceiptWorkflowResult:
    amount = float(_required(payload, "amount"))
    if amount <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    concept = str(_required(payload, "concept")).strip()
    expense_date = _parse_expense_date(_required(payload, "date"))
    payment_method = str(_required(payload, "payment_method")).strip()
    tournament_id = UUID(str(_required(payload, "tournament_id")))
    account_type = str(_required(payload, "account_type")).strip().lower()
    if account_type not in ACCOUNT_TYPES:
        raise ValueError("Tipo de cuenta de gastos no válido.")

    employee, tournament = await _employee_and_tournament(
        session,
        employee_id=employee_id,
        tournament_id=tournament_id,
    )
    phase = str(payload.get("phase") or "").strip() or None
    if phase and phase not in get_tournament_etapas(tournament):
        raise ValueError("La fase no corresponde al torneo/proyecto seleccionado.")
    categories = normalize_categories(list(payload.get("categories") or []), tournament)
    currency = normalize_currency(payload.get("currency") or "MXN")
    edition = normalize_edition(payload.get("edition"), default_current_year=True)
    reference = await _unique_reference(session, employee_id)

    try:
        account = CuentaDeGastos(
            empleado_id=employee_id,
            referencia_base=reference,
            nombre=str(payload.get("account_name") or concept).strip() or None,
            estado="abierta",
            tipo_cuenta=account_type,
            torneo_id=tournament_id,
            fase=phase,
            categorias=categories,
            edicion=edition,
            currency=currency,
        )
        session.add(account)
        await session.flush()
        operations_reference = await allocate_referencia_operaciones_for_empleado(
            session, employee
        )
        report_document = Documento(
            empleado_id=employee_id,
            tipo="INFORME",
            numero_referencia=f"I-{reference}",
            estado="borrador",
            referencia_base=reference,
            referencia_operaciones=operations_reference,
            cuenta_gastos_id=account.id,
            categorias=categories,
            edicion=edition,
            currency=currency,
        )
        session.add(report_document)
        await session.flush()

        expense = await create_expense_from_data(
            session=session,
            concepto=concept,
            gasto_cantidad=amount,
            fecha=expense_date,
            empleado_id=employee_id,
            proyecto=tournament.name,
            tipo_gasto="ticket",
            departamento=getattr(employee, "departamento", None),
            fase_torneo=phase,
            metodo_pago=payment_method,
            archivo_nombre=str(payload.get("file_name") or "comprobante"),
            archivo_data=str(_required(payload, "file_b64")),
            tournament_id=str(tournament_id),
            origen="assistant_receipt_workflow",
            skip_initial_tocino=True,
            categorias=categories,
            edicion=edition,
            currency=currency,
            budget_concept_id=(
                UUID(str(payload["budget_concept_id"]))
                if payload.get("budget_concept_id")
                else None
            ),
        )
        expense.cuenta_gastos_id = account.id
        expense.referencia_base = reference
        expense.informe_documento_id = report_document.id

        request_payload = build_solicitud_personal_payload(
            cuenta_id=account.id,
            empleado_id=employee_id,
            monto_solicitado=amount,
            concepto_pago=concept,
            proveedor_cliente_id=payload.get("provider_id"),
            budget_concept_id=payload.get("budget_concept_id"),
            pago_urgente=bool(payload.get("urgent", False)),
        )
        payment_request = await create_solicitud_personal_document(
            session,
            request_payload,
            commit=False,
        )
        if commit:
            await session.commit()
        else:
            await session.flush()
        return PersonalReceiptWorkflowResult(
            account=account,
            report_document=report_document,
            expense=expense,
            payment_request=payment_request,
        )
    except Exception:
        await session.rollback()
        raise


async def create_third_party_receipt_workflow(
    session: AsyncSession,
    *,
    employee_id: UUID,
    payload: Mapping[str, Any],
    commit: bool = True,
) -> Documento:
    raw_bytes = payload.get("file_bytes")
    if not isinstance(raw_bytes, bytes) or not raw_bytes:
        raise ValueError("file_bytes is required")
    attachment = SolicitudTercerosAttachment(
        raw_bytes=raw_bytes,
        filename=str(payload.get("file_name") or "comprobante"),
        mime_type=str(payload.get("content_type") or "application/octet-stream"),
        categoria="supporting",
    )
    request_payload = build_solicitud_terceros_payload(
        empleado_id=employee_id,
        monto_solicitado=_required(payload, "amount"),
        proveedor_cliente_id=str(_required(payload, "provider_id")),
        torneo_id=str(_required(payload, "tournament_id")),
        concepto_pago=str(_required(payload, "concept")),
        fecha_pago=payload.get("payment_date"),
        numero_factura=payload.get("invoice_number"),
        notas=payload.get("notes"),
        attachments=[attachment],
        categorias=list(payload.get("categories") or []),
        edicion=payload.get("edition"),
        currency=payload.get("currency") or "MXN",
        budget_concept_id=payload.get("budget_concept_id"),
        pago_urgente=bool(payload.get("urgent", False)),
    )
    try:
        document = await create_solicitud_terceros_document(
            session,
            request_payload,
            commit=False,
        )
        if commit:
            await session.commit()
        else:
            await session.flush()
        return document
    except Exception:
        await session.rollback()
        raise
