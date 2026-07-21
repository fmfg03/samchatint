from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devnous.gastos.models import (
    AccountingPoliza,
    Aprobacion,
    BankMovement,
    CFDIReport,
    Documento,
    Empleado,
    ExpenseReport,
    InvoiceReport,
    ReconciliationAuditLog,
    Reembolso,
    Tournament,
)
from devnous.gastos.services.cfdi_expense_link_service import (
    link_expense_to_cfdi_if_manual_uuid_set,
    normalize_cfdi_uuid_to_canonical,
)
from devnous.gastos.services.cfdi_matching_service import get_cfdi_matching_overview
from devnous.gastos.services.documento_payment_service import (
    get_pending_document_payment_overview,
    register_document_payment,
    register_document_reembolso,
)
from devnous.gastos.services.documento_service import (
    SolicitudValidationError,
    build_solicitud_personal_payload,
    build_solicitud_terceros_payload,
    create_solicitud_personal_document,
    create_solicitud_terceros_document,
)
from devnous.gastos.services.documento_workflow_service import (
    transition_documento_workflow,
)
from devnous.gastos.services.expense_accounting_service import (
    build_expense_accounting_preview,
)
from devnous.gastos.services.expense_service import (
    create_expense_from_data,
    trigger_cfdi_generation,
)
from devnous.gastos.services.receipt_workflow_service import (
    create_personal_receipt_workflow,
    create_third_party_receipt_workflow,
)
from devnous.gastos.services.tournament_phase_service import get_tournament_etapas
from samchat.budgets.service import (
    build_budget_commitment_expense_preview,
    build_budget_executive_alerts,
    build_budget_snapshot,
    get_budget_version,
    list_budget_lines,
    list_budget_tournament_commitments,
    transition_budget_version,
    update_budget_line,
    update_budget_version_metadata,
)
from samchat.tournaments_v2.adapters import (
    create_media_asset_v2,
    update_player_fields_v2,
    update_team_fields_v2,
)
from samchat.tournaments_v2.services import build_tournament_soul_snapshot

from .capability_negotiation import receipt_workflow_writes_enabled
from .context import AssistantContext
from .tools import (
    finance_accounting_report,
    finance_alerts_scan,
    finance_expense_assign_accounting,
    finance_expense_post_accounting,
    finance_expense_workflow_status,
    finance_planner_snapshot,
    finance_realtime_report,
    finance_strategy_snapshot,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AdapterResult:
    action: str
    status: str
    data: Dict[str, Any]
    context: AssistantContext


async def _raise_unexpected_adapter_error(
    session: AsyncSession,
    *,
    action: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    await session.rollback()
    logger.exception(
        "Unexpected adapter error", extra={"action": action, **(extra or {})}
    )
    raise RuntimeError("Unexpected processing error")


def _as_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    return UUID(str(value))


def _normalize_amount(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _normalize_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.utcnow()
    return datetime.fromisoformat(str(value))


def _normalize_optional_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    return _normalize_datetime(value)


def _adapter_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _supabase_rest_fetch_sync(
    table: str,
    *,
    select_expr: str = "*",
    filters: Optional[Dict[str, str]] = None,
    order: Optional[str] = None,
    limit: int = 500,
) -> Any:
    base_url = (
        os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or ""
    ).rstrip("/")
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        return []
    params: Dict[str, str] = {
        "select": select_expr,
        "limit": str(max(1, min(limit, 5000))),
    }
    if filters:
        params.update(filters)
    if order:
        params["order"] = order
    url = f"{base_url}/rest/v1/{table}?{urllib_parse.urlencode(params)}"
    req = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib_request.urlopen(req, timeout=12) as res:
            body = res.read().decode("utf-8", errors="replace")
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Supabase REST unavailable: {exc}") from exc
    return json.loads(body or "[]")


def _digits_only(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _plain_text_from_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _supabase_rest_mutate_sync(
    table: str,
    *,
    method: str,
    payload: Optional[Any] = None,
    filters: Optional[Dict[str, str]] = None,
) -> Any:
    base_url = (
        os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or ""
    ).rstrip("/")
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not base_url or not service_key:
        raise RuntimeError("Supabase service role key is not configured")
    params = urllib_parse.urlencode(filters or {})
    url = f"{base_url}/rest/v1/{table}"
    if params:
        url = f"{url}?{params}"
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    req = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        data=data,
        method=method.upper(),
    )
    try:
        with urllib_request.urlopen(req, timeout=18) as res:
            body = res.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase REST mutation failed: {detail}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Supabase REST unavailable: {exc}") from exc
    return json.loads(body or "[]")


async def _supabase_rest_fetch(
    table: str,
    *,
    select_expr: str = "*",
    filters: Optional[Dict[str, str]] = None,
    order: Optional[str] = None,
    limit: int = 500,
) -> list[Dict[str, Any]]:
    payload = await asyncio.to_thread(
        _supabase_rest_fetch_sync,
        table,
        select_expr=select_expr,
        filters=filters,
        order=order,
        limit=limit,
    )
    return payload if isinstance(payload, list) else []


async def _supabase_rest_mutate(
    table: str,
    *,
    method: str,
    payload: Optional[Any] = None,
    filters: Optional[Dict[str, str]] = None,
) -> Any:
    return await asyncio.to_thread(
        _supabase_rest_mutate_sync,
        table,
        method=method,
        payload=payload,
        filters=filters,
    )


def _normalize_binary_payload(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    raise ValueError("binary payload must be bytes")


def _coalesce_contextual_scope(
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "bi_scope": payload.get("bi_scope")
        or context.tournament_name
        or context.departamento
        or context.fase_torneo,
        "proyecto": payload.get("proyecto") or context.tournament_name,
        "concepto": payload.get("concepto") or context.concepto,
        "departamento": payload.get("departamento") or context.departamento,
        "fase_torneo": payload.get("fase_torneo") or context.fase_torneo,
    }


def _expense_snapshot(expense: ExpenseReport) -> Dict[str, Any]:
    referencia_operaciones = None
    for linked_doc in (
        getattr(expense, "solicitud_documento", None),
        getattr(expense, "documento", None),
        getattr(expense, "informe_documento", None),
    ):
        if linked_doc is not None:
            referencia_operaciones = getattr(linked_doc, "referencia_operaciones", None)
            if referencia_operaciones:
                break

    return {
        "expense_id": str(expense.id),
        "numero_referencia": getattr(expense, "numero_referencia", None),
        "concepto": getattr(expense, "concepto", None),
        "proyecto": getattr(expense, "proyecto", None),
        "departamento": getattr(expense, "departamento", None),
        "fase_torneo": getattr(expense, "fase_torneo", None),
        "tipo_gasto": getattr(expense, "tipo_gasto", None),
        "estado_factura": getattr(expense, "estado_factura", None),
        "estado_reembolso": getattr(expense, "estado_reembolso", None),
        "mensaje_error": getattr(expense, "mensaje_error", None),
        "gasto_cantidad": _normalize_amount(getattr(expense, "gasto_cantidad", 0) or 0),
        "fecha": expense.fecha.isoformat() if getattr(expense, "fecha", None) else None,
        "nova_request_id": getattr(expense, "nova_request_id", None),
        "cfdi_report_id": (
            str(expense.cfdi_report_id)
            if getattr(expense, "cfdi_report_id", None)
            else None
        ),
        "documento_id": (
            str(expense.documento_id)
            if getattr(expense, "documento_id", None)
            else None
        ),
        "solicitud_documento_id": (
            str(expense.solicitud_documento_id)
            if getattr(expense, "solicitud_documento_id", None)
            else None
        ),
        "informe_documento_id": (
            str(expense.informe_documento_id)
            if getattr(expense, "informe_documento_id", None)
            else None
        ),
        "cuenta_gastos_id": (
            str(expense.cuenta_gastos_id)
            if getattr(expense, "cuenta_gastos_id", None)
            else None
        ),
        "referencia_base": getattr(expense, "referencia_base", None),
        "referencia_operaciones": referencia_operaciones,
    }


def _cfdi_snapshot(cfdi: Optional[CFDIReport]) -> Optional[Dict[str, Any]]:
    if cfdi is None:
        return None
    return {
        "cfdi_report_id": str(cfdi.id),
        "cfdi_uuid": getattr(cfdi, "cfdi_uuid", None),
        "estado_factura": getattr(cfdi, "estado_factura", None),
    }


def _movement_snapshot(movement: BankMovement) -> Dict[str, Any]:
    return {
        "bank_movement_id": str(movement.id),
        "proveedor_cliente_id": (
            str(movement.proveedor_cliente_id)
            if getattr(movement, "proveedor_cliente_id", None)
            else None
        ),
        "matched_aux_entry_id": (
            str(movement.matched_aux_entry_id)
            if getattr(movement, "matched_aux_entry_id", None)
            else None
        ),
        "related_poliza_id": (
            str(movement.related_poliza_id)
            if getattr(movement, "related_poliza_id", None)
            else None
        ),
        "matched_expense_id": (
            str(movement.matched_expense_id)
            if getattr(movement, "matched_expense_id", None)
            else None
        ),
        "conciliacion_estado": getattr(movement, "conciliacion_estado", None)
        or "unmatched",
    }


def _documento_snapshot(documento: Documento) -> Dict[str, Any]:
    return {
        "documento_id": str(documento.id),
        "tipo": getattr(documento, "tipo", None),
        "estado": getattr(documento, "estado", None),
        "numero_referencia": getattr(documento, "numero_referencia", None),
        "torneo_id": (
            str(documento.torneo_id) if getattr(documento, "torneo_id", None) else None
        ),
        "proveedor_cliente_id": (
            str(documento.proveedor_cliente_id)
            if getattr(documento, "proveedor_cliente_id", None)
            else None
        ),
        "monto_solicitado": _normalize_amount(
            getattr(documento, "monto_solicitado", 0) or 0
        ),
        "fecha_pago": (
            documento.fecha_pago.isoformat()
            if getattr(documento, "fecha_pago", None)
            else None
        ),
        "concepto_pago": getattr(documento, "concepto_pago", None),
        "referencia_base": getattr(documento, "referencia_base", None),
        "referencia_operaciones": getattr(documento, "referencia_operaciones", None),
        "cuenta_gastos_id": (
            str(documento.cuenta_gastos_id)
            if getattr(documento, "cuenta_gastos_id", None)
            else None
        ),
        "enviado_en": (
            documento.enviado_en.isoformat()
            if getattr(documento, "enviado_en", None)
            else None
        ),
        "aprobado_en": (
            documento.aprobado_en.isoformat()
            if getattr(documento, "aprobado_en", None)
            else None
        ),
    }


def _aprobacion_snapshot(aprobacion: Aprobacion) -> Dict[str, Any]:
    return {
        "aprobacion_id": str(aprobacion.id),
        "tipo_entidad": getattr(aprobacion, "tipo_entidad", None),
        "entidad_id": str(aprobacion.entidad_id),
        "aprobador_id": str(aprobacion.aprobador_id),
        "accion": getattr(aprobacion, "accion", None),
        "comentario": getattr(aprobacion, "comentario", None),
        "fecha": (
            aprobacion.fecha.isoformat() if getattr(aprobacion, "fecha", None) else None
        ),
    }


def _reembolso_snapshot(reembolso: Reembolso) -> Dict[str, Any]:
    return {
        "reembolso_id": str(reembolso.id),
        "empleado_id": str(reembolso.empleado_id),
        "documento_id": str(reembolso.documento_id),
        "monto": _normalize_amount(getattr(reembolso, "monto", 0) or 0),
        "moneda": getattr(reembolso, "moneda", None),
        "metodo_pago": getattr(reembolso, "metodo_pago", None),
        "fecha_pago": (
            reembolso.fecha_pago.isoformat()
            if getattr(reembolso, "fecha_pago", None)
            else None
        ),
        "estado": getattr(reembolso, "estado", None),
        "creado_en": (
            reembolso.creado_en.isoformat()
            if getattr(reembolso, "creado_en", None)
            else None
        ),
    }


def _recompute_conciliacion_estado(movement: BankMovement) -> str:
    if getattr(movement, "matched_aux_entry_id", None) and getattr(
        movement, "proveedor_cliente_id", None
    ):
        return "high"
    if (
        getattr(movement, "matched_aux_entry_id", None)
        or getattr(movement, "proveedor_cliente_id", None)
        or getattr(movement, "matched_expense_id", None)
    ):
        return "medium"
    return "unmatched"


async def _resolve_tournament(
    session: AsyncSession,
    *,
    tournament_id: Optional[str],
    tournament_name: Optional[str],
) -> Optional[Tournament]:
    if tournament_id:
        tournament = await session.get(Tournament, _as_uuid(tournament_id))
        if tournament is not None:
            return tournament

    if tournament_name:
        stmt = select(Tournament).where(Tournament.name == str(tournament_name).strip())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    return None


async def create_manual_expense_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    """Thin adapter over the existing shared expense creation service."""

    concept = payload.get("concepto") or context.concepto
    if not concept:
        raise ValueError("concepto is required")

    amount = payload.get("gasto_cantidad")
    if amount is None:
        raise ValueError("gasto_cantidad is required")

    try:
        expense = await create_expense_from_data(
            session=session,
            concepto=str(concept),
            gasto_cantidad=_normalize_amount(amount),
            fecha=_normalize_datetime(payload.get("fecha")),
            empleado_id=_as_uuid(
                payload.get("empleado_id") or context.responsible_user_id
            ),
            proyecto=payload.get("proyecto") or context.tournament_name,
            tipo_gasto=str(payload.get("tipo_gasto") or "manual"),
            departamento=payload.get("departamento") or context.departamento,
            fase_torneo=payload.get("fase_torneo") or context.fase_torneo,
            metodo_pago=payload.get("metodo_pago"),
            ultimos_4_digitos=payload.get("ultimos_4_digitos"),
            iva=payload.get("iva"),
            hospedaje_entidad_fiscal=payload.get("hospedaje_entidad_fiscal"),
            hospedaje_tasa_impuesto=payload.get("hospedaje_tasa_impuesto"),
            hospedaje_impuesto_monto=payload.get("hospedaje_impuesto_monto"),
            hospedaje_impuesto_confirmado=bool(
                payload.get("hospedaje_impuesto_confirmado", False)
            ),
            cfdi_use=payload.get("cfdi_use"),
            archivo_nombre=payload.get("archivo_nombre"),
            archivo_data=payload.get("archivo_data"),
            archivo_path=payload.get("archivo_path"),
            tournament_id=payload.get("tournament_id") or context.tournament_id,
            rfc_id=payload.get("rfc_id"),
            nombre_enviador=payload.get("nombre_enviador"),
            origen=payload.get("origen"),
            skip_initial_tocino=bool(payload.get("skip_initial_tocino", False)),
        )
        await session.commit()
        await session.refresh(expense)
    except ValueError:
        raise
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="expenses.create_manual_expense",
            extra={
                "empleado_id": str(
                    payload.get("empleado_id") or context.responsible_user_id or ""
                ),
                "tournament_id": str(
                    payload.get("tournament_id") or context.tournament_id or ""
                ),
            },
        )
    updated_context = context.merge(
        expense_id=str(expense.id),
        tournament_id=payload.get("tournament_id") or context.tournament_id,
        tournament_name=payload.get("proyecto") or context.tournament_name,
        fase_torneo=payload.get("fase_torneo") or context.fase_torneo,
        concepto=str(concept),
        departamento=payload.get("departamento") or context.departamento,
        referencia_base=getattr(expense, "referencia_base", None)
        or context.referencia_base,
        referencia_operaciones=payload.get("referencia_operaciones")
        or context.referencia_operaciones,
    )
    return AdapterResult(
        action="expenses.create_manual_expense",
        status="completed",
        data=_expense_snapshot(expense),
        context=updated_context,
    )


async def request_cfdi_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    if not expense_id:
        raise ValueError("expense_id is required")

    expense = await session.get(ExpenseReport, _as_uuid(expense_id))
    if expense is None:
        raise ValueError(f"expense {expense_id} not found")

    nova_request_id = await trigger_cfdi_generation(
        session=session,
        expense=expense,
        rfc_id=payload.get("rfc_id"),
        cfdi_use=payload.get("cfdi_use"),
    )
    await session.refresh(expense)
    expense_snapshot = _expense_snapshot(expense)
    updated_context = context.merge(
        expense_id=str(expense.id),
        receipt_id=expense_snapshot.get("cfdi_report_id") or context.receipt_id,
        document_id=expense_snapshot.get("solicitud_documento_id")
        or expense_snapshot.get("documento_id"),
        expense_account_id=expense_snapshot.get("cuenta_gastos_id"),
        referencia_base=expense_snapshot.get("referencia_base")
        or context.referencia_base,
        referencia_operaciones=expense_snapshot.get("referencia_operaciones")
        or context.referencia_operaciones,
    )
    return AdapterResult(
        action="receipts.request_cfdi",
        status="completed" if nova_request_id else "failed",
        data={
            **expense_snapshot,
            "nova_request_id": nova_request_id,
        },
        context=updated_context,
    )


async def cfdi_workflow_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    numero_referencia = payload.get("numero_referencia")
    if not expense_id and not numero_referencia:
        raise ValueError("expense_id or numero_referencia is required")

    result = await finance_expense_workflow_status(
        session,
        expense_id=expense_id,
        numero_referencia=numero_referencia,
    )
    cfdi = result.get("cfdi") or {}
    updated_context = context.merge(
        expense_id=str(result.get("expense_id") or expense_id or context.expense_id),
        receipt_id=cfdi.get("cfdi_report_id") or context.receipt_id,
    )
    return AdapterResult(
        action="receipts.cfdi_workflow_snapshot",
        status="completed",
        data=result,
        context=updated_context,
    )


async def cfdi_matching_overview_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    result = await get_cfdi_matching_overview(
        session,
        view=payload.get("view"),
        limit=payload.get("limit", 100),
    )
    return AdapterResult(
        action="receipts.cfdi_matching_overview",
        status="completed",
        data=result,
        context=context,
    )


async def _transition_document_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
    action: str,
    result_action: str,
) -> AdapterResult:
    document_id = payload.get("document_id") or context.document_id or context.need_id
    actor_id = payload.get("actor_id") or context.responsible_user_id
    if not document_id:
        raise ValueError("document_id is required")
    if not actor_id:
        raise ValueError("actor_id is required")

    result = await transition_documento_workflow(
        session,
        documento_id=document_id,
        actor_id=actor_id,
        action=action,
        comentario=payload.get("comentario"),
    )
    documento_snapshot = _documento_snapshot(result.documento)
    aprobacion_snapshot = _aprobacion_snapshot(result.aprobacion)
    updated_context = context.merge(
        need_id=documento_snapshot["documento_id"],
        document_id=documento_snapshot["documento_id"],
        responsible_user_id=str(actor_id),
        expense_account_id=documento_snapshot.get("cuenta_gastos_id")
        or context.expense_account_id,
        tournament_id=documento_snapshot.get("torneo_id") or context.tournament_id,
        referencia_base=documento_snapshot.get("referencia_base")
        or context.referencia_base,
        referencia_operaciones=(
            documento_snapshot.get("referencia_operaciones")
            or context.referencia_operaciones
        ),
    )
    return AdapterResult(
        action=result_action,
        status="completed",
        data={
            "documento": documento_snapshot,
            "aprobacion": aprobacion_snapshot,
        },
        context=updated_context,
    )


async def send_document_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    return await _transition_document_adapter(
        session,
        context=context,
        payload=payload,
        action="send",
        result_action="receipts.send_document",
    )


async def approve_document_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    return await _transition_document_adapter(
        session,
        context=context,
        payload=payload,
        action="approve",
        result_action="receipts.approve_document",
    )


async def reject_document_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    return await _transition_document_adapter(
        session,
        context=context,
        payload=payload,
        action="reject",
        result_action="receipts.reject_document",
    )


async def build_expense_accounting_preview_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    if not expense_id:
        raise ValueError("expense_id is required")

    stmt = select(ExpenseReport).where(ExpenseReport.id == _as_uuid(expense_id))
    result = await session.execute(stmt)
    expense = result.scalar_one_or_none()
    if expense is None:
        raise ValueError(f"expense {expense_id} not found")

    preview = await build_expense_accounting_preview(
        session,
        expense,
        contra_cuenta_contable_id=payload.get("contra_cuenta_contable_id"),
        contra_cuenta_codigo=payload.get("contra_cuenta_codigo"),
    )
    expense_snapshot = _expense_snapshot(expense)
    updated_context = context.merge(
        expense_id=str(expense.id),
        document_id=expense_snapshot.get("solicitud_documento_id")
        or expense_snapshot.get("documento_id"),
        expense_account_id=expense_snapshot.get("cuenta_gastos_id"),
        referencia_base=expense_snapshot.get("referencia_base")
        or context.referencia_base,
        referencia_operaciones=expense_snapshot.get("referencia_operaciones")
        or context.referencia_operaciones,
    )
    return AdapterResult(
        action="accounting.build_expense_preview",
        status="completed",
        data={
            "expense": expense_snapshot,
            "preview": preview,
        },
        context=updated_context,
    )


async def executive_realtime_report_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    scoped = _coalesce_contextual_scope(context=context, payload=payload)
    result = await finance_realtime_report(
        session,
        question=payload.get("question"),
        title=payload.get("title"),
        date_from=payload.get("date_from"),
        date_to=payload.get("date_to"),
        proyecto=scoped["proyecto"],
        concepto=scoped["concepto"],
        departamento=scoped["departamento"],
        fase_torneo=scoped["fase_torneo"],
        metodo_pago=payload.get("metodo_pago"),
        proveedor_nombre=payload.get("proveedor_nombre"),
        budget_total=payload.get("budget_total"),
        budget_source=payload.get("budget_source", "solicitudes"),
        compare_years=payload.get("compare_years", 1),
        projection_mode=payload.get("projection_mode", "run_rate"),
        group_by=payload.get("group_by", "proyecto"),
        top_n=payload.get("top_n", 12),
        bi_scope=scoped["bi_scope"],
    )
    return AdapterResult(
        action="executive.realtime_report",
        status="completed",
        data=result,
        context=context.merge(
            tournament_name=scoped["proyecto"] or context.tournament_name,
            concepto=scoped["concepto"] or context.concepto,
            departamento=scoped["departamento"] or context.departamento,
            fase_torneo=scoped["fase_torneo"] or context.fase_torneo,
        ),
    )


async def executive_strategy_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    scoped = _coalesce_contextual_scope(context=context, payload=payload)
    result = await finance_strategy_snapshot(
        session,
        question=payload.get("question"),
        title=payload.get("title"),
        date_from=payload.get("date_from"),
        date_to=payload.get("date_to"),
        proyecto=scoped["proyecto"],
        concepto=scoped["concepto"],
        departamento=scoped["departamento"],
        fase_torneo=scoped["fase_torneo"],
        metodo_pago=payload.get("metodo_pago"),
        proveedor_nombre=payload.get("proveedor_nombre"),
        budget_total=payload.get("budget_total"),
        budget_source=payload.get("budget_source", "solicitudes"),
        compare_years=payload.get("compare_years", 1),
        bi_scope=scoped["bi_scope"],
        top_n=payload.get("top_n", 12),
        z_threshold=payload.get("z_threshold", 2.0),
        min_amount=payload.get("min_amount", 5000.0),
        min_records=payload.get("min_records", 3),
    )
    return AdapterResult(
        action="executive.strategy_snapshot",
        status="completed",
        data=result,
        context=context.merge(
            tournament_name=scoped["proyecto"] or context.tournament_name,
            concepto=scoped["concepto"] or context.concepto,
            departamento=scoped["departamento"] or context.departamento,
            fase_torneo=scoped["fase_torneo"] or context.fase_torneo,
        ),
    )


async def executive_accounting_report_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    result = await finance_accounting_report(
        session,
        report_type=payload.get("report_type", "estado_mes"),
        year=payload.get("year"),
        month=payload.get("month"),
        tipo_poliza=payload.get("tipo_poliza", "all"),
        cuenta_codigo=payload.get("cuenta_codigo", "all"),
        q=payload.get("q", ""),
        limit=payload.get("limit", 120),
    )
    return AdapterResult(
        action="executive.accounting_report",
        status="completed",
        data=result,
        context=context,
    )


async def executive_alerts_scan_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    scoped = _coalesce_contextual_scope(context=context, payload=payload)
    result = await finance_alerts_scan(
        session,
        date_from=payload.get("date_from"),
        date_to=payload.get("date_to"),
        bi_scope=scoped["bi_scope"],
        z_threshold=payload.get("z_threshold", 2.0),
        min_amount=payload.get("min_amount", 5000.0),
        min_records=payload.get("min_records", 3),
    )
    return AdapterResult(
        action="executive.alerts_scan",
        status="completed",
        data=result,
        context=context.merge(
            tournament_name=scoped["proyecto"] or context.tournament_name,
            concepto=scoped["concepto"] or context.concepto,
            departamento=scoped["departamento"] or context.departamento,
            fase_torneo=scoped["fase_torneo"] or context.fase_torneo,
        ),
    )


async def executive_planner_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    scoped = _coalesce_contextual_scope(context=context, payload=payload)
    result = await finance_planner_snapshot(
        session,
        question=payload.get("question"),
        title=payload.get("title"),
        date_from=payload.get("date_from"),
        date_to=payload.get("date_to"),
        proyecto=scoped["proyecto"],
        concepto=scoped["concepto"],
        departamento=scoped["departamento"],
        fase_torneo=scoped["fase_torneo"],
        metodo_pago=payload.get("metodo_pago"),
        proveedor_nombre=payload.get("proveedor_nombre"),
        budget_total=payload.get("budget_total"),
        budget_source=payload.get("budget_source", "solicitudes"),
        compare_years=payload.get("compare_years", 1),
        bi_scope=scoped["bi_scope"],
        top_n=payload.get("top_n", 12),
        z_threshold=payload.get("z_threshold", 2.0),
        min_amount=payload.get("min_amount", 5000.0),
        min_records=payload.get("min_records", 3),
    )
    return AdapterResult(
        action="executive.planner_snapshot",
        status="completed",
        data=result,
        context=context.merge(
            tournament_name=scoped["proyecto"] or context.tournament_name,
            concepto=scoped["concepto"] or context.concepto,
            departamento=scoped["departamento"] or context.departamento,
            fase_torneo=scoped["fase_torneo"] or context.fase_torneo,
        ),
    )


async def budgets_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    edition_year = int(payload.get("edition_year") or context.edition or 2026)
    result = await build_budget_snapshot(
        session,
        tournament_id=payload.get("tournament_id") or context.tournament_id,
        tournament_name=payload.get("tournament_name") or context.tournament_name,
        tournament_slug=payload.get("tournament_slug"),
        edition_year=edition_year,
        version_id=payload.get("version_id"),
    )
    result["executive_alerts"] = list(result.get("executive_alerts") or [])
    if not result["executive_alerts"]:
        result["executive_alerts"] = build_budget_executive_alerts(
            result.get("summary") or {},
            result.get("forecast") or {},
            result.get("scenarios") or {},
        )
    return AdapterResult(
        action="budgets.snapshot",
        status="completed",
        data=result,
        context=context.merge(
            tournament_id=payload.get("tournament_id") or context.tournament_id,
            tournament_name=payload.get("tournament_name") or context.tournament_name,
            edition=str(edition_year),
        ),
    )


async def budgets_update_line_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    line_id = payload.get("line_id") or payload.get("budget_line_id")
    if not line_id:
        raise ValueError("line_id is required")
    updated_line = await update_budget_line(
        session,
        line_id=str(line_id),
        actor_empleado_id=payload.get("empleado_id") or context.responsible_user_id,
        updates={
            key: payload.get(key)
            for key in (
                "concept_name",
                "account_code_final",
                "budget_amount",
                "priority",
                "owner_name",
                "phase",
                "criteria_note",
                "observations",
            )
            if key in payload
        },
    )
    version = await get_budget_version(
        session,
        version_id=updated_line["budget_version_id"],
    )
    return AdapterResult(
        action="budgets.update_line",
        status="completed",
        data={"line": updated_line, "version": version},
        context=context.merge(
            tournament_id=updated_line.get("tournament_id") or context.tournament_id,
            tournament_name=updated_line.get("tournament_name")
            or context.tournament_name,
            edition=str(version.get("edition_year") or context.edition or "2026"),
        ),
    )


async def budgets_update_version_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    version_id = payload.get("version_id")
    if not version_id:
        raise ValueError("version_id is required")
    version = await update_budget_version_metadata(
        session,
        version_id=str(version_id),
        actor_empleado_id=payload.get("empleado_id") or context.responsible_user_id,
        version_name=payload.get("version_name"),
        notes=payload.get("notes"),
    )
    return AdapterResult(
        action="budgets.update_version",
        status="completed",
        data={"version": version},
        context=context.merge(
            edition=str(version.get("edition_year") or context.edition or "2026")
        ),
    )


async def budgets_submit_for_approval_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    version_id = payload.get("version_id")
    if not version_id:
        raise ValueError("version_id is required")
    version = await transition_budget_version(
        session,
        version_id=str(version_id),
        new_status="submitted",
        actor_empleado_id=payload.get("empleado_id") or context.responsible_user_id,
        note=payload.get("note"),
    )
    return AdapterResult(
        action="budgets.submit_for_approval",
        status="completed",
        data={"version": version},
        context=context.merge(
            edition=str(version.get("edition_year") or context.edition or "2026")
        ),
    )


async def budgets_approve_version_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    version_id = payload.get("version_id")
    if not version_id:
        raise ValueError("version_id is required")
    version = await transition_budget_version(
        session,
        version_id=str(version_id),
        new_status="approved",
        actor_empleado_id=payload.get("empleado_id") or context.responsible_user_id,
        note=payload.get("note"),
    )
    return AdapterResult(
        action="budgets.approve_version",
        status="completed",
        data={"version": version},
        context=context.merge(
            edition=str(version.get("edition_year") or context.edition or "2026")
        ),
    )


async def budgets_freeze_version_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    version_id = payload.get("version_id")
    if not version_id:
        raise ValueError("version_id is required")
    version = await transition_budget_version(
        session,
        version_id=str(version_id),
        new_status="frozen",
        actor_empleado_id=payload.get("empleado_id") or context.responsible_user_id,
        note=payload.get("note"),
    )
    return AdapterResult(
        action="budgets.freeze_version",
        status="completed",
        data={"version": version},
        context=context.merge(
            edition=str(version.get("edition_year") or context.edition or "2026")
        ),
    )


async def budgets_reforecast_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    version_id = payload.get("version_id")
    if not version_id:
        raise ValueError("version_id is required")
    version = await transition_budget_version(
        session,
        version_id=str(version_id),
        new_status="reforecast",
        actor_empleado_id=payload.get("empleado_id") or context.responsible_user_id,
        note=payload.get("note"),
    )
    return AdapterResult(
        action="budgets.reforecast",
        status="completed",
        data={"version": version},
        context=context.merge(
            edition=str(version.get("edition_year") or context.edition or "2026")
        ),
    )


async def operations_folder_planner_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    tournament_id = payload.get("tournament_id") or context.tournament_id
    tournament_name = payload.get("tournament_name") or context.tournament_name
    edition_year = int(payload.get("edition_year") or context.edition or 2026)
    item_type = payload.get("item_type")
    status = payload.get("status")
    scope = payload.get("scope")
    entity_name = payload.get("entity_name")
    limit = max(1, min(int(payload.get("limit") or 300), 1000))
    drill_document = (
        payload.get("drill_document")
        or payload.get("documento_id")
        or payload.get("document_id")
    )
    budget_commitment_limit = max(
        1,
        min(int(payload.get("budget_commitment_limit") or 20), 100),
    )

    filters: Dict[str, str] = {}
    if tournament_id:
        filters["tournament_id"] = f"eq.{tournament_id}"
    if item_type:
        filters["item_type"] = f"eq.{item_type}"
    if status:
        filters["status"] = f"eq.{status}"
    if scope:
        filters["scope"] = f"eq.{scope}"
    if entity_name:
        filters["entity_name"] = f"ilike.%{str(entity_name).strip()}%"

    tournament_rows = await _supabase_rest_fetch(
        "tournaments",
        select_expr="id,name,slug,is_active",
        limit=1000,
    )
    tournament_by_id = {str(row.get("id")): row for row in tournament_rows}
    commitment_rows = await _supabase_rest_fetch(
        "tournament_operational_commitments",
        select_expr=(
            "id,tournament_id,source_draft_id,source_evidence_id,item_type,scope,"
            "entity_name,title,owner_name,counterparty_name,due_date,amount,currency,"
            "status,confidence,notes,created_at,updated_at"
        ),
        filters=filters,
        order="due_date.asc.nullslast,created_at.desc",
        limit=limit,
    )
    commitments = [
        {
            **{key: _adapter_jsonable(value) for key, value in row.items()},
            "tournament_name": tournament_by_id.get(
                str(row.get("tournament_id")), {}
            ).get("name"),
            "tournament_slug": tournament_by_id.get(
                str(row.get("tournament_id")), {}
            ).get("slug"),
        }
        for row in commitment_rows
    ]
    if tournament_name and not tournament_id:
        needle = str(tournament_name).strip().lower()
        commitments = [
            item
            for item in commitments
            if needle in str(item.get("tournament_name") or "").lower()
            or needle in str(item.get("tournament_slug") or "").lower()
        ]

    today = date.today()
    open_statuses = {"open", "in_progress"}
    alerts = []
    counts_by_status: Dict[str, int] = {}
    counts_by_type: Dict[str, int] = {}
    planned_amount_by_type = {"payment": 0.0, "collection": 0.0}
    without_owner = 0
    due_next_30 = 0
    overdue = 0
    open_risks = 0

    for item in commitments:
        item_status = str(item.get("status") or "")
        item_type_value = str(item.get("item_type") or "")
        counts_by_status[item_status] = counts_by_status.get(item_status, 0) + 1
        counts_by_type[item_type_value] = counts_by_type.get(item_type_value, 0) + 1
        if item_status not in open_statuses:
            continue
        if item_type_value in planned_amount_by_type:
            planned_amount_by_type[item_type_value] += float(item.get("amount") or 0)
        if not str(item.get("owner_name") or "").strip():
            without_owner += 1
            alerts.append(
                {
                    "severity": "info",
                    "kind": "missing_owner",
                    "title": "Compromiso sin responsable",
                    "commitment_id": item.get("id"),
                    "tournament_name": item.get("tournament_name"),
                    "detail": item.get("title"),
                }
            )
        due_raw = item.get("due_date")
        due = date.fromisoformat(str(due_raw)) if due_raw else None
        if due:
            days = (due - today).days
            if days < 0:
                overdue += 1
                alerts.append(
                    {
                        "severity": "critical",
                        "kind": "overdue",
                        "title": "Compromiso vencido",
                        "commitment_id": item.get("id"),
                        "tournament_name": item.get("tournament_name"),
                        "detail": item.get("title"),
                        "due_date": due.isoformat(),
                        "days_overdue": abs(days),
                    }
                )
            elif days <= 30:
                due_next_30 += 1
                alerts.append(
                    {
                        "severity": "warning" if days <= 7 else "info",
                        "kind": "upcoming_due",
                        "title": "Compromiso próximo",
                        "commitment_id": item.get("id"),
                        "tournament_name": item.get("tournament_name"),
                        "detail": item.get("title"),
                        "due_date": due.isoformat(),
                        "days_until_due": days,
                    }
                )
        if item_type_value == "risk":
            open_risks += 1
            alerts.append(
                {
                    "severity": "warning",
                    "kind": "open_risk",
                    "title": "Riesgo operativo abierto",
                    "commitment_id": item.get("id"),
                    "tournament_name": item.get("tournament_name"),
                    "detail": item.get("title"),
                }
            )

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda alert: severity_rank.get(str(alert.get("severity")), 9))

    budget_snapshot: Optional[Dict[str, Any]] = None
    budget_commitments: list[Dict[str, Any]] = []
    selected_budget_commitment: Optional[Dict[str, Any]] = None
    selected_budget_commitment_expense: Optional[Dict[str, Any]] = None
    if tournament_id or tournament_name:
        budget_snapshot = await build_budget_snapshot(
            session,
            tournament_id=tournament_id,
            tournament_name=tournament_name,
            edition_year=edition_year,
        )
        budget_snapshot["executive_alerts"] = list(
            budget_snapshot.get("executive_alerts") or []
        )
        if not budget_snapshot["executive_alerts"]:
            budget_snapshot["executive_alerts"] = build_budget_executive_alerts(
                budget_snapshot.get("summary") or {},
                budget_snapshot.get("forecast") or {},
                budget_snapshot.get("scenarios") or {},
            )
        budget_commitments = await list_budget_tournament_commitments(
            session,
            edition_year=edition_year,
            tournament_id=str(tournament_id) if tournament_id else None,
            tournament_name=str(tournament_name) if tournament_name else None,
            tournament_code=None,
            limit=budget_commitment_limit,
        )
        if drill_document:
            selected_budget_commitment = next(
                (
                    item
                    for item in budget_commitments
                    if str(item.get("documento_id") or "") == str(drill_document)
                ),
                None,
            )
        if selected_budget_commitment:
            selected_budget_commitment_expense = (
                build_budget_commitment_expense_preview(selected_budget_commitment)
            )

    return AdapterResult(
        action="operations.folder_planner_snapshot",
        status="completed",
        data={
            "ok": True,
            "filters": {
                "tournament_id": tournament_id,
                "tournament_name": tournament_name,
                "item_type": item_type,
                "status": status,
                "scope": scope,
                "entity_name": entity_name,
                "limit": limit,
            },
            "summary": {
                "commitments_count": len(commitments),
                "counts_by_status": counts_by_status,
                "counts_by_type": counts_by_type,
                "overdue_open_count": overdue,
                "due_next_30_open_count": due_next_30,
                "without_owner_open_count": without_owner,
                "open_risks_count": open_risks,
                "planned_payment_amount": round(planned_amount_by_type["payment"], 2),
                "planned_collection_amount": round(
                    planned_amount_by_type["collection"], 2
                ),
            },
            "budget": (
                {
                    "source": budget_snapshot.get("source"),
                    "version": budget_snapshot.get("version") or {},
                    "summary": budget_snapshot.get("summary") or {},
                    "forecast": budget_snapshot.get("forecast") or {},
                    "scenarios": budget_snapshot.get("scenarios") or {},
                    "executive_alerts": budget_snapshot.get("executive_alerts") or [],
                    "commitments": budget_commitments,
                    "selected_commitment": selected_budget_commitment,
                    "selected_commitment_expense": selected_budget_commitment_expense,
                }
                if budget_snapshot
                else None
            ),
            "alerts": alerts[:50],
            "commitments": commitments[:200],
            "sources": ["tournament_operational_commitments", "tournaments"],
        },
        context=context.merge(
            tournament_id=(
                str(tournament_id) if tournament_id else context.tournament_id
            ),
            tournament_name=tournament_name or context.tournament_name,
        ),
    )


async def operations_tournament_soul_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    tournament_id = payload.get("tournament_id") or context.tournament_id
    tournament_slug = (
        payload.get("tournament_slug") or payload.get("slug") or context.tournament_name
    )
    tournament_name = payload.get("tournament_name") or context.tournament_name
    tournament_key = (
        payload.get("tournament_key")
        or context.sport
        or tournament_slug
        or tournament_name
        or "all"
    )
    if tournament_id and not tournament_slug:
        tournament_slug = str(tournament_id)
    result = await build_tournament_soul_snapshot(
        tournament_key=str(tournament_key or "all"),
        tournament_slug=str(tournament_slug) if tournament_slug else None,
        tournament_name=str(tournament_name) if tournament_name else None,
        include_communications=bool(payload.get("include_communications", True)),
        include_media=bool(payload.get("include_media", True)),
        limit=max(1, min(int(payload.get("limit") or 250), 1000)),
    )
    resolved_tournaments = result.get("tournaments") or []
    primary_tournament = resolved_tournaments[0] if resolved_tournaments else {}
    return AdapterResult(
        action="operations.tournament_soul_snapshot",
        status="completed",
        data=result,
        context=context.merge(
            tournament_id=(
                str(primary_tournament.get("id"))
                if primary_tournament.get("id")
                else context.tournament_id
            ),
            tournament_name=(
                str(primary_tournament.get("name"))
                if primary_tournament.get("name")
                else tournament_name or context.tournament_name
            ),
            sport=(
                context.sport
                or str(tournament_key or "")
                or str(primary_tournament.get("slug") or "")
            ),
        ),
    )


async def operations_update_commitment_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    commitment_id = payload.get("commitment_id")
    if not commitment_id:
        raise ValueError("commitment_id is required")
    tournament_id = payload.get("tournament_id") or context.tournament_id
    status = payload.get("status")
    owner_name = payload.get("owner_name")
    notes = payload.get("notes")

    allowed_statuses = {"open", "in_progress", "done", "dismissed"}
    update_payload: Dict[str, Any] = {}
    if status is not None:
        normalized_status = str(status).strip()
        if normalized_status not in allowed_statuses:
            raise ValueError(
                "status must be one of: open, in_progress, done, dismissed"
            )
        update_payload["status"] = normalized_status
    if owner_name is not None:
        update_payload["owner_name"] = str(owner_name).strip() or None
    if notes is not None:
        update_payload["notes"] = str(notes).strip() or None
    if not update_payload:
        raise ValueError("At least one of status, owner_name or notes is required")

    filters = {"id": f"eq.{commitment_id}"}
    if tournament_id:
        filters["tournament_id"] = f"eq.{tournament_id}"

    before_rows = await _supabase_rest_fetch(
        "tournament_operational_commitments",
        select_expr="*",
        filters=filters,
        limit=1,
    )
    if not before_rows:
        raise ValueError("Operational commitment not found")

    updated = await _supabase_rest_mutate(
        "tournament_operational_commitments",
        method="PATCH",
        payload=update_payload,
        filters=filters,
    )
    if not isinstance(updated, list) or not updated:
        raise ValueError("Operational commitment update did not return a row")

    updated_commitment = updated[0]
    return AdapterResult(
        action="operations.update_commitment",
        status="completed",
        data={
            "ok": True,
            "before": before_rows[0],
            "commitment": updated_commitment,
            "note": "Compromiso operativo actualizado. No se crearon pagos reales ni asientos contables.",
        },
        context=context.merge(
            tournament_id=str(updated_commitment.get("tournament_id") or tournament_id),
            tournament_name=context.tournament_name,
            referencia_operaciones=str(commitment_id),
        ),
    )


async def operations_create_solicitud_from_commitment_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    commitment_id = payload.get("commitment_id")
    if not commitment_id:
        raise ValueError("commitment_id is required")

    commitment_rows = await _supabase_rest_fetch(
        "tournament_operational_commitments",
        select_expr="*",
        filters={"id": f"eq.{commitment_id}"},
        limit=1,
    )
    if not commitment_rows:
        raise ValueError("Operational commitment not found")
    commitment = commitment_rows[0]
    if str(commitment.get("item_type") or "") != "payment":
        raise ValueError("Only payment commitments can create payment requests")

    empleado_id = payload.get("empleado_id") or context.responsible_user_id
    if not empleado_id:
        raise ValueError("empleado_id is required")
    proveedor_cliente_id = payload.get("proveedor_cliente_id")
    if not proveedor_cliente_id:
        raise ValueError("proveedor_cliente_id is required")

    supabase_tournament = None
    supabase_tournament_id = str(commitment.get("tournament_id") or "")
    if supabase_tournament_id:
        tournament_rows = await _supabase_rest_fetch(
            "tournaments",
            select_expr="id,name,slug",
            filters={"id": f"eq.{supabase_tournament_id}"},
            limit=1,
        )
        supabase_tournament = tournament_rows[0] if tournament_rows else None

    local_tournament = await _resolve_tournament(
        session,
        tournament_id=payload.get("torneo_id") or payload.get("gastos_torneo_id"),
        tournament_name=payload.get("tournament_name")
        or payload.get("proyecto")
        or (supabase_tournament or {}).get("name")
        or context.tournament_name,
    )
    if local_tournament is None:
        raise ValueError(
            "No matching gastos Tournament found. Provide gastos_torneo_id or tournament_name."
        )

    amount = payload.get("monto_solicitado")
    if amount is None:
        amount = commitment.get("amount")
    concept = (
        payload.get("concepto_pago")
        or commitment.get("title")
        or context.concepto
        or "Solicitud desde compromiso operativo"
    )
    notes = payload.get("notas")
    if not notes:
        notes = (
            "Generado desde compromiso operativo "
            f"{commitment_id}. Scope={commitment.get('scope')}"
            f"{' entidad=' + str(commitment.get('entity_name')) if commitment.get('entity_name') else ''}."
        )

    try:
        solicitud_payload = build_solicitud_terceros_payload(
            empleado_id=_as_uuid(empleado_id),
            monto_solicitado=amount,
            proveedor_cliente_id=proveedor_cliente_id,
            torneo_id=str(local_tournament.id),
            concepto_pago=str(concept),
            fecha_pago=payload.get("fecha_pago") or commitment.get("due_date"),
            numero_factura=payload.get("numero_factura"),
            referencia_pago=payload.get("referencia_pago"),
            fecha_inicio=payload.get("fecha_inicio"),
            fecha_fin=payload.get("fecha_fin"),
            notas=notes,
            pdf_bytes=_normalize_binary_payload(payload.get("archivo_data")),
            pdf_filename=payload.get("archivo_nombre"),
        )
        documento = await create_solicitud_terceros_document(session, solicitud_payload)
    except SolicitudValidationError as exc:
        raise ValueError(str(exc)) from exc
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="operations.create_solicitud_from_commitment",
            extra={
                "commitment_id": str(commitment_id),
                "empleado_id": str(empleado_id),
            },
        )

    update_notes = "\n".join(
        item
        for item in [
            str(commitment.get("notes") or "").strip(),
            (
                "Solicitud borrador creada desde este compromiso: "
                f"{documento.numero_referencia} ({documento.id})."
            ),
        ]
        if item
    )
    try:
        updated_commitment = await _supabase_rest_mutate(
            "tournament_operational_commitments",
            method="PATCH",
            payload={
                "status": "in_progress",
                "notes": update_notes,
                "payload": {
                    **(commitment.get("payload") or {}),
                    "solicitud_documento_id": str(documento.id),
                    "solicitud_numero_referencia": documento.numero_referencia,
                    "gastos_torneo_id": str(local_tournament.id),
                },
            },
            filters={"id": f"eq.{commitment_id}"},
        )
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="operations.create_solicitud_from_commitment",
            extra={
                "commitment_id": str(commitment_id),
                "documento_id": str(documento.id),
            },
        )

    documento_snapshot = _documento_snapshot(documento)
    return AdapterResult(
        action="operations.create_solicitud_from_commitment",
        status="completed",
        data={
            "ok": True,
            "documento": documento_snapshot,
            "commitment": (
                updated_commitment[0]
                if isinstance(updated_commitment, list) and updated_commitment
                else commitment
            ),
            "source_commitment": commitment,
            "note": "Solicitud creada como borrador desde compromiso operativo. No se registró pago real ni asiento contable.",
        },
        context=context.merge(
            tournament_id=str(local_tournament.id),
            tournament_name=local_tournament.name,
            document_id=str(documento.id),
            need_id=str(documento.id),
            responsible_user_id=str(documento.empleado_id),
            concepto=documento.concepto_pago,
            referencia_operaciones=documento.referencia_operaciones,
        ),
    )


async def assign_expense_accounting_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    numero_referencia = payload.get("numero_referencia")
    if not expense_id and not numero_referencia:
        raise ValueError("expense_id or numero_referencia is required")

    assignment = await finance_expense_assign_accounting(
        session,
        expense_id=expense_id,
        numero_referencia=numero_referencia,
        cuenta_contable_id=payload.get("cuenta_contable_id"),
        cuenta_codigo=payload.get("cuenta_codigo"),
        use_suggested=payload.get("use_suggested", True),
    )
    workflow = await finance_expense_workflow_status(
        session,
        expense_id=assignment.get("expense_id"),
    )
    document_workflow = workflow.get("document_workflow") or {}
    updated_context = context.merge(
        expense_id=str(
            assignment.get("expense_id") or expense_id or context.expense_id
        ),
        document_id=document_workflow.get("documento_id") or context.document_id,
    )
    return AdapterResult(
        action="accounting.assign_expense_accounting",
        status="completed",
        data={
            "assignment": assignment,
            "workflow": workflow,
        },
        context=updated_context,
    )


async def post_expense_accounting_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    numero_referencia = payload.get("numero_referencia")
    actor_id = payload.get("empleado_id") or context.responsible_user_id
    if not expense_id and not numero_referencia:
        raise ValueError("expense_id or numero_referencia is required")
    if not actor_id:
        raise ValueError("empleado_id is required")

    posting = await finance_expense_post_accounting(
        session,
        empleado_id=str(actor_id),
        expense_id=expense_id,
        numero_referencia=numero_referencia,
        tipo_poliza=payload.get("tipo_poliza", "auto"),
        contra_cuenta_contable_id=payload.get("contra_cuenta_contable_id"),
        contra_cuenta_codigo=payload.get("contra_cuenta_codigo"),
        iva_cuenta_contable_id=payload.get("iva_cuenta_contable_id"),
        iva_cuenta_codigo=payload.get("iva_cuenta_codigo"),
        allow_without_cfdi=bool(payload.get("allow_without_cfdi", False)),
    )
    workflow = await finance_expense_workflow_status(
        session,
        expense_id=posting.get("expense_id"),
    )
    document_workflow = workflow.get("document_workflow") or {}
    updated_context = context.merge(
        expense_id=str(posting.get("expense_id") or expense_id or context.expense_id),
        responsible_user_id=str(actor_id),
        accounting_entry_id=posting.get("poliza_id") or context.accounting_entry_id,
        document_id=document_workflow.get("documento_id") or context.document_id,
    )
    return AdapterResult(
        action="accounting.post_expense_accounting",
        status="completed",
        data={
            "posting": posting,
            "workflow": workflow,
        },
        context=updated_context,
    )


async def link_expense_to_cfdi_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    if not expense_id:
        raise ValueError("expense_id is required")

    expense = await session.get(ExpenseReport, _as_uuid(expense_id))
    if expense is None:
        raise ValueError(f"expense {expense_id} not found")

    manual_uuid = payload.get("cfdi_uuid_manual")
    if manual_uuid is not None:
        expense.cfdi_uuid_manual = normalize_cfdi_uuid_to_canonical(str(manual_uuid))

    try:
        linked = await link_expense_to_cfdi_if_manual_uuid_set(
            session,
            expense,
            clear_report_if_no_match=bool(
                payload.get("clear_report_if_no_match", False)
            ),
        )
        await session.commit()
        await session.refresh(expense)
    except ValueError:
        raise
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="receipts.link_expense_to_cfdi",
            extra={"expense_id": str(expense_id)},
        )

    cfdi_report = None
    if getattr(expense, "cfdi_report_id", None):
        cfdi_report = await session.get(CFDIReport, expense.cfdi_report_id)

    expense_snapshot = _expense_snapshot(expense)
    return AdapterResult(
        action="receipts.link_expense_to_cfdi",
        status="completed" if linked else "not_linked",
        data={
            "expense": expense_snapshot,
            "cfdi": _cfdi_snapshot(cfdi_report),
            "linked": linked,
        },
        context=context.merge(
            expense_id=str(expense.id),
            document_id=expense_snapshot.get("solicitud_documento_id")
            or expense_snapshot.get("documento_id"),
            expense_account_id=expense_snapshot.get("cuenta_gastos_id"),
            referencia_base=expense_snapshot.get("referencia_base")
            or context.referencia_base,
            referencia_operaciones=expense_snapshot.get("referencia_operaciones")
            or context.referencia_operaciones,
        ),
    )


async def create_expense_from_operations_context_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    """
    Build a finance-valid expense payload from tournament operations context.

    This adapter does not invent new business logic. It only resolves tournament
    identity, validates the requested phase against the configured tournament
    stages, and delegates to the existing shared expense service.
    """

    tournament_name = (
        payload.get("tournament_name")
        or payload.get("proyecto")
        or context.tournament_name
    )
    tournament_id = payload.get("tournament_id") or context.tournament_id
    tournament = await _resolve_tournament(
        session,
        tournament_id=tournament_id,
        tournament_name=tournament_name,
    )

    resolved_phase = payload.get("fase_torneo") or context.fase_torneo
    if tournament is not None and resolved_phase:
        valid_phases = get_tournament_etapas(tournament)
        if resolved_phase not in valid_phases:
            raise ValueError(
                f"fase_torneo '{resolved_phase}' is not valid for tournament "
                f"'{tournament.name}'. Allowed: {', '.join(valid_phases)}"
            )

    enriched_payload = {
        **payload,
        "tournament_id": (
            str(tournament.id) if tournament is not None else tournament_id
        ),
        "proyecto": tournament.name if tournament is not None else tournament_name,
        "fase_torneo": resolved_phase,
        "concepto": payload.get("concepto") or context.concepto,
        "departamento": payload.get("departamento") or context.departamento,
    }

    result = await create_manual_expense_adapter(
        session,
        context=context.merge(
            tournament_id=enriched_payload.get("tournament_id"),
            tournament_name=enriched_payload.get("proyecto"),
            fase_torneo=enriched_payload.get("fase_torneo"),
            concepto=enriched_payload.get("concepto"),
            departamento=enriched_payload.get("departamento"),
            referencia_operaciones=enriched_payload.get("referencia_operaciones")
            or context.referencia_operaciones,
        ),
        payload=enriched_payload,
    )
    return AdapterResult(
        action="operations.create_expense_from_context",
        status=result.status,
        data={
            "tournament": (
                {
                    "tournament_id": str(tournament.id),
                    "name": tournament.name,
                    "etapas": get_tournament_etapas(tournament),
                }
                if tournament is not None
                else None
            ),
            "expense": result.data,
        },
        context=result.context,
    )


async def create_solicitud_terceros_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    empleado_id = payload.get("empleado_id") or context.responsible_user_id
    if not empleado_id:
        raise ValueError("empleado_id is required")

    try:
        solicitud_payload = build_solicitud_terceros_payload(
            empleado_id=_as_uuid(empleado_id),
            monto_solicitado=payload.get("monto_solicitado"),
            proveedor_cliente_id=payload.get("proveedor_cliente_id"),
            torneo_id=payload.get("torneo_id") or context.tournament_id,
            concepto_pago=payload.get("concepto_pago") or context.concepto,
            fecha_pago=payload.get("fecha_pago"),
            numero_factura=payload.get("numero_factura"),
            referencia_pago=payload.get("referencia_pago"),
            fecha_inicio=payload.get("fecha_inicio"),
            fecha_fin=payload.get("fecha_fin"),
            notas=payload.get("notas"),
            pdf_bytes=_normalize_binary_payload(payload.get("archivo_data")),
            pdf_filename=payload.get("archivo_nombre"),
        )
        documento = await create_solicitud_terceros_document(session, solicitud_payload)
    except SolicitudValidationError as exc:
        raise ValueError(str(exc)) from exc
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="expenses.create_solicitud_terceros",
            extra={"empleado_id": str(empleado_id)},
        )

    updated_context = context.merge(
        tournament_id=(
            str(documento.torneo_id)
            if getattr(documento, "torneo_id", None)
            else context.tournament_id
        ),
        document_id=str(documento.id),
        need_id=str(documento.id),
        responsible_user_id=str(documento.empleado_id),
        concepto=getattr(documento, "concepto_pago", None) or context.concepto,
        referencia_operaciones=getattr(documento, "referencia_operaciones", None),
    )
    return AdapterResult(
        action="expenses.create_solicitud_terceros",
        status="completed",
        data={"documento": _documento_snapshot(documento)},
        context=updated_context,
    )


async def create_solicitud_personal_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    empleado_id = payload.get("empleado_id") or context.responsible_user_id
    if not empleado_id:
        raise ValueError("empleado_id is required")

    cuenta_id = payload.get("cuenta_id") or context.expense_account_id
    if not cuenta_id:
        raise ValueError("cuenta_id is required")

    try:
        solicitud_payload = build_solicitud_personal_payload(
            cuenta_id=cuenta_id,
            empleado_id=_as_uuid(empleado_id),
            monto_solicitado=payload.get("monto_solicitado"),
            concepto_pago=payload.get("concepto_pago") or context.concepto,
            fecha_pago=payload.get("fecha_pago"),
            proveedor_cliente_id=payload.get("proveedor_cliente_id"),
        )
        documento = await create_solicitud_personal_document(session, solicitud_payload)
    except SolicitudValidationError as exc:
        raise ValueError(str(exc)) from exc
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="expenses.create_solicitud_personal",
            extra={"empleado_id": str(empleado_id), "cuenta_id": str(cuenta_id)},
        )

    updated_context = context.merge(
        expense_account_id=str(cuenta_id),
        document_id=str(documento.id),
        need_id=str(documento.id),
        responsible_user_id=str(documento.empleado_id),
        concepto=getattr(documento, "concepto_pago", None) or context.concepto,
        referencia_base=getattr(documento, "referencia_base", None),
        referencia_operaciones=getattr(documento, "referencia_operaciones", None),
    )
    return AdapterResult(
        action="expenses.create_solicitud_personal",
        status="completed",
        data={"documento": _documento_snapshot(documento)},
        context=updated_context,
    )


def _verified_receipt_bytes(payload: Dict[str, Any]) -> bytes:
    encoded = str(payload.get("file_b64") or "")
    if not encoded:
        raise ValueError("file_b64 is required")
    raw = base64.b64decode(encoded, validate=True)
    expected = str(payload.get("evidence_sha256") or "").lower()
    if not expected or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("receipt evidence hash mismatch")
    return raw


async def create_personal_receipt_workflow_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    actor_id = str(context.responsible_user_id or "")
    if not actor_id or not receipt_workflow_writes_enabled(actor_id):
        raise ValueError("receipt workflow writes are disabled for this actor")
    _verified_receipt_bytes(payload)
    result = await create_personal_receipt_workflow(
        session,
        employee_id=UUID(actor_id),
        payload=payload,
        commit=False,
    )
    return AdapterResult(
        action="expenses.create_personal_receipt_workflow",
        status="completed",
        data={
            "account": result.account.to_dict(),
            "expense": _expense_snapshot(result.expense),
            "payment_request": _documento_snapshot(result.payment_request),
        },
        context=context.merge(
            expense_account_id=str(result.account.id),
            expense_id=str(result.expense.id),
            document_id=str(result.payment_request.id),
            referencia_base=result.account.referencia_base,
        ),
    )


async def create_third_party_receipt_workflow_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    actor_id = str(context.responsible_user_id or "")
    if not actor_id or not receipt_workflow_writes_enabled(actor_id):
        raise ValueError("receipt workflow writes are disabled for this actor")
    raw = _verified_receipt_bytes(payload)
    document = await create_third_party_receipt_workflow(
        session,
        employee_id=UUID(actor_id),
        payload={**payload, "file_bytes": raw},
        commit=False,
    )
    return AdapterResult(
        action="expenses.create_third_party_receipt_workflow",
        status="completed",
        data={"payment_request": _documento_snapshot(document)},
        context=context.merge(
            document_id=str(document.id),
            need_id=str(document.id),
            concepto=getattr(document, "concepto_pago", None),
        ),
    )


async def link_bank_movement_to_expense_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    movement_id = payload.get("movement_id")
    expense_id = payload.get("expense_id") or context.expense_id
    empleado_id = payload.get("empleado_id") or context.responsible_user_id
    if not movement_id:
        raise ValueError("movement_id is required")
    if not expense_id:
        raise ValueError("expense_id is required")

    movement = await session.get(BankMovement, _as_uuid(movement_id))
    if movement is None:
        raise ValueError(f"bank movement {movement_id} not found")

    expense = await session.get(ExpenseReport, _as_uuid(expense_id))
    if expense is None:
        raise ValueError(f"expense {expense_id} not found")

    empleado = None
    if empleado_id:
        empleado = await session.get(Empleado, _as_uuid(empleado_id))

    try:
        before_state = _movement_snapshot(movement)
        movement.matched_expense_id = expense.id
        movement.conciliacion_estado = _recompute_conciliacion_estado(movement)
        session.add(
            ReconciliationAuditLog(
                bank_movement_id=movement.id,
                empleado_id=empleado.id if empleado else None,
                action="link_expense",
                before_state=before_state,
                after_state=_movement_snapshot(movement),
                details={
                    "expense_id": str(expense.id),
                    "proyecto": getattr(expense, "proyecto", None),
                    "concepto": getattr(expense, "concepto", None),
                },
            )
        )
        await session.commit()
        await session.refresh(movement)
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="accounting.link_bank_to_expense",
            extra={"movement_id": str(movement_id), "expense_id": str(expense_id)},
        )

    expense_snapshot = _expense_snapshot(expense)
    return AdapterResult(
        action="accounting.link_bank_to_expense",
        status="completed",
        data={
            "movement": _movement_snapshot(movement),
            "expense": expense_snapshot,
        },
        context=context.merge(
            expense_id=str(expense.id),
            accounting_entry_id=(
                str(movement.related_poliza_id)
                if getattr(movement, "related_poliza_id", None)
                else context.accounting_entry_id
            ),
            document_id=expense_snapshot.get("solicitud_documento_id")
            or expense_snapshot.get("documento_id"),
            expense_account_id=expense_snapshot.get("cuenta_gastos_id"),
            referencia_base=expense_snapshot.get("referencia_base")
            or context.referencia_base,
            referencia_operaciones=expense_snapshot.get("referencia_operaciones")
            or context.referencia_operaciones,
        ),
    )


async def expense_full_workflow_snapshot_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    expense_id = payload.get("expense_id") or context.expense_id
    if not expense_id:
        raise ValueError("expense_id is required")

    expense = await session.get(ExpenseReport, _as_uuid(expense_id))
    if expense is None:
        raise ValueError(f"expense {expense_id} not found")

    workflow_snapshot = await finance_expense_workflow_status(
        session,
        expense_id=str(expense.id),
    )
    invoice_stmt = (
        select(InvoiceReport)
        .where(InvoiceReport.expense_id == expense.id)
        .order_by(InvoiceReport.created_at.desc())
    )
    invoice = (await session.execute(invoice_stmt)).scalars().first()

    cfdi_report = None
    if getattr(expense, "cfdi_report_id", None):
        cfdi_report = await session.get(CFDIReport, expense.cfdi_report_id)

    preview = await build_expense_accounting_preview(session, expense)

    movement_stmt = select(BankMovement).where(
        BankMovement.matched_expense_id == expense.id
    )
    movements = list((await session.execute(movement_stmt)).scalars().all())

    poliza = None
    if movements:
        for movement in movements:
            if getattr(movement, "related_poliza_id", None):
                poliza = await session.get(AccountingPoliza, movement.related_poliza_id)
                if poliza is not None:
                    break

    expense_snapshot = _expense_snapshot(expense)
    return AdapterResult(
        action="expense.full_workflow_snapshot",
        status="completed",
        data={
            "workflow": workflow_snapshot,
            "expense": expense_snapshot,
            "invoice": (
                {
                    "invoice_report_id": str(invoice.id),
                    "nova_request_id": getattr(invoice, "nova_request_id", None),
                    "estado_factura": getattr(invoice, "estado_factura", None),
                    "link_xml": getattr(invoice, "link_xml", None),
                    "link_pdf": getattr(invoice, "link_pdf", None),
                    "mensaje_error": getattr(invoice, "mensaje_error", None),
                }
                if invoice is not None
                else None
            ),
            "cfdi": _cfdi_snapshot(cfdi_report),
            "accounting_preview": preview,
            "bank_movements": [_movement_snapshot(movement) for movement in movements],
            "poliza": (
                {
                    "poliza_id": str(poliza.id),
                    "numero": getattr(poliza, "numero_poliza", None),
                    "tipo": getattr(poliza, "tipo_poliza", None),
                    "fecha": (
                        poliza.fecha.isoformat()
                        if getattr(poliza, "fecha", None)
                        else None
                    ),
                    "concepto": getattr(poliza, "concepto", None),
                }
                if poliza is not None
                else None
            ),
        },
        context=context.merge(
            expense_id=str(expense.id),
            accounting_entry_id=(
                str(poliza.id) if poliza is not None else context.accounting_entry_id
            ),
            document_id=expense_snapshot.get("solicitud_documento_id")
            or expense_snapshot.get("documento_id"),
            expense_account_id=expense_snapshot.get("cuenta_gastos_id"),
            referencia_base=expense_snapshot.get("referencia_base")
            or context.referencia_base,
            referencia_operaciones=expense_snapshot.get("referencia_operaciones")
            or context.referencia_operaciones,
        ),
    )


async def pending_document_payment_overview_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    actor_id = payload.get("actor_id") or context.responsible_user_id
    if not actor_id:
        raise ValueError("actor_id is required")

    result = await get_pending_document_payment_overview(
        session,
        actor_id=actor_id,
    )
    return AdapterResult(
        action="receipts.pending_payment_overview",
        status="completed",
        data=result,
        context=context.merge(responsible_user_id=str(actor_id)),
    )


async def register_document_payment_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    document_id = payload.get("document_id") or context.document_id or context.need_id
    actor_id = payload.get("actor_id") or context.responsible_user_id
    if not document_id:
        raise ValueError("document_id is required")
    if not actor_id:
        raise ValueError("actor_id is required")

    try:
        result = await register_document_payment(
            session,
            documento_id=document_id,
            actor_id=actor_id,
        )
    except ValueError:
        raise
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="receipts.register_document_payment",
            extra={"document_id": str(document_id), "actor_id": str(actor_id)},
        )
    documento_snapshot = _documento_snapshot(result.documento)
    aprobacion_snapshot = _aprobacion_snapshot(result.aprobacion)
    expense_snapshot = (
        _expense_snapshot(result.expense) if result.expense is not None else None
    )
    return AdapterResult(
        action="receipts.register_document_payment",
        status="completed",
        data={
            "documento": documento_snapshot,
            "aprobacion": aprobacion_snapshot,
            "expense": expense_snapshot,
        },
        context=context.merge(
            need_id=documento_snapshot["documento_id"],
            document_id=documento_snapshot["documento_id"],
            responsible_user_id=str(actor_id),
            expense_id=expense_snapshot["expense_id"] if expense_snapshot else None,
            tournament_id=documento_snapshot.get("torneo_id") or context.tournament_id,
            expense_account_id=documento_snapshot.get("cuenta_gastos_id")
            or context.expense_account_id,
            referencia_base=documento_snapshot.get("referencia_base")
            or context.referencia_base,
            referencia_operaciones=(
                documento_snapshot.get("referencia_operaciones")
                or context.referencia_operaciones
            ),
        ),
    )


async def register_document_reembolso_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    document_id = payload.get("document_id") or context.document_id or context.need_id
    actor_id = payload.get("actor_id") or context.responsible_user_id
    if not document_id:
        raise ValueError("document_id is required")
    if not actor_id:
        raise ValueError("actor_id is required")

    try:
        result = await register_document_reembolso(
            session,
            documento_id=document_id,
            actor_id=actor_id,
            monto=payload.get("monto"),
            moneda=payload.get("moneda"),
            metodo_pago=payload.get("metodo_pago"),
            fecha_pago=payload.get("fecha_pago"),
            estado=payload.get("estado", "pagado"),
        )
    except ValueError:
        raise
    except Exception:
        await _raise_unexpected_adapter_error(
            session,
            action="receipts.register_document_reembolso",
            extra={"document_id": str(document_id), "actor_id": str(actor_id)},
        )
    documento_snapshot = _documento_snapshot(result.documento)
    reembolso_snapshot = _reembolso_snapshot(result.reembolso)
    return AdapterResult(
        action="receipts.register_document_reembolso",
        status="completed",
        data={
            "documento": documento_snapshot,
            "reembolso": reembolso_snapshot,
        },
        context=context.merge(
            need_id=documento_snapshot["documento_id"],
            document_id=documento_snapshot["documento_id"],
            responsible_user_id=str(actor_id),
            tournament_id=documento_snapshot.get("torneo_id") or context.tournament_id,
            expense_account_id=documento_snapshot.get("cuenta_gastos_id")
            or context.expense_account_id,
            referencia_base=documento_snapshot.get("referencia_base")
            or context.referencia_base,
            referencia_operaciones=(
                documento_snapshot.get("referencia_operaciones")
                or context.referencia_operaciones
            ),
        ),
    )


def _tournament_command_scope(
    context: AssistantContext, payload: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "tournament_key": str(
            payload.get("tournament_key")
            or context.sport
            or context.tournament_name
            or context.tournament_id
            or "all"
        ),
        "tournament_slug": payload.get("tournament_slug")
        or payload.get("slug")
        or context.tournament_id,
        "tournament_name": payload.get("tournament_name") or context.tournament_name,
    }


async def operations_update_team_status_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    team_id = payload.get("team_id") or context.team_id
    team_name = payload.get("team_name")
    status = str(payload.get("status") or "").strip().lower()
    if not (team_id or team_name):
        raise ValueError("team_id or team_name is required")
    if status not in {"pending", "approved", "rejected", "paid"}:
        raise ValueError("status must be one of: pending, approved, rejected, paid")

    result = await update_team_fields_v2(
        **_tournament_command_scope(context, payload),
        team_id=str(team_id) if team_id else None,
        team_name=str(team_name) if team_name else None,
        updates={"status": status},
        dry_run=bool(payload.get("dry_run", False)),
    )
    return AdapterResult(
        action="operations.update_team_status",
        status="completed",
        data=result,
        context=context.merge(
            tournament_id=(result.get("tournament") or {}).get("id")
            or context.tournament_id,
            tournament_name=(result.get("tournament") or {}).get("name")
            or context.tournament_name,
            team_id=(result.get("team") or {}).get("id") or context.team_id,
        ),
    )


async def operations_verify_player_document_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    updates: Dict[str, Any] = {}
    if "documents_complete" in payload:
        updates["documents_complete"] = bool(payload.get("documents_complete"))
    if "documents_verified" in payload:
        updates["documents_verified"] = bool(payload.get("documents_verified"))
    if not updates:
        raise ValueError("documents_complete or documents_verified is required")

    result = await update_player_fields_v2(
        **_tournament_command_scope(context, payload),
        team_id=payload.get("team_id") or context.team_id,
        team_name=payload.get("team_name"),
        category_id=payload.get("category_id"),
        category_name=payload.get("category_name") or context.category,
        match_curp=payload.get("match_curp") or payload.get("curp"),
        match_first_name=payload.get("match_first_name") or payload.get("first_name"),
        match_last_name=payload.get("match_last_name") or payload.get("last_name"),
        match_birth_date=payload.get("match_birth_date") or payload.get("birth_date"),
        updates=updates,
        dry_run=bool(payload.get("dry_run", False)),
    )
    return AdapterResult(
        action="operations.verify_player_document",
        status="completed",
        data=result,
        context=context.merge(
            tournament_id=(result.get("tournament") or {}).get("id")
            or context.tournament_id,
            tournament_name=(result.get("tournament") or {}).get("name")
            or context.tournament_name,
            team_id=(result.get("team") or {}).get("id") or context.team_id,
            category=(result.get("category") or {}).get("name") or context.category,
        ),
    )


async def operations_create_media_asset_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    result = await create_media_asset_v2(
        **_tournament_command_scope(context, payload),
        asset_type=str(payload.get("asset_type") or "photo"),
        title=str(payload.get("title") or ""),
        description=payload.get("description"),
        url=payload.get("url")
        or payload.get("image_url")
        or payload.get("video_url")
        or payload.get("stream_url"),
        thumbnail_url=payload.get("thumbnail_url"),
        category_id=payload.get("category_id"),
        category_name=payload.get("category_name") or context.category,
        video_type=str(payload.get("video_type") or "highlight"),
        platform=str(payload.get("platform") or "youtube"),
        scheduled_time=payload.get("scheduled_time"),
        status=str(payload.get("status") or "scheduled"),
        dry_run=bool(payload.get("dry_run", False)),
    )
    return AdapterResult(
        action="operations.create_media_asset",
        status="completed",
        data=result,
        context=context.merge(
            tournament_id=(result.get("tournament") or {}).get("id")
            or context.tournament_id,
            tournament_name=(result.get("tournament") or {}).get("name")
            or context.tournament_name,
            category=(result.get("category") or {}).get("name") or context.category,
        ),
    )


async def operations_send_tournament_reminder_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    channel = str(payload.get("channel") or "whatsapp").strip().lower()
    reminder_type = str(payload.get("reminder_type") or "custom").strip().lower()
    message = str(payload.get("message") or "").strip()
    if channel != "whatsapp":
        raise ValueError("Only whatsapp reminders are currently enabled")
    if not message and reminder_type == "documents":
        message = "Te recordamos completar la documentación pendiente de tu equipo."
    if not message:
        raise ValueError("message is required")

    result = await send_tournament_whatsapp_adapter(
        session,
        context=context,
        payload={
            **payload,
            "message": message,
            "template_type": payload.get("template_type"),
            "recipients": payload.get("recipients") or [],
        },
    )
    return AdapterResult(
        action="operations.send_tournament_reminder",
        status="completed",
        data={
            **result.data,
            "reminder_type": reminder_type,
            "channel": channel,
        },
        context=result.context,
    )


async def send_tournament_email_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    tournament_id = payload.get("tournament_id") or context.tournament_id
    actor_id = (
        payload.get("empleado_id")
        or payload.get("actor_id")
        or context.responsible_user_id
    )
    recipients = payload.get("recipients") or []
    subject = str(payload.get("subject") or "").strip()
    html_content = str(payload.get("html_content") or payload.get("body") or "").strip()
    text_content = str(
        payload.get("text_content") or ""
    ).strip() or _plain_text_from_html(html_content)
    scheduled_at = _normalize_optional_datetime(payload.get("scheduled_at"))

    if not tournament_id:
        raise ValueError("tournament_id is required")
    if not actor_id:
        raise ValueError("empleado_id/actor_id is required")
    if not isinstance(recipients, list) or not recipients:
        raise ValueError("recipients must be a non-empty list")
    if not subject:
        raise ValueError("subject is required")
    if not html_content and not text_content:
        raise ValueError("html_content or text_content is required")

    normalized_recipients = []
    for row in recipients[:500]:
        if not isinstance(row, dict):
            raise ValueError("each recipient must be an object")
        email = str(row.get("email") or "").strip().lower()
        if "@" not in email:
            raise ValueError("recipient email is invalid")
        normalized_recipients.append(
            {
                "email": email,
                "name": str(row.get("name") or "").strip() or None,
            }
        )

    if scheduled_at:
        rows = await _supabase_rest_mutate(
            "scheduled_emails",
            method="POST",
            payload={
                "scheduled_at": scheduled_at.isoformat(),
                "subject": subject,
                "html_content": html_content or text_content,
                "text_content": text_content or None,
                "recipients": normalized_recipients,
                "created_by": str(actor_id),
                "tournament_id": str(tournament_id),
            },
        )
        communication = rows[0] if isinstance(rows, list) and rows else {}
        mode = "scheduled_email"
    else:
        rows = await _supabase_rest_mutate(
            "email_send_log",
            method="POST",
            payload={
                "sent_by": str(actor_id),
                "recipient_count": len(normalized_recipients),
                "subject": subject,
                "status": "assistant_confirmed",
                "error_message": None,
                "tournament_id": str(tournament_id),
            },
        )
        communication = rows[0] if isinstance(rows, list) and rows else {}
        mode = "email_send_log"

    return AdapterResult(
        action="communications.send_tournament_email",
        status="completed",
        data={
            "delivery_mode": mode,
            "communication": communication,
            "recipient_count": len(normalized_recipients),
            "subject": subject,
            "tournament_id": str(tournament_id),
            "requires_operator_delivery": not bool(scheduled_at),
            "notes": [
                "Accion ejecutada despues de confirmacion del asistente.",
                "El envio inmediato queda registrado como intencion confirmada; el operador/email worker conserva el envio real.",
            ],
        },
        context=context.merge(
            tournament_id=str(tournament_id),
            responsible_user_id=str(actor_id),
        ),
    )


async def send_tournament_whatsapp_adapter(
    session: AsyncSession,
    *,
    context: AssistantContext,
    payload: Dict[str, Any],
) -> AdapterResult:
    tournament_id = payload.get("tournament_id") or context.tournament_id
    actor_id = (
        payload.get("empleado_id")
        or payload.get("actor_id")
        or context.responsible_user_id
    )
    recipients = payload.get("recipients") or []
    message = str(
        payload.get("message") or payload.get("message_content") or ""
    ).strip()
    template_type = str(
        payload.get("template_type") or payload.get("templateType") or ""
    ).strip()
    message_type = "template" if template_type else "custom"
    message_content = message or (
        f"[Template: {template_type}]" if template_type else ""
    )

    if not tournament_id:
        raise ValueError("tournament_id is required")
    if not actor_id:
        raise ValueError("empleado_id/actor_id is required")
    if not isinstance(recipients, list) or not recipients:
        raise ValueError("recipients must be a non-empty list")
    if not message_content:
        raise ValueError("message or template_type is required")

    rows_to_insert = []
    for row in recipients[:500]:
        if not isinstance(row, dict):
            raise ValueError("each recipient must be an object")
        phone = _digits_only(
            row.get("phone") or row.get("to") or row.get("recipient_phone")
        )
        if len(phone) < 10:
            raise ValueError("recipient phone is invalid")
        rows_to_insert.append(
            {
                "team_id": str(row.get("team_id")) if row.get("team_id") else None,
                "tournament_id": str(tournament_id),
                "recipient_phone": phone,
                "recipient_name": str(
                    row.get("name") or row.get("recipient_name") or ""
                ).strip()
                or None,
                "message_type": message_type,
                "message_content": message_content,
                "message_sid": None,
                "status": "assistant_confirmed",
                "sent_by": str(actor_id),
                "direction": "outgoing",
                "is_read": True,
                "sent_at": datetime.utcnow().isoformat(),
            }
        )

    try:
        rows = await _supabase_rest_mutate(
            "whatsapp_message_log",
            method="POST",
            payload=rows_to_insert,
        )
    except RuntimeError as exc:
        if "direction" not in str(exc) and "is_read" not in str(exc):
            raise
        fallback_rows = [
            {
                key: value
                for key, value in row.items()
                if key not in {"direction", "is_read"}
            }
            for row in rows_to_insert
        ]
        rows = await _supabase_rest_mutate(
            "whatsapp_message_log",
            method="POST",
            payload=fallback_rows,
        )

    return AdapterResult(
        action="communications.send_tournament_whatsapp",
        status="completed",
        data={
            "delivery_mode": "whatsapp_message_log",
            "messages": rows if isinstance(rows, list) else [],
            "recipient_count": len(rows_to_insert),
            "message_type": message_type,
            "tournament_id": str(tournament_id),
            "requires_operator_delivery": True,
            "notes": [
                "Accion ejecutada despues de confirmacion del asistente.",
                "El mensaje queda visible en el historial WhatsApp como salida confirmada por asistente.",
            ],
        },
        context=context.merge(
            tournament_id=str(tournament_id),
            responsible_user_id=str(actor_id),
        ),
    )


CanonicalAdapter = Callable[
    [AsyncSession], Awaitable[AdapterResult]
]  # pragma: no cover - documentation alias only
