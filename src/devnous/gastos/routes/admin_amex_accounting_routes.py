"""Finance review and confirmation of one AMEX journal per report cut."""

import json
from datetime import date
from html import escape
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AmexAccountingCut,
    AmexAccountingReview,
    CuentaContable,
    Documento,
    Empleado,
    ExpenseReport,
)
from ..services.amex_accounting_cut_service import (
    create_amex_accounting_cut,
    review_amex_partida,
)
from ..services.amex_cut_export_service import cut_expense_cfdis
from ..services.amex_expense_service import company_amex_sql_condition
from ..services.coi_poliza_exporter import (
    generate_coi_poliza_csv,
    generate_coi_poliza_xlsx,
    generate_coi_poliza_zip,
)
from .dependencies import get_db_session, require_admin_finanzas

router = APIRouter()
BASE = "/admin/contabilidad/amex"


def _page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title><style>
    body{{font-family:system-ui,sans-serif;margin:24px;color:#172b4d;background:#f7f9fc}}
    main{{max-width:1300px;margin:auto}}table{{width:100%;border-collapse:collapse;background:white}}
    td,th{{padding:12px;text-align:left;border-bottom:1px solid #dfe3e8}}
    input,select,button{{padding:8px;margin:4px 0;max-width:100%}}
    label{{display:block}}.notice{{padding:12px;background:#fff2cc;margin:12px 0}}
    .table-shell{{overflow-x:auto}}nav{{margin-bottom:20px}}section{{margin:24px 0}}
    </style></head><body><main><nav><a href="/admin/contabilidad/coi">Contabilidad COI</a>
    · <a href="{BASE}">Revisión AMEX</a>
    · <a href="/admin/gastos/amex/conciliacion">Conciliación AMEX</a></nav>
    <h1>{escape(title)}</h1>{content}</main></body></html>"""


def _redirect(informe_id: UUID, message: str, *, error: bool = False):
    parameter = "error_msg" if error else "msg"
    return RedirectResponse(
        f"{BASE}/informes/{informe_id}?{parameter}={quote(message)}", status_code=303
    )


@router.get(BASE, response_class=Response)
async def amex_accounting_reports(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    linked = exists().where(
        ExpenseReport.cuenta_gastos_id == Documento.cuenta_gastos_id,
        ExpenseReport.estado_gasto != "cancelado",
        company_amex_sql_condition(),
    )
    result = await session.execute(
        select(Documento)
        .where(Documento.tipo == "INFORME", Documento.estado == "aprobado", linked)
        .order_by(Documento.aprobado_en.desc())
        .limit(100)
    )
    rows = "".join(
        f'<tr><td>{escape(str(document.numero_referencia or "Informe"))}</td>'
        f'<td><a href="{BASE}/informes/{document.id}">Revisar partidas y corte</a></td></tr>'
        for document in result.scalars().all()
    )
    return Response(
        _page(
            "Revisión contable AMEX",
            "<p>Revisa cada partida del informe y confirma su tratamiento antes de crear el corte.</p>"
            f"<table><thead><tr><th>Informe aprobado</th><th>Acción</th></tr></thead>"
            f'<tbody>{rows or "<tr><td colspan=2>Sin informes AMEX aprobados.</td></tr>"}</tbody></table>',
        ),
        media_type="text/html",
    )


@router.get(BASE + "/informes/{informe_id}", response_class=Response)
async def amex_accounting_report_review(
    informe_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    informe = await session.get(Documento, informe_id)
    if informe is None or informe.tipo != "INFORME":
        raise HTTPException(404, "Informe no encontrado.")
    result = await session.execute(
        select(ExpenseReport)
        .where(
            or_(
                (
                    ExpenseReport.cuenta_gastos_id == informe.cuenta_gastos_id
                    if informe.cuenta_gastos_id
                    else False
                ),
                ExpenseReport.documento_id == informe.id,
                ExpenseReport.informe_documento_id == informe.id,
            ),
            ExpenseReport.estado_gasto != "cancelado",
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.id.asc())
    )
    expenses = list(result.scalars().all())
    result = await session.execute(
        select(AmexAccountingReview).where(
            AmexAccountingReview.informe_id == informe_id
        )
    )
    reviews = {review.expense_id: review for review in result.scalars().all()}
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True), CuentaContable.codigo.like("1170-002-%")
        )
        .order_by(CuentaContable.codigo)
    )
    accounts = list(result.scalars().all())
    result = await session.execute(
        select(AmexAccountingCut)
        .where(AmexAccountingCut.informe_id == informe_id)
        .order_by(AmexAccountingCut.created_at.desc(), AmexAccountingCut.id.desc())
    )
    cuts = list(result.scalars().all())
    versions = {
        str(expense.id): reviews[expense.id].version
        for expense in expenses
        if expense.id in reviews
    }
    rows = []
    for expense in expenses:
        review = reviews.get(expense.id)
        treatment = review.treatment if review else "expense"
        options = '<option value="">Selecciona la cuenta del socio</option>' + "".join(
            f'<option value="{account.id}" '
            f'{"selected" if review and review.debtor_account_id == account.id else ""}>'
            f'{escape(account.codigo)} · {escape(account.nombre or "")}</option>'
            for account in accounts
        )
        rows.append(f"""<tr>
            <td>{escape(str(expense.numero_referencia or 'Partida'))}<br>{escape(str(expense.concepto or ''))}</td>
            <td>{expense.fecha.date().isoformat() if expense.fecha else 'Sin fecha'}</td>
            <td>${float(expense.gasto_cantidad or 0):,.2f}</td>
            <td><form method="POST" action="{BASE}/informes/{informe_id}/partidas/{expense.id}">
                <label><input type="checkbox" name="reviewed" value="true" {'checked' if review else ''} required>
                    Partida revisada</label>
                <label>Tratamiento<select name="treatment">
                    <option value="expense" {'selected' if treatment == 'expense' else ''}>Gasto de empresa</option>
                    <option value="partner_receivable" {'selected' if treatment == 'partner_receivable' else ''}>Cargo al socio por el total</option>
                </select></label>
                <label>Cuenta de deudores socios<select name="debtor_account_id">{options}</select></label>
                <label>Motivo<input name="reason" maxlength="1000" value="{escape(review.reason if review else '', quote=True)}"></label>
                <button type="submit">Guardar revisión</button>
            </form></td>
        </tr>""")
    downloads = "".join(
        f'<li>{"Corte inicial" if cut.kind == "initial" else "Corte de ajuste"} · '
        f"{cut.accounting_date.isoformat()} · "
        + " · ".join(
            f'<a href="{BASE}/cortes/{cut.id}.{fmt}">{fmt.upper()}</a>'
            for fmt in ("xlsx", "csv", "zip")
        )
        + "</li>"
        for cut in cuts
    )
    dates = [expense.fecha.date() for expense in expenses if expense.fecha]
    cut_date = date.today() if cuts else min(dates, default=date.today())
    notice = (
        request.query_params.get("error_msg") or request.query_params.get("msg") or ""
    )
    content = f"""<div class="notice">{escape(notice) if notice else 'La opción Cargo al socio usa el total de la partida, incluso si tiene factura.'}</div>
        <p>El corte incluye todas las partidas activas del informe. Completa sus revisiones y confirma una sola póliza.</p>
        <div class="table-shell"><table><thead><tr><th>Partida</th><th>Fecha</th><th>Total</th><th>Revisión de Finanzas</th></tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan="4">Sin partidas activas.</td></tr>'}</tbody></table></div>
        <section><h2>{'Reclasificación del informe' if cuts else 'Corte contable del informe'}</h2>
        <form method="POST" action="{BASE}/informes/{informe_id}/cortes">
            <input type="hidden" name="expected_versions" value="{escape(json.dumps(versions), quote=True)}">
            <input type="hidden" name="adjustment" value="{'true' if cuts else 'false'}">
            <label>Fecha contable<input type="date" name="accounting_date" value="{cut_date.isoformat()}" required></label>
            <label>Motivo del corte<input name="reason" maxlength="1000" {'required' if cuts else ''}></label>
            <label><input type="checkbox" name="confirmed" value="true" required>Confirmo la revisión del informe completo</label>
            <button type="submit">{'Generar una póliza de ajuste' if cuts else 'Generar una póliza del corte'}</button>
        </form></section><section><h2>Cortes guardados</h2><ul>{downloads or '<li>Aún no se ha generado un corte.</li>'}</ul></section>"""
    return Response(
        _page(f"Informe {informe.numero_referencia or ''} · AMEX", content),
        media_type="text/html",
    )


@router.post(BASE + "/informes/{informe_id}/partidas/{expense_id}")
async def amex_accounting_review_save(
    informe_id: UUID,
    expense_id: UUID,
    reviewed: bool = Form(False),
    treatment: str = Form(...),
    debtor_account_id: str = Form(""),
    reason: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    try:
        debtor_id = UUID(debtor_account_id) if debtor_account_id.strip() else None
    except ValueError:
        return _redirect(informe_id, "Cuenta de socio inválida.", error=True)
    if not reviewed:
        return _redirect(
            informe_id, "Confirma el check de revisión de la partida.", error=True
        )
    result = await review_amex_partida(
        session,
        informe_id=informe_id,
        expense_id=expense_id,
        treatment=treatment,
        debtor_account_id=debtor_id if treatment != "expense" else None,
        actor=current_empleado,
        reason=reason,
    )
    if result.status == "pending":
        await session.rollback()
        return _redirect(
            informe_id, f"No se guardó la revisión ({result.reason}).", error=True
        )
    await session.commit()
    return _redirect(informe_id, "Revisión de la partida guardada.")


@router.post(BASE + "/informes/{informe_id}/cortes")
async def amex_accounting_cut_confirm(
    informe_id: UUID,
    expected_versions: str = Form(...),
    accounting_date: date = Form(...),
    confirmed: bool = Form(False),
    adjustment: bool = Form(False),
    reason: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    if not confirmed:
        return _redirect(
            informe_id, "Confirma la revisión del informe completo.", error=True
        )
    try:
        versions = json.loads(expected_versions)
        if not isinstance(versions, dict) or not all(
            isinstance(key, str) and type(value) is int and value > 0
            for key, value in versions.items()
        ):
            raise ValueError
    except (ValueError, TypeError):
        return _redirect(
            informe_id, "La selección de revisiones es inválida.", error=True
        )
    result = await create_amex_accounting_cut(
        session,
        informe_id=informe_id,
        actor=current_empleado,
        expected_versions=versions,
        accounting_date=accounting_date,
        adjustment=adjustment,
        reason=reason,
    )
    if result.status == "pending":
        await session.rollback()
        return _redirect(
            informe_id, f"No se generó el corte ({result.reason}).", error=True
        )
    await session.commit()
    return _redirect(
        informe_id, "Corte guardado con una sola póliza. Puedes descargarlo."
    )


@router.get(BASE + "/cortes/{cut_id}.{export_format}", response_class=Response)
async def amex_accounting_cut_export(
    cut_id: UUID,
    export_format: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    if export_format not in {"xlsx", "csv", "zip"}:
        raise HTTPException(404, "Formato no disponible.")
    cut = await session.get(AmexAccountingCut, cut_id)
    if cut is None:
        raise HTTPException(404, "Corte no encontrado.")
    try:
        items = cut_expense_cfdis(cut)
        generators = {
            "xlsx": generate_coi_poliza_xlsx,
            "csv": generate_coi_poliza_csv,
            "zip": generate_coi_poliza_zip,
        }
        content = generators[export_format](items)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    media = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "zip": "application/zip",
    }
    return Response(
        content,
        media_type=media[export_format],
        headers={
            "Content-Disposition": f'attachment; filename="Corte_AMEX_{cut.id}.{export_format}"'
        },
    )
