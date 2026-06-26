"""Read-only finance control projection over gastos, COI, DIOT and pagos."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _is_missing(value: Any) -> bool:
    return not _safe_str(value)


def _has_cfdi(row: dict[str, Any]) -> bool:
    return bool(
        _safe_str(row.get("cfdi_report_id"))
        or _safe_str(row.get("cfdi_uuid_manual"))
        or _safe_str(row.get("cfdi_uuid"))
    )


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _safe_str(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _period_label(value: Any) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return "sin fecha"
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _expense_cfdi_period_warning(expense: dict[str, Any]) -> str | None:
    gasto_fecha = _parse_iso_datetime(expense.get("fecha"))
    cfdi_fecha = _parse_iso_datetime(expense.get("cfdi_fecha"))
    if gasto_fecha is None or cfdi_fecha is None:
        return None
    if (gasto_fecha.year, gasto_fecha.month) == (cfdi_fecha.year, cfdi_fecha.month):
        return None
    return (
        f"CFDI {cfdi_fecha.year:04d}-{cfdi_fecha.month:02d} distinto al gasto "
        f"{gasto_fecha.year:04d}-{gasto_fecha.month:02d}."
    )


def _period_bounds(
    year: int | None, month: int | None
) -> tuple[int, int, datetime, datetime]:
    now = datetime.utcnow()
    period_year = int(year or now.year)
    period_month = max(1, min(int(month or now.month), 12))
    start = datetime(period_year, period_month, 1)
    end = (
        datetime(period_year + 1, 1, 1)
        if period_month == 12
        else datetime(period_year, period_month + 1, 1)
    )
    return period_year, period_month, start, end


def _action_item(
    *,
    severity: str,
    module: str,
    title: str,
    detail: str,
    owner: str = "Finanzas",
    due: str = "hoy",
    href: str = "/admin/gastos",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "module": module,
        "title": title,
        "detail": detail,
        "owner": owner,
        "due": due,
        "href": href,
    }


async def build_finance_source_snapshot(
    session: AsyncSession,
    *,
    year: int | None = None,
    month: int | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    """Read current finance source rows and normalize them for UI projections."""
    from devnous.gastos.models import AccountingPoliza, Documento, ExpenseReport

    period_year, period_month, start, end = _period_bounds(year, month)

    document_stmt = (
        select(Documento)
        .options(
            selectinload(Documento.proveedor_cliente),
            selectinload(Documento.empleado),
            selectinload(Documento.beneficiario_empleado),
        )
        .where(
            or_(
                and_(Documento.creado_en >= start, Documento.creado_en < end),
                and_(Documento.aprobado_en >= start, Documento.aprobado_en < end),
                and_(Documento.pagado_en >= start, Documento.pagado_en < end),
                Documento.estado.in_(["enviado", "aprobado"]),
            )
        )
        .order_by(Documento.creado_en.desc())
        .limit(limit)
    )
    expense_stmt = (
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.empleado),
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.contra_cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(
            and_(
                ExpenseReport.estado_gasto != "cancelado",
                or_(
                    and_(
                        ExpenseReport.created_at >= start,
                        ExpenseReport.created_at < end,
                    ),
                    and_(ExpenseReport.fecha >= start, ExpenseReport.fecha < end),
                    ExpenseReport.estado_reembolso.in_(["pendiente", "aprobado"]),
                ),
            )
        )
        .order_by(ExpenseReport.created_at.desc())
        .limit(limit)
    )
    poliza_stmt = (
        select(AccountingPoliza)
        .options(selectinload(AccountingPoliza.lines))
        .where(
            or_(
                and_(
                    AccountingPoliza.fecha_poliza >= start,
                    AccountingPoliza.fecha_poliza < end,
                ),
                and_(
                    AccountingPoliza.created_at >= start,
                    AccountingPoliza.created_at < end,
                ),
            )
        )
        .order_by(
            AccountingPoliza.fecha_poliza.desc().nullslast(),
            AccountingPoliza.created_at.desc(),
        )
        .limit(limit)
    )

    documents = (await session.execute(document_stmt)).scalars().all()
    expenses = (await session.execute(expense_stmt)).scalars().all()
    polizas = (await session.execute(poliza_stmt)).scalars().all()

    return {
        "period": {"year": period_year, "month": period_month},
        "documents": [_serialize_document(document) for document in documents],
        "expenses": [_serialize_expense(expense) for expense in expenses],
        "polizas": [_serialize_poliza(poliza) for poliza in polizas],
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _serialize_document(document: Any) -> dict[str, Any]:
    proveedor = getattr(document, "proveedor_cliente", None)
    empleado = getattr(document, "beneficiario_empleado", None) or getattr(
        document, "empleado", None
    )
    return {
        "entity_type": "documento",
        "id": str(getattr(document, "id", "")),
        "tipo": getattr(document, "tipo", None),
        "numero_referencia": getattr(document, "numero_referencia", None),
        "estado": getattr(document, "estado", None),
        "monto_total": _safe_float(getattr(document, "monto_total", None)),
        "monto_solicitado": _safe_float(getattr(document, "monto_solicitado", None)),
        "creado_en": _iso(getattr(document, "creado_en", None)),
        "aprobado_en": _iso(getattr(document, "aprobado_en", None)),
        "pagado_en": _iso(getattr(document, "pagado_en", None)),
        "fecha_pago": _iso(getattr(document, "fecha_pago", None)),
        "metodo_pago": getattr(document, "metodo_pago", None),
        "cfdi_uuid_manual": getattr(document, "cfdi_uuid_manual", None),
        "cfdi_report_id": str(getattr(document, "cfdi_report_id", "") or ""),
        "cuenta_gastos_id": str(getattr(document, "cuenta_gastos_id", "") or ""),
        "gasto_generado_id": str(getattr(document, "gasto_generado_id", "") or ""),
        "proveedor_nombre": getattr(proveedor, "nombre", None),
        "beneficiario_nombre": getattr(empleado, "nombre", None),
    }


def _serialize_expense(expense: Any) -> dict[str, Any]:
    empleado = getattr(expense, "empleado", None)
    cfdi = getattr(expense, "cfdi_report", None)
    cuenta_contable = getattr(expense, "cuenta_contable", None)
    row = {
        "entity_type": "expense",
        "id": str(getattr(expense, "id", "")),
        "numero_referencia": getattr(expense, "numero_referencia", None),
        "concepto": getattr(expense, "concepto", None),
        "proyecto": getattr(expense, "proyecto", None),
        "estado_reembolso": getattr(expense, "estado_reembolso", None),
        "estado_gasto": getattr(expense, "estado_gasto", None),
        "gasto_cantidad": _safe_float(getattr(expense, "gasto_cantidad", None)),
        "iva": _safe_float(getattr(expense, "iva", None)),
        "metodo_pago": getattr(expense, "metodo_pago", None),
        "origen": getattr(expense, "origen", None),
        "cuenta_contable_id": str(getattr(expense, "cuenta_contable_id", "") or ""),
        "cuenta_contable_nombre": getattr(cuenta_contable, "nombre", None),
        "contra_cuenta_contable_id": str(
            getattr(expense, "contra_cuenta_contable_id", "") or ""
        ),
        "cuenta_iva_id": str(getattr(expense, "cuenta_iva_id", "") or ""),
        "cfdi_uuid_manual": getattr(expense, "cfdi_uuid_manual", None),
        "cfdi_report_id": str(getattr(expense, "cfdi_report_id", "") or ""),
        "cfdi_fecha": _iso(getattr(cfdi, "fecha", None)),
        "created_at": _iso(getattr(expense, "created_at", None)),
        "fecha": _iso(getattr(expense, "fecha", None)),
        "empleado_nombre": getattr(empleado, "nombre", None),
    }
    row["cfdi_period_warning"] = _expense_cfdi_period_warning(row)
    return row


def _serialize_poliza(poliza: Any) -> dict[str, Any]:
    lines = list(getattr(poliza, "lines", None) or [])
    debe = sum(_safe_float(getattr(line, "debe", None)) for line in lines)
    haber = sum(_safe_float(getattr(line, "haber", None)) for line in lines)
    return {
        "id": str(getattr(poliza, "id", "")),
        "tipo_poliza": getattr(poliza, "tipo_poliza", None),
        "numero_poliza": getattr(poliza, "numero_poliza", None),
        "fecha_poliza": _iso(getattr(poliza, "fecha_poliza", None)),
        "beneficiario_nombre": getattr(poliza, "beneficiario_nombre", None),
        "concepto": getattr(poliza, "concepto", None),
        "origen": getattr(poliza, "origen", None),
        "cfdi_uuid": getattr(poliza, "cfdi_uuid", None),
        "cfdi_report_id": str(getattr(poliza, "cfdi_report_id", "") or ""),
        "line_count": len(lines),
        "debe": round(debe, 2),
        "haber": round(haber, 2),
    }


def build_finance_action_queue(snapshot: dict[str, Any]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for document in snapshot.get("documents") or []:
        estado = _safe_str(document.get("estado")).lower()
        tipo = _safe_str(document.get("tipo")).upper()
        ref = _safe_str(document.get("numero_referencia")) or _safe_str(
            document.get("id")
        )
        amount = _safe_float(
            document.get("monto_total") or document.get("monto_solicitado")
        )
        if (
            tipo == "SOLICITUD"
            and estado == "aprobado"
            and not document.get("pagado_en")
        ):
            actions.append(
                _action_item(
                    severity="high",
                    module="Pagos",
                    title=f"Pagar {tipo or 'documento'} {ref}",
                    detail=f"Autorizado y no pagado por ${amount:,.2f}.",
                    href=f"/admin/documentos/{document.get('id')}",
                )
            )
        if (
            tipo == "INFORME"
            and estado in {"aprobado", "cerrado"}
            and not document.get("cuenta_gastos_id")
        ):
            actions.append(
                _action_item(
                    severity="medium",
                    module="Cuenta de gastos",
                    title=f"Ligar informe {ref} a cuenta de gastos",
                    detail="Sin cuenta de gastos falta trazabilidad para COI.",
                )
            )
        if estado in {"aprobado", "pagado", "cerrado"} and not _has_cfdi(document):
            actions.append(
                _action_item(
                    severity="medium",
                    module="DIOT / CFDI",
                    title=f"Completar CFDI de {ref}",
                    detail="Falta CFDI/UUID para amarre fiscal y DIOT.",
                    href="/admin/cfdi",
                )
            )

    for expense in snapshot.get("expenses") or []:
        estado = _safe_str(expense.get("estado_reembolso")).lower()
        ref = _safe_str(expense.get("numero_referencia")) or _safe_str(
            expense.get("id")
        )
        if estado in {"pendiente", "aprobado"} and (
            _is_missing(expense.get("cuenta_contable_id"))
            or _is_missing(expense.get("contra_cuenta_contable_id"))
        ):
            actions.append(
                _action_item(
                    severity="high",
                    module="COI",
                    title=f"Clasificar cuentas de gasto {ref}",
                    detail="Falta cuenta contable o contracuenta; póliza incompleta.",
                    href="/admin/cuentas-contables",
                )
            )
        if estado in {"aprobado", "pagado"} and not _has_cfdi(expense):
            actions.append(
                _action_item(
                    severity="medium",
                    module="DIOT / CFDI",
                    title=f"Amarrar CFDI de gasto {ref}",
                    detail="Sin CFDI no debe cerrarse la preparación DIOT.",
                    href="/admin/cfdi",
                )
            )
        period_warning = _expense_cfdi_period_warning(expense)
        if period_warning:
            actions.append(
                _action_item(
                    severity="medium",
                    module="Comprobantes",
                    title=f"Revisar mes CFDI de {ref}",
                    detail=period_warning,
                    href="/admin/finanzas",
                )
            )

    for poliza in snapshot.get("polizas") or []:
        delta = round(
            _safe_float(poliza.get("debe")) - _safe_float(poliza.get("haber")),
            2,
        )
        if abs(delta) > 0.01:
            actions.append(
                _action_item(
                    severity="high",
                    module="Cierre contable",
                    title=(
                        f"Cuadrar póliza {poliza.get('tipo_poliza')}-"
                        f"{poliza.get('numero_poliza')}"
                    ),
                    detail=f"Diferencia debe/haber de ${delta:,.2f}.",
                    href="/admin/contabilidad/historica",
                )
            )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    actions.sort(
        key=lambda item: (
            severity_rank.get(item["severity"], 9),
            item["module"],
            item["title"],
        )
    )
    return {
        "title": "Finance Action Queue",
        "actions": actions[:40],
        "open_count": len(actions),
        "high_count": sum(1 for action in actions if action["severity"] == "high"),
        "medium_count": sum(1 for action in actions if action["severity"] == "medium"),
        "low_count": sum(1 for action in actions if action["severity"] == "low"),
        "read_only": True,
    }


def _build_cash_control(snapshot: dict[str, Any]) -> dict[str, Any]:
    documents = list(snapshot.get("documents") or [])
    polizas = list(snapshot.get("polizas") or [])
    approved_unpaid = [
        row
        for row in documents
        if _safe_str(row.get("estado")).lower() == "aprobado"
        and not row.get("pagado_en")
    ]
    paid_documents = [
        row
        for row in documents
        if _safe_str(row.get("estado")).lower() == "pagado" or row.get("pagado_en")
    ]
    income_polizas = [
        row
        for row in polizas
        if _safe_str(row.get("tipo_poliza")).lower() in {"ig", "ingreso"}
    ]
    approved_unpaid_total = sum(
        _safe_float(row.get("monto_total") or row.get("monto_solicitado"))
        for row in approved_unpaid
    )
    paid_total = sum(
        _safe_float(row.get("monto_total") or row.get("monto_solicitado"))
        for row in paid_documents
    )
    income_total = sum(
        max(_safe_float(row.get("debe")), _safe_float(row.get("haber")))
        for row in income_polizas
    )
    return {
        "title": "Cash Control Center",
        "approved_unpaid_count": len(approved_unpaid),
        "approved_unpaid_total": round(approved_unpaid_total, 2),
        "paid_documents_count": len(paid_documents),
        "paid_total": round(paid_total, 2),
        "income_polizas_count": len(income_polizas),
        "income_total": round(income_total, 2),
        "payment_pressure": "high" if approved_unpaid_total > 0 else "normal",
        "approved_unpaid": approved_unpaid[:15],
    }


def _build_accounting_close(snapshot: dict[str, Any]) -> dict[str, Any]:
    expenses = list(snapshot.get("expenses") or [])
    polizas = list(snapshot.get("polizas") or [])
    unbalanced = [
        row
        for row in polizas
        if abs(_safe_float(row.get("debe")) - _safe_float(row.get("haber"))) > 0.01
    ]
    ready_expenses = [
        row
        for row in expenses
        if row.get("cuenta_contable_id")
        and row.get("contra_cuenta_contable_id")
        and _has_cfdi(row)
    ]
    pending_expenses = [
        row
        for row in expenses
        if not (
            row.get("cuenta_contable_id")
            and row.get("contra_cuenta_contable_id")
            and _has_cfdi(row)
        )
    ]
    cross_month_receipts = [
        row for row in expenses if _expense_cfdi_period_warning(row) is not None
    ]
    return {
        "title": "Accounting Close Center",
        "polizas_count": len(polizas),
        "unbalanced_count": len(unbalanced),
        "unbalanced_polizas": unbalanced[:10],
        "coi_ready_expenses_count": len(ready_expenses),
        "pending_coi_expenses_count": len(pending_expenses),
        "pending_coi_expenses": pending_expenses[:25],
        "cross_month_receipts_count": len(cross_month_receipts),
        "cross_month_receipts": cross_month_receipts[:20],
        "coi_ready_ratio": (
            round(len(ready_expenses) / len(expenses), 4) if expenses else 0
        ),
    }


def _build_tax_readiness(snapshot: dict[str, Any]) -> dict[str, Any]:
    documents = [
        {"entity_type": "documento", **dict(row)}
        for row in snapshot.get("documents") or []
    ]
    expenses = [
        {"entity_type": "expense", **dict(row)}
        for row in snapshot.get("expenses") or []
    ]
    fiscal_rows = documents + expenses
    missing_cfdi = [row for row in fiscal_rows if not _has_cfdi(row)]
    amex_rows = [
        row
        for row in snapshot.get("expenses") or []
        if "amex" in _safe_str(row.get("metodo_pago") or row.get("origen")).lower()
    ]
    cross_month_receipts = [
        row
        for row in snapshot.get("expenses") or []
        if _expense_cfdi_period_warning(row)
    ]
    return {
        "title": "Tax Readiness",
        "cfdi_missing_count": len(missing_cfdi),
        "diot_blockers_count": len(missing_cfdi),
        "cross_month_receipts_count": len(cross_month_receipts),
        "amex_rows_count": len(amex_rows),
        "amex_tip_attention_count": len(
            [row for row in amex_rows if _safe_float(row.get("gasto_cantidad")) > 0]
        ),
        "status": "blocked" if missing_cfdi else "ready",
        "blockers": missing_cfdi[:12],
        "cross_month_receipts": cross_month_receipts[:12],
    }


def _build_payment_run(snapshot: dict[str, Any]) -> dict[str, Any]:
    payable = [
        row
        for row in snapshot.get("documents") or []
        if _safe_str(row.get("estado")).lower() == "aprobado"
        and _safe_str(row.get("tipo")).upper() == "SOLICITUD"
        and not row.get("pagado_en")
    ]
    payable_total = sum(
        _safe_float(row.get("monto_total") or row.get("monto_solicitado"))
        for row in payable
    )
    return {
        "title": "Payment Run",
        "payable_count": len(payable),
        "payable_total": round(payable_total, 2),
        "items": payable[:20],
        "next_step": (
            "Registrar pago y generar póliza"
            if payable
            else "Sin pagos autorizados pendientes"
        ),
    }


def _build_finance_copilot(
    snapshot: dict[str, Any], action_queue: dict[str, Any]
) -> dict[str, Any]:
    period = snapshot.get("period") or {}
    return {
        "title": "Finance Copilot",
        "suggested_prompts": [
            (
                f"Prepara el cierre de {period.get('month')}/{period.get('year')} "
                "y dime que bloquea COI."
            ),
            "Dame las pólizas descuadradas con monto y origen.",
            "Genera el run de pagos autorizados de hoy.",
            "Lista gastos AMEX donde deba inferirse propina contra voucher.",
            "Dime que documentos bloquean la DIOT por CFDI faltante.",
        ],
        "open_actions": action_queue.get("open_count", 0),
        "read_only": True,
    }


def _build_finance_brief(
    snapshot: dict[str, Any],
    *,
    action_queue: dict[str, Any],
    cash_control: dict[str, Any],
    accounting_close: dict[str, Any],
    tax_readiness: dict[str, Any],
    payment_run: dict[str, Any],
) -> dict[str, Any]:
    period = snapshot.get("period") or {}
    lines = [
        f"Brief financiero {period.get('month')}/{period.get('year')}",
        (
            f"- Acciones abiertas: {action_queue.get('open_count', 0)} "
            f"({action_queue.get('high_count', 0)} alta prioridad)."
        ),
        (
            f"- Pagos autorizados pendientes: {payment_run.get('payable_count', 0)} "
            f"por ${payment_run.get('payable_total', 0):,.2f}."
        ),
        (
            f"- COI: {accounting_close.get('coi_ready_expenses_count', 0)} "
            f"gastos listos, {accounting_close.get('pending_coi_expenses_count', 0)} "
            "pendientes."
        ),
        (
            f"- Comprobantes otro mes: "
            f"{tax_readiness.get('cross_month_receipts_count', 0)}."
        ),
        f"- Pólizas descuadradas: {accounting_close.get('unbalanced_count', 0)}.",
        f"- DIOT/CFDI: {tax_readiness.get('diot_blockers_count', 0)} bloqueo(s).",
        (
            f"- Ingresos contabilizados en pólizas: "
            f"${cash_control.get('income_total', 0):,.2f}."
        ),
    ]
    if action_queue.get("actions"):
        first = action_queue["actions"][0]
        lines.append(
            f"- Siguiente acción: {first.get('title')} ({first.get('module')})."
        )
    return {
        "title": "One-click Finance Brief",
        "plain_text": "\n".join(lines),
        "whatsapp_text": " | ".join(lines[:4]),
        "email_subject": f"Brief financiero {period.get('month')}/{period.get('year')}",
        "export_targets": ["WhatsApp", "Email", "PDF"],
        "pdf_ready": True,
    }


def build_finance_platform_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    action_queue = build_finance_action_queue(snapshot)
    cash_control = _build_cash_control(snapshot)
    accounting_close = _build_accounting_close(snapshot)
    tax_readiness = _build_tax_readiness(snapshot)
    payment_run = _build_payment_run(snapshot)
    finance_copilot = _build_finance_copilot(snapshot, action_queue)
    finance_brief = _build_finance_brief(
        snapshot,
        action_queue=action_queue,
        cash_control=cash_control,
        accounting_close=accounting_close,
        tax_readiness=tax_readiness,
        payment_run=payment_run,
    )
    return {
        "ok": True,
        "read_only": True,
        "period": snapshot.get("period") or {},
        "summary": {
            "documents": len(snapshot.get("documents") or []),
            "expenses": len(snapshot.get("expenses") or []),
            "polizas": len(snapshot.get("polizas") or []),
            "open_actions": action_queue.get("open_count", 0),
            "payment_pressure": cash_control.get("payment_pressure"),
            "tax_status": tax_readiness.get("status"),
        },
        "action_queue": action_queue,
        "finance_brief": finance_brief,
        "cash_control_center": cash_control,
        "accounting_close_center": accounting_close,
        "tax_readiness": tax_readiness,
        "payment_run": payment_run,
        "finance_copilot": finance_copilot,
    }
