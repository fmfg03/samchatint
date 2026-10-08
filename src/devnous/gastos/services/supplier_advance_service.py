"""Supplier advance lifecycle owned by the existing document workflow.

No commits or schema writes: caller owns the transaction. A paid advance is
immutable; an invoice child reserves its allocation until rejected/cancelled.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import undefer

from ..models import (
    Aprobacion,
    CFDIReport,
    CuentaContable,
    Documento,
    ProveedorCliente,
    RFCConfig,
)
from .cfdi_ingestion_service import ingest_cfdi_from_upload
from .cfdi_upload_resolver import resolve_cfdi_upload
from .documento_service import (
    SolicitudTercerosAttachment,
    SolicitudValidationError,
    _persist_solicitud_terceros_adjuntos,
    generate_documento_reference_number,
    parse_optional_date,
    validate_solicitud_terceros_attachment,
)

ADVANCE_ACCOUNT = "1215-001-001"
LIABILITY_ACCOUNT = "2120-002-099"
RELEASED_STATES = {"rechazado", "cancelado"}
POSTED_STATES = {"aprobado", "en_proceso_pago", "pagado", "cerrado"}


def money(value: object) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SolicitudValidationError("invalid_amount", "Importe inválido.") from exc
    if not result.is_finite() or result < 0:
        raise SolicitudValidationError(
            "invalid_amount", "El importe debe ser finito y no negativo."
        )
    return result


def invoice_allocation(total: object, available: object) -> tuple[Decimal, Decimal]:
    invoice, balance = money(total), money(available)
    if invoice <= 0:
        raise SolicitudValidationError(
            "invalid_invoice_total", "La factura debe tener un total positivo."
        )
    applied = min(invoice, balance)
    return applied, invoice - applied


def require_supplier_advance_mxn(document) -> None:
    if str(getattr(document, "currency", "MXN") or "").strip().upper() != "MXN":
        raise SolicitudValidationError(
            "advance_currency_unsupported",
            "Los anticipos a proveedores requieren MXN; no existe conversión contable verificada.",
        )


def validate_initial_advance(payload, due_date: str | date | None) -> None:
    require_supplier_advance_mxn(payload)
    payload.supplier_advance_due_date = (
        due_date if isinstance(due_date, date) else parse_optional_date(due_date)
    )
    if payload.supplier_advance_due_date is None:
        raise SolicitudValidationError(
            "advance_due_date_required", "Indique la fecha esperada de comprobación."
        )
    money(payload.monto_solicitado)
    if (
        payload.cfdi_uuid_manual
        or payload.cfdi_compartido_confirmado
        or any(a.categoria == "cfdi_xml" for a in payload.attachments)
    ):
        raise SolicitudValidationError(
            "advance_invoice_not_allowed",
            "La factura se registra después del pago mediante Comprobar anticipo. Use Materialidades para el soporte inicial.",
        )


async def _lock_advance(session, advance_id: UUID) -> Documento:
    advance = (
        await session.execute(
            select(Documento)
            .options(undefer(Documento.fase))
            .where(Documento.id == advance_id)
            .with_for_update(of=Documento)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if advance is None or not advance.is_supplier_advance:
        raise SolicitudValidationError(
            "invalid_advance", "Anticipo a proveedor no encontrado."
        )
    require_supplier_advance_mxn(advance)
    return advance


async def advance_balances(session, advance: Documento) -> dict[str, Decimal]:
    children = list(
        (
            await session.execute(
                select(Documento).where(Documento.supplier_advance_id == advance.id)
            )
        )
        .scalars()
        .all()
    )
    paid = (
        money(advance.monto_solicitado or 0)
        if advance.estado == "pagado" and advance.pagado_en
        else Decimal("0.00")
    )
    reserved = sum(
        (
            money(c.supplier_advance_applied or 0)
            for c in children
            if c.estado not in RELEASED_STATES
        ),
        Decimal("0.00"),
    )
    applied = sum(
        (
            money(c.supplier_advance_applied or 0)
            for c in children
            if c.estado in POSTED_STATES
        ),
        Decimal("0.00"),
    )
    return {
        "paid": paid,
        "reserved": reserved,
        "applied": applied,
        "available": max(paid - reserved, Decimal("0.00")),
        "pending": max(paid - applied, Decimal("0.00")),
    }


async def submit_supplier_invoice(
    session,
    *,
    advance_id: UUID,
    actor,
    submission_id: UUID,
    xml: SolicitudTercerosAttachment,
    pdf: SolicitudTercerosAttachment | None = None,
) -> Documento:
    """Validate and reserve one full invoice under the paid advance atomically."""
    from .documento_workflow_service import reserve_documento_cfdis_or_raise
    from .project_authorization_service import prepare_document_authorization_route

    advance = await _lock_advance(session, advance_id)
    if advance.empleado_id != actor.id:
        raise SolicitudValidationError(
            "owner_mismatch", "Solo el solicitante puede comprobar este anticipo."
        )
    if advance.estado != "pagado" or not advance.pagado_en:
        raise SolicitudValidationError(
            "advance_not_paid", "Primero debe confirmarse el pago del anticipo."
        )
    existing = (
        await session.execute(
            select(Documento).where(
                Documento.empleado_id == actor.id,
                Documento.client_submission_id == submission_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.supplier_advance_id != advance.id:
            raise SolicitudValidationError(
                "submission_conflict",
                "La clave de envío ya pertenece a otro movimiento.",
            )
        existing._supplier_invoice_created = False
        return existing
    validate_solicitud_terceros_attachment(xml)
    if xml.categoria != "cfdi_xml":
        raise SolicitudValidationError(
            "xml_required", "Se requiere el XML de la factura."
        )
    resolved, error = resolve_cfdi_upload(xml_bytes=xml.raw_bytes)
    if error or resolved is None:
        raise SolicitudValidationError("invalid_cfdi", error or "XML inválido.")
    data = resolved.parsed
    provider = await session.get(ProveedorCliente, advance.proveedor_cliente_id)
    if (
        provider is None
        or not provider.activo
        or not provider.rfc
        or str(data.get("emisor_rfc", "")).upper() != provider.rfc.strip().upper()
    ):
        raise SolicitudValidationError(
            "issuer_mismatch",
            "El RFC emisor debe coincidir con el proveedor activo del anticipo.",
        )
    receiver = str(data.get("receptor_rfc", "")).strip().upper()
    receivers = set(
        (
            await session.execute(
                select(RFCConfig.tax_id).where(RFCConfig.active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    if not receiver or receiver not in {str(r).strip().upper() for r in receivers}:
        raise SolicitudValidationError(
            "receiver_mismatch",
            "El RFC receptor no corresponde a una razón social activa configurada.",
        )
    if data.get("tipo_de_comprobante") != "I" or not data.get("cfdi_uuid"):
        raise SolicitudValidationError(
            "invalid_invoice",
            "Se requiere una factura de ingreso timbrada; no un complemento o nota de crédito.",
        )
    if str(data.get("moneda", "")).upper() != (advance.currency or "MXN").upper():
        raise SolicitudValidationError(
            "currency_mismatch", "La factura y el anticipo deben tener la misma moneda."
        )
    balance = await advance_balances(session, advance)
    if balance["available"] <= 0:
        raise SolicitudValidationError(
            "advance_fully_reserved",
            "El anticipo ya está aplicado o reservado en otras comprobaciones.",
        )
    applied, remainder = invoice_allocation(data.get("total"), balance["available"])
    child = Documento(
        id=uuid4(),
        empleado_id=advance.empleado_id,
        tipo="SOLICITUD",
        numero_referencia=await generate_documento_reference_number(
            session, "SOLICITUD", actor.id
        ),
        estado="control_presupuestal",
        proveedor_cliente_id=advance.proveedor_cliente_id,
        torneo_id=advance.torneo_id,
        fase=advance.fase,
        proyecto_otro=advance.proyecto_otro,
        categorias=advance.categorias,
        edicion=advance.edicion,
        currency=advance.currency,
        referencia_operaciones=advance.referencia_operaciones,
        referencia_base=advance.numero_referencia,
        concepto_pago=f"Comprobación de anticipo {advance.numero_referencia}",
        monto_solicitado=remainder,
        monto_total=remainder,
        client_submission_id=submission_id,
        numero_factura=str(data.get("folio") or ""),
    )
    session.add(child)
    await session.flush()
    await ingest_cfdi_from_upload(
        session,
        xml_bytes=xml.raw_bytes,
        source="user_upload",
        entity=child,
        numero_referencia=child.numero_referencia,
        require_shared_confirmation=True,
    )
    attachments = [xml] + ([pdf] if pdf else [])
    await _persist_solicitud_terceros_adjuntos(
        session, documento=child, attachments=attachments
    )
    child.supplier_advance_id = advance.id
    child.supplier_advance_applied = applied
    child.supplier_invoice_total = money(data["total"])
    await reserve_documento_cfdis_or_raise(session, child, actor)
    await prepare_document_authorization_route(session, child)
    session.add(
        Aprobacion(
            tipo_entidad="documento",
            entidad_id=child.id,
            aprobador_id=actor.id,
            accion="comprobar_anticipo_proveedor",
            comentario=f"Origen {advance.numero_referencia}; factura {child.supplier_invoice_total}; anticipo reservado {applied}; cantidad a pagar {remainder}.",
            fecha=datetime.utcnow(),
        )
    )
    child._supplier_invoice_created = True
    return child


async def _account(session, code: str):
    return (
        await session.execute(
            select(CuentaContable).where(
                CuentaContable.codigo == code, CuentaContable.activo.is_(True)
            )
        )
    ).scalar_one_or_none()


async def _event(session, *, document, event: str, lines: list[dict], fecha: datetime):
    from .employee_debtor_accounting_service import _create_poliza, _event_poliza_number

    meta = {
        "documento_id": str(document.id),
        "advance_id": str(document.supplier_advance_id or document.id),
        "event": event,
    }
    for line in lines:
        line["concepto"] = document.numero_referencia
        line["raw_row_json"] = meta
    provider = await session.get(ProveedorCliente, document.proveedor_cliente_id)
    return await _create_poliza(
        session,
        origen=event,
        numero_poliza=_event_poliza_number(
            {
                "proveedor_anticipo_pago": "SUP-ADV",
                "proveedor_anticipo_factura": "SUP-INV",
                "proveedor_anticipo_aplica": "SUP-APL",
                "proveedor_anticipo_remanente": "SUP-PAY",
            }[event],
            document.id,
        ),
        fecha=fecha,
        beneficiario_nombre=provider.nombre if provider else "Proveedor",
        concepto=f"{event} - {document.numero_referencia}",
        lines=lines,
    )


def _line(account, *, debit=0, credit=0):
    return {
        "cuenta_codigo": account.codigo,
        "cuenta_contable_id": account.id,
        "debe": debit,
        "haber": credit,
    }


async def _existing(session, document, event):
    from .employee_debtor_accounting_service import (
        _event_poliza_number,
        _existing_event_poliza,
    )

    return await _existing_event_poliza(
        session,
        origen=event,
        numero_poliza=_event_poliza_number(
            {
                "proveedor_anticipo_pago": "SUP-ADV",
                "proveedor_anticipo_factura": "SUP-INV",
                "proveedor_anticipo_aplica": "SUP-APL",
                "proveedor_anticipo_remanente": "SUP-PAY",
            }[event],
            document.id,
        ),
    )


async def ensure_supplier_invoice_posting(session, *, documento):
    """Recognize the full invoice and apply its advance at final approval.

    Tax mapping uses the existing canonical invoice preview, failing closed for
    missing accounts. No new fiscal-advance CFDI interpretation is introduced.
    """
    from ..models import ExpenseReport
    from .employee_debtor_accounting_service import (
        DebtorPostingResult,
        _create_poliza,
        _event_poliza_number,
        _load_budget_accounts,
        _preview_expense_lines,
    )
    from .expense_accounting_service import build_expense_accounting_preview

    require_supplier_advance_mxn(documento)
    advance = await _lock_advance(session, documento.supplier_advance_id)
    existing = await _existing(session, documento, "proveedor_anticipo_factura")
    if existing:
        return DebtorPostingResult(status="exists", poliza=existing)
    if (
        advance.estado != "pagado"
        or not advance.pagado_en
        or advance.proveedor_cliente_id != documento.proveedor_cliente_id
    ):
        return DebtorPostingResult(status="pending", reason="invalid_paid_advance")
    balances = await advance_balances(session, advance)
    if balances["reserved"] > balances["paid"]:
        return DebtorPostingResult(status="pending", reason="advance_overallocated")
    cfdi = (
        await session.get(CFDIReport, documento.cfdi_report_id)
        if documento.cfdi_report_id
        else None
    )
    if (
        cfdi is None
        or money(cfdi.total) != money(documento.supplier_invoice_total)
        or money(documento.monto_total) + money(documento.supplier_advance_applied)
        != money(cfdi.total)
    ):
        return DebtorPostingResult(status="pending", reason="invoice_amount_changed")
    concept, expense_account, _ = await _load_budget_accounts(session, documento)
    liability, advance_account = await _account(
        session, LIABILITY_ACCOUNT
    ), await _account(session, ADVANCE_ACCOUNT)
    if (
        concept is None
        or expense_account is None
        or liability is None
        or advance_account is None
    ):
        return DebtorPostingResult(
            status="pending", reason="missing_supplier_advance_accounts"
        )
    synthetic = ExpenseReport(
        proyecto="Solicitud",
        concepto=documento.concepto_pago,
        gasto_cantidad=float(documento.supplier_invoice_total),
        fecha=cfdi.fecha or datetime.utcnow(),
        tipo_gasto="manual",
        metodo_pago="TRANSFERENCIA",
        iva=None,
        budget_concept_id=concept.id,
        cuenta_contable_id=expense_account.id,
        cfdi_report_id=cfdi.id,
    )
    synthetic.cuenta_contable, synthetic.cfdi_report = expense_account, cfdi
    preview = await build_expense_accounting_preview(
        session,
        synthetic,
        contra_cuenta_contable_id=str(liability.id),
        contra_cuenta_codigo=liability.codigo,
    )
    lines, error = _preview_expense_lines(
        preview=preview,
        expense=synthetic,
        counterpart=liability,
        meta={"documento_id": str(documento.id), "advance_id": str(advance.id)},
    )
    if error:
        return DebtorPostingResult(status="pending", reason=error)
    payable = sum(
        (
            money(line.get("haber", 0))
            for line in lines
            if line["cuenta_codigo"] == liability.codigo
        ),
        Decimal("0.00"),
    )
    if payable != money(documento.supplier_invoice_total):
        return DebtorPostingResult(status="pending", reason="invoice_payable_mismatch")
    now = datetime.utcnow()
    provider = await session.get(ProveedorCliente, documento.proveedor_cliente_id)
    poliza = await _create_poliza(
        session,
        origen="proveedor_anticipo_factura",
        numero_poliza=_event_poliza_number("SUP-INV", documento.id),
        fecha=now,
        beneficiario_nombre=provider.nombre if provider else "Proveedor",
        concepto=documento.concepto_pago,
        lines=lines,
    )
    poliza.cfdi_report_id, poliza.cfdi_uuid = cfdi.id, cfdi.cfdi_uuid
    from .expense_service import create_expense_from_data

    expense = await create_expense_from_data(
        session=session,
        concepto=documento.concepto_pago,
        gasto_cantidad=float(documento.supplier_invoice_total),
        fecha=cfdi.fecha or now,
        empleado_id=documento.empleado_id,
        proyecto=str(getattr(concept, "tournament_name", None) or "Solicitud"),
        tipo_gasto="manual",
        metodo_pago="TRANSFERENCIA",
        origen="supplier_advance_invoice",
        tournament_id=str(documento.torneo_id) if documento.torneo_id else None,
        fase_torneo=documento.fase,
        categorias=documento.categorias,
        edicion=documento.edicion,
        currency=documento.currency,
        budget_concept_id=documento.budget_concept_id,
    )
    expense.documento_id = documento.id
    expense.cfdi_report_id, expense.cfdi_uuid_manual = cfdi.id, cfdi.cfdi_uuid
    expense.cuenta_contable_id = expense_account.id
    await _event(
        session,
        document=documento,
        event="proveedor_anticipo_aplica",
        fecha=now,
        lines=[
            _line(liability, debit=money(documento.supplier_advance_applied)),
            _line(advance_account, credit=money(documento.supplier_advance_applied)),
        ],
    )
    if money(documento.monto_total) == 0:
        documento.estado = "cerrado"
        documento.gasto_generado_id = expense.id
        documento.fecha_pago = None
    return DebtorPostingResult(
        status="created", poliza=poliza, liability_account=liability
    )


async def ensure_supplier_payment_posting(session, *, documento, fecha_pago):
    """Post only confirmed bank cash, never an operational cutoff."""
    from .employee_debtor_accounting_service import (
        DebtorPostingResult,
        resolve_default_bank_account,
    )

    require_supplier_advance_mxn(documento)
    parent_id = documento.supplier_advance_id or documento.id
    await _lock_advance(session, parent_id)
    event = (
        "proveedor_anticipo_pago"
        if documento.is_supplier_advance
        else "proveedor_anticipo_remanente"
    )
    existing = await _existing(session, documento, event)
    if existing:
        return DebtorPostingResult(status="exists", poliza=existing)
    bank = await resolve_default_bank_account(session)
    counterpart = await _account(
        session, ADVANCE_ACCOUNT if documento.is_supplier_advance else LIABILITY_ACCOUNT
    )
    if bank is None or counterpart is None:
        return DebtorPostingResult(
            status="pending", reason="missing_supplier_advance_accounts"
        )
    if not documento.is_supplier_advance and not await _existing(
        session, documento, "proveedor_anticipo_factura"
    ):
        return DebtorPostingResult(status="pending", reason="missing_invoice_accrual")
    amount = money(documento.monto_total)
    if amount <= 0:
        return DebtorPostingResult(status="pending", reason="no_bank_payment_due")
    fecha = (
        fecha_pago
        if isinstance(fecha_pago, datetime)
        else datetime.combine(fecha_pago, datetime.min.time())
    )
    poliza = await _event(
        session,
        document=documento,
        event=event,
        fecha=fecha,
        lines=[_line(counterpart, debit=amount), _line(bank, credit=amount)],
    )
    return DebtorPostingResult(status="created", poliza=poliza, bank_account=bank)
