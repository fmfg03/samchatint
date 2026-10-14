"""Finance's explicit, resumable three-step AMEX statement workspace."""

import json
from datetime import date
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AmexAccountingCut, Empleado
from ..services.amex_card_account_service import list_amex_card_accounts
from ..services.amex_statement_import_service import import_amex_statement
from ..services.amex_statement_workflow_service import (
    WorkflowConflict,
    confirm_statement_informe,
    export_statement,
    incorporate_statement_cut,
    list_statement_workflows,
    statement_workflow_view,
)
from .admin_amex_accounting_routes import (
    _exception_text,
    _page,
    _require_finance_actor,
    amex_accounting_report_review,
    extract_amex_page_content,
)
from .dependencies import get_db_session, require_admin_finanzas

router = APIRouter()
BASE = "/admin/contabilidad/amex/estados"


def _html(title: str, content: str, actor: Empleado) -> Response:
    styles = """<style>.statement-steps{display:flex;gap:12px;flex-wrap:wrap}
    .statement-steps
    a{display:inline-block;padding:10px 14px;background:var(--shell-card);
    border:1px solid var(--shell-line);border-radius:14px;
    color:var(--shell-ink);text-decoration:none}
    .statement-steps [aria-current]{
    font-weight:bold;background:#ecfdf5}.statement-meta{overflow-wrap:anywhere}
    .statement-form{max-width:700px}.statement-form input,.statement-form select{
    box-sizing:border-box;width:100%}.statement-form input[type=checkbox]{width:auto}
    .statement-form label{margin:12px 0}.statement-table{overflow-x:auto}
    .statement-table td{overflow-wrap:anywhere;max-width:28rem;white-space:normal}
    .statement-table td:nth-child(3){white-space:normal}
    @media(max-width:600px){.statement-table table{min-width:650px}}</style>"""
    return Response(_page(title, styles + content, actor), media_type="text/html")


def _steps(import_id: UUID | None, current: int) -> str:
    paths = [BASE + "/nuevo", BASE, BASE]
    if import_id:
        paths[1:] = [f"{BASE}/{import_id}/pendientes", f"{BASE}/{import_id}/polizas"]
    labels = ["1. Cargar estado", "2. Resolver pendientes", "3. Generar pólizas"]
    return (
        '<nav class="statement-steps" aria-label="Recorrido AMEX">'
        + "".join(
            f'<a href="{path}"'
            f' {"aria-current=step" if i == current else ""}>{label}</a>'
            for i, (path, label) in enumerate(zip(paths, labels), 1)
        )
        + "</nav>"
    )


def _notice(message: str) -> str:
    return f'<p class="notice" role="status">{escape(message)}</p>' if message else ""


def _redirect(
    import_id: UUID,
    message: str,
    *,
    step: str = "polizas",
    informe_id: UUID | None = None,
):
    query = {"msg": message}
    if informe_id is not None:
        query["informe_id"] = str(informe_id)
    return RedirectResponse(
        f"{BASE}/{import_id}/{step}?{urlencode(query)}", status_code=303
    )


def _meta(statement) -> str:
    return (
        '<p class="statement-meta">'
        f"{escape(str(statement.filename))} · "
        f"{escape(str(statement.period_start))} a {escape(str(statement.period_end))}"
        "</p>"
    )


def _row_reason(code: str | None) -> str:
    return {
        "Falta informe aprobado": "Falta informe aprobado.",
        "Falta tarjeta explícita": "Falta identificar la tarjeta.",
        "Falta evidencia fiscal o tratamiento en el informe": (
            "Revisa la factura y el tratamiento del informe."
        ),
        "Cargo importado cancelado: requiere revisión": (
            "Cargo cancelado: requiere revisión."
        ),
        "missing_date": "Falta la fecha del cargo.",
        "invalid_date": "La fecha no tiene un formato válido.",
        "missing_description": "Falta la descripción del cargo.",
        "missing_amount": "Falta el importe del cargo.",
        "invalid_amount": "El importe no tiene un formato válido.",
        "missing_amex_card": "Falta identificar la tarjeta.",
        "missing_cfdi": "Falta la factura del cargo.",
        "missing_approved_informe": "Falta informe aprobado.",
        "ambiguous_approved_informe": (
            "Hay varios informes posibles; declara la fuente."
        ),
    }.get(code or "", "Revisa los datos y la evidencia del cargo.")


def _attention_label(decision) -> str:
    if decision is None:
        return ""
    return {
        "amex_attention_missing_beneficiary": "Falta beneficiario explícito",
        "amex_attention_beneficiary_conflict": "Beneficiarios contradictorios",
        "amex_attention_ambiguous_beneficiary": "Beneficiario ambiguo",
        "amex_attention_multiple_or_missing_informes": (
            "Informe asociado ausente o ambiguo"
        ),
        "amex_attention_configuration_disabled": "Revisar configuración de atención",
    }.get(decision.reason, "")


def _totals(view: dict) -> str:
    totals = view.get("imported_totals", {})
    text = (
        "Total de cargos: "
        + " · ".join(
            f"${amount:,.2f} {escape(str(currency))}"
            for currency, amount in totals.items()
        )
        if totals
        else "Total de cargos: pendiente de lectura"
    )
    if view.get("totals_unknown_count", 0):
        text += (
            f" · Total incompleto: {view['totals_unknown_count']} cargos sin importe"
            " verificable"
        )
    return text


def _progress(view: dict) -> str:
    progress = view.get("financial_progress", {})
    generated = progress.get("selected_generated_count")
    selected = progress.get("selected_source_count")
    text = (
        "Pólizas vinculadas:"
        f" {int(progress.get('linked_cut_count', view.get('linked_cut_count', 0)))}"
    )
    if selected is not None and generated is not None:
        text += f" · Fuentes seleccionadas: {generated}/{selected} con póliza"
    else:
        text += " · Falta declarar las fuentes aprobadas del estado"
    return text


def _summary(view: dict) -> str:
    return (
        f"<p>{_totals(view)}</p><p>Pendientes operativos:"
        f" {view.get('operational_pending_count', 'Por revisar')} · Banco por vincular:"
        f" {view.get('bank_pending_count', 'Por revisar')}</p><p>{_progress(view)}</p>"
    )


async def _view(session, import_id, **kwargs):
    try:
        return await statement_workflow_view(session, import_id, **kwargs)
    except WorkflowConflict:
        raise HTTPException(
            409, "Revisa el estado y la selección de informes disponibles."
        ) from None


@router.get(BASE, response_class=Response)
async def statement_dashboard(
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    listing = await list_statement_workflows(
        session, actor=current_empleado, page=page, page_size=20
    )
    rows = []
    for summary in listing["rows"]:
        statement = summary["import"]
        skipped = summary["skipped_count"]
        card_label = escape(
            str(getattr(summary.get("card"), "card_label", "Tarjeta declarada"))
        )
        start = escape(str(statement.period_start))
        end = escape(str(statement.period_end))
        pending = summary.get("operational_pending_count", "Por revisar")
        bank_pending = summary.get("bank_pending_count", "Por revisar")
        partial = " · Carga parcial" if skipped else ""
        rows.append(
            f"<tr><td>{card_label}</td><td>{start} a {end}</td>"
            f'<td>{summary["imported_count"]}<br>{_totals(summary)}</td>'
            f"<td>{pending} operativos<br>{skipped} filas omitidas</td>"
            f"<td>{bank_pending} por vincular</td>"
            f'<td>{_progress(summary)}{partial}</td><td><a class="button primary"'
            f' href="{BASE}/{statement.id}/pendientes">Continuar</a></td></tr>'
        )
    paging = (
        f'<nav aria-label="Páginas de estados"><span>Página {listing["page"]}</span>'
    )
    if listing["has_previous"]:
        paging += (
            f'<a class="button secondary" href="{BASE}?page={listing["page"] - 1}">'
            "Anterior</a>"
        )
    if listing["has_next"]:
        paging += (
            f'<a class="button secondary" href="{BASE}?page={listing["page"] + 1}">'
            "Siguiente</a>"
        )
    paging += "</nav>"
    return _html(
        "Estados de cuenta AMEX",
        f'<p><a class="button primary" href="{BASE}/nuevo">'
        "Cargar estado de cuenta</a></p><div"
        ' class="statement-table table-shell"><table><thead><tr><th>Tarjeta</th>'
        "<th>Período"
        " declarado</th><th>Cargos</th><th>Pendientes"
        " operativos</th><th>Banco</th><th>Avance"
        " contable</th><th>Acción</th></tr></thead><tbody>"
        + ("".join(rows) or '<tr><td colspan="7">Sin estados en esta página.</td></tr>')
        + "</tbody></table></div>"
        + paging
        + '<p><a href="/admin/gastos/amex/conciliacion">Cargos'
        " anteriores</a> · Las cargas anteriores conservan su flujo; no se"
        " reconstruye su archivo o período.</p>",
        current_empleado,
    )


async def _upload_form(
    session: AsyncSession, actor: Empleado, message: str = ""
) -> Response:
    cards = await list_amex_card_accounts(session)
    result = await session.execute(
        select(Empleado).where(Empleado.activo.is_(True)).order_by(Empleado.nombre)
    )
    people = result.scalars().all()
    card_options = "".join(
        f'<option value="{card.id}">{escape(str(card.card_label))} ·'
        f" ••••{escape(str(card.last4))}</option>"
        for card in cards
    )
    employee_options = "".join(
        f'<option value="{person.id}">{escape(str(person.nombre))}</option>'
        for person in people
    )
    return _html(
        "Cargar estado de cuenta AMEX",
        _steps(None, 1)
        + _notice(message)
        + f"""
        <form class="statement-form" method="POST" action="{BASE}/nuevo"
        enctype="multipart/form-data">
        <label>Tarjeta<select name="card_account_id" required><option value="">
        Selecciona tarjeta</option>{card_options}</select></label>
        <label>Titular declarado<select name="employee_id" required><option value="">
        Selecciona titular</option>{employee_options}</select></label>
        <label>Inicio del período del estado<input type="date" name="period_start"
        required></label>
        <label>Fin del período del estado<input type="date" name="period_end" required>
        </label>
        <label>Estado de cuenta CSV<input type="file" name="file" accept=".csv,text/csv"
        required></label>
        <label>UUID del CFDI mensual (opcional)<input name="cfdi_uuid_mensual"></label>
        <label><input type="checkbox" name="additional_confirmed" value="true">Confirmo
        que es una carga adicional para una tarjeta y período ya cargados</label>
        <button class="button primary" type="submit">Cargar y continuar</button></form>
        <p>La carga conserva las fechas originales. No aprueba informes ni genera una
        póliza.</p>""",
        actor,
    )


@router.get(BASE + "/nuevo", response_class=Response)
async def statement_new(
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    return await _upload_form(session, current_empleado)


@router.post(BASE + "/nuevo", response_class=Response)
async def statement_upload(
    card_account_id: UUID = Form(...),
    employee_id: UUID = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    file: UploadFile = File(...),
    cfdi_uuid_mensual: str = Form(""),
    additional_confirmed: bool = Form(False),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    try:
        result = await import_amex_statement(
            session,
            actor=current_empleado,
            card_account_id=card_account_id,
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
            filename=file.filename or "estado.csv",
            content=await file.read(),
            cfdi_uuid_mensual=cfdi_uuid_mensual or None,
            additional_confirmed=additional_confirmed,
        )
        if result["status"] not in {"imported", "replay"}:
            await session.rollback()
            messages = {
                "additional_confirmation_required": (
                    "Ya existe una carga para esta tarjeta y período. Revisa el archivo"
                    " y confirma explícitamente si es adicional; vuelve a"
                    " seleccionarlo."
                ),
                "context_conflict": (
                    "Este archivo ya fue cargado con otro contexto. Conserva la carga"
                    " original; revisa tarjeta, titular y período."
                ),
            }
            return await _upload_form(
                session,
                current_empleado,
                messages.get(
                    result["status"],
                    "No se cargó el estado. Revisa el archivo, tarjeta, titular y"
                    " período.",
                ),
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return _redirect(
        result["import"].id,
        "Carga conservada. Revisa los pendientes y continúa.",
        step="pendientes",
    )


@router.get(BASE + "/{import_id}/pendientes", response_class=Response)
async def statement_pending(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    view = await _view(session, import_id, actor=current_empleado)
    statement = view["import"]
    rows = []
    return_to = f"{BASE}/{import_id}/pendientes"
    for item in view["items"]:
        data = item.normalized_json or {}
        state = view.get("item_states", {}).get(item.id, {})
        expense = view.get("expenses", {}).get(item.imported_expense_id)
        expense_id = item.imported_expense_id
        return_query = urlencode({"return_to": return_to})
        row_date = escape(str(data.get("date", data.get("fecha", ""))))
        description = escape(
            str(
                getattr(expense, "concepto", None)
                or data.get("description", data.get("concepto", "Cargo del estado"))
            )
        )
        amount = escape(str(data.get("amount", data.get("importe", ""))))
        bank_state = (
            "Fila omitida: " + escape(_row_reason(item.reason_code))
            if item.status == "skipped"
            else escape(
                str(state.get("reason", "Estado bancario pendiente de lectura"))
            )
        )
        if state.get("status") == "cancelado":
            bank_state = "Cargo cancelado; conserva el importe original del estado."
            if state.get("bank_associated"):
                bank_state += " Asociación bancaria anterior conservada."
        action = (
            f'<a href="/gastos/{expense_id}?{return_query}">Revisar'
            " evidencia del cargo</a>"
            if expense_id
            else "Corrige la fila del archivo; no existe un cargo generado."
        )
        operational = " · ".join(
            _row_reason(code) for code in state.get("blockers", [])
        )
        rows.append(
            f"<tr><td>{item.row_number}</td><td>{row_date}</td>"
            f"<td>{description}</td><td>{amount}</td><td>{bank_state}</td>"
            f"<td>{escape(operational)}<br>{action}<br><a"
            f' href="/admin/gastos/amex/conciliacion?{return_query}">'
            "Revisar vínculo bancario</a></td></tr>"
        )
    warning = (
        _notice(
            "Carga parcial: hay filas omitidas. Las pólizas no resuelven esos errores"
            " del archivo."
        )
        if any(item.status == "skipped" for item in view["items"])
        else ""
    )
    return _html(
        "Resolver pendientes AMEX",
        _steps(import_id, 2)
        + _meta(statement)
        + _summary(view)
        + _notice(request.query_params.get("msg", ""))
        + warning
        + "<p>Todos los cargos bancarios permanecen visibles. Los informes atendidos"
        " desde Cuenta no se incluyen en la cola manual AMEX.</p>"
        + '<div class="statement-table table-shell"><table><thead><tr><th>Fila</th>'
        "<th>Fecha</th><th>Cargo</th><th>Importe</th><th>Estado"
        " bancario</th><th>Resolver</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        + "<section><h2>Falta informe aprobado</h2><p>Para un cargo sin fuente"
        " aprobada, completa su Cuenta e INFORME. No se genera una póliza"
        " independiente del cargo.</p>"
        + '<a href="/informes-de-gastos">Abrir Cuentas de gastos</a> · <a'
        ' href="/soporte">Solicitar soporte</a></section>'
        + f'<a class="button primary" href="{BASE}/{import_id}/polizas">Continuar a'
        " pólizas</a>",
        current_empleado,
    )


@router.get(BASE + "/{import_id}/polizas", response_class=Response)
async def statement_policies(
    import_id: UUID,
    request: Request,
    informe_id: UUID | None = None,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    selected = [informe_id] if informe_id else []
    view = await _view(
        session, import_id, actor=current_empleado, selected_informe_ids=selected
    )
    if informe_id is None and view.get("selected_informe_ids"):
        # These IDs come from validated, immutable cut-link declarations.
        informe_id = UUID(str(view["selected_informe_ids"][0]))
        view = await _view(
            session,
            import_id,
            actor=current_empleado,
            selected_informe_ids=[informe_id],
        )
    option_parts = []
    attention = view.get("attention_decisions", {})
    for report in view["reports"]:
        reason = _attention_label(attention.get(report.id))
        label = str(report.numero_referencia or "Informe aprobado")
        if reason:
            label += f" · {reason}"
        option_parts.append(
            f'<option value="{report.id}"'
            f' {"selected" if report.id == informe_id else ""}>{escape(label)}</option>'
        )
    options = "".join(option_parts)
    content = (
        _steps(import_id, 3)
        + _meta(view["import"])
        + _summary(view)
        + _notice(request.query_params.get("msg", ""))
    )
    content += f"""<p>Declara el informe que corresponde a esta carga.
    La selección es explícita y no acredita conciliación bancaria.</p>
    <form method="GET" action="{BASE}/{import_id}/polizas"><label>Informe
    aprobado<select name="informe_id" required><option value="">Selecciona el
    informe</option>{options}</select></label>
    <button class="button primary">Preparar informe completo</button>
    </form>"""
    for warning in view.get("warnings", []):
        content += _notice(str(warning))
    for blocker in view.get("blockers", []):
        content += _notice(str(blocker))
    if informe_id is not None and view["selected_reports"]:
        # Reuse the canonical read-only whole-report renderer and trusted batch script.
        # Only the form destination changes; the owner workflow handles the transaction.
        response = await amex_accounting_report_review(
            informe_id, request, session, current_empleado
        )
        page = response.body.decode()
        body = extract_amex_page_content(page)
        old_action = f'action="/admin/contabilidad/amex/informes/{informe_id}/cortes"'
        new_action = f'action="{BASE}/{import_id}/informes/{informe_id}/confirmar"'
        body = body.replace(old_action, new_action, 1)
        selected_preview = view["selected_reports"][0].get("preview")
        report_return = f"{BASE}/{import_id}/polizas?informe_id={informe_id}"
        report_query = urlencode({"return_to": report_return})
        for expense in getattr(selected_preview, "expenses", []):
            body = body.replace(
                f'href="/gastos/{expense.id}"',
                f'href="/gastos/{expense.id}?{report_query}"',
            )
        # Existing cuts are incorporated explicitly below; legacy download links
        # would leave the statement's declared and validated cut set.
        frozen_section = "<section><h2>Cortes guardados</h2>"
        if frozen_section in body:
            before, after = body.split(frozen_section, 1)
            body = before + after.split("</section>", 1)[1]
        body = body.replace(
            '<input type="hidden" name="confirmation_mode"',
            '<input type="hidden" name="selected_informe_ids"'
            f' value="{escape(json.dumps([str(informe_id)]), quote=True)}"><input'
            ' type="hidden" name="confirmation_mode"',
            1,
        )
        content += (
            "<section><h2>Informe completo, todas sus tarjetas y fechas</h2>"
            + body
            + "</section>"
        )
        for cut in view["cuts"]:
            if cut.informe_id == informe_id:
                content += f"""<form method="POST"
                action="{BASE}/{import_id}/cortes/{cut.id}/incorporar">
                <input type="hidden" name="selected_informe_ids"
                value="{escape(json.dumps([str(informe_id)]), quote=True)}">
                <p>Corte congelado: {escape(str(cut.accounting_date))}. Incorporarlo
                conserva su póliza y evidencia; no cubre cambios posteriores.</p>
                <label><input type="checkbox" name="confirmed" value="true" required>
                Confirmo incorporar este corte existente</label>
                <button class="button secondary">Incorporar corte guardado</button>
                </form>"""
    content += (
        "<section><h2>Descargar pólizas incorporadas</h2><p>Una póliza completa por"
        " informe. Descargar no registra importación o aceptación en COI.</p>"
    )
    if view.get("linked_cut_ids"):
        content += " · ".join(
            f'<a href="{BASE}/{import_id}/exportar.{fmt}">{fmt.upper()}</a>'
            for fmt in ("xlsx", "csv", "zip")
        )
    else:
        content += "<p>Aún no hay pólizas incorporadas a este estado.</p>"
    content += "</section>"
    return _html("Generar pólizas AMEX", content, current_empleado)


def _selection(value: str) -> list[UUID]:
    data = json.loads(value)
    if not isinstance(data, list) or not data:
        raise ValueError("invalid selection")
    return [UUID(item) for item in data]


@router.post(BASE + "/{import_id}/informes/{informe_id}/confirmar")
async def statement_confirm(
    import_id: UUID,
    informe_id: UUID,
    selected_informe_ids: str = Form(...),
    expected_versions: str = Form(...),
    accounting_date: date = Form(...),
    confirmed: bool = Form(False),
    adjustment: bool = Form(False),
    reason: str = Form(""),
    confirmation_mode: str = Form(...),
    expected_source_key: str = Form(...),
    expected_approval_id: str = Form(""),
    expected_approval_version: str = Form(""),
    decisions: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    try:
        selected = _selection(selected_informe_ids)
        versions, groups = json.loads(expected_versions), json.loads(decisions)
        if (
            confirmation_mode != "approved_informe"
            or not isinstance(versions, dict)
            or not isinstance(groups, list)
        ):
            raise ValueError
        result = await confirm_statement_informe(
            session,
            import_id=import_id,
            informe_id=informe_id,
            actor=current_empleado,
            selected_informe_ids=selected,
            expected_versions=versions,
            expected_source_key=expected_source_key,
            expected_approval_id=(
                UUID(expected_approval_id) if expected_approval_id else None
            ),
            expected_approval_version=(
                int(expected_approval_version) if expected_approval_version else None
            ),
            decisions=groups,
            accounting_date=accounting_date,
            confirmed=confirmed,
            adjustment=adjustment,
            reason=reason,
        )
        if result.status == "pending":
            await session.rollback()
            return _redirect(
                import_id, _exception_text(result.reason), informe_id=informe_id
            )
        await session.commit()
    except (ValueError, TypeError):
        await session.rollback()
        return _redirect(
            import_id,
            "No se generó la póliza. Revisa la selección, fuente y decisiones"
            " actuales.",
            informe_id=informe_id,
        )
    except Exception:
        await session.rollback()
        raise
    return _redirect(
        import_id,
        "Póliza completa guardada. Puedes descargarla.",
        informe_id=informe_id,
    )


@router.post(BASE + "/{import_id}/cortes/{cut_id}/incorporar")
async def statement_incorporate(
    import_id: UUID,
    cut_id: UUID,
    selected_informe_ids: str = Form(...),
    confirmed: bool = Form(False),
    reason: str = Form(""),
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    try:
        await incorporate_statement_cut(
            session,
            import_id=import_id,
            cut_id=cut_id,
            actor=current_empleado,
            selected_informe_ids=_selection(selected_informe_ids),
            confirmed=confirmed,
            reason=reason,
        )
        cut = await session.get(AmexAccountingCut, cut_id)
        if cut is None:
            raise HTTPException(409, "No se pudo verificar el corte incorporado.")
        report_id = UUID(str(cut.informe_id))
        await session.commit()
    except (ValueError, TypeError):
        await session.rollback()
        return _redirect(
            import_id,
            "No se incorporó el corte. Revisa fuente, confirmación y corte guardado.",
        )
    except Exception:
        await session.rollback()
        raise
    return _redirect(
        import_id,
        "Corte congelado incorporado sin generar otra póliza.",
        informe_id=report_id,
    )


@router.get(BASE + "/{import_id}/exportar.{export_format}", response_class=Response)
async def statement_export(
    import_id: UUID,
    export_format: str,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(current_empleado)
    media_types = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "zip": "application/zip",
    }
    if export_format not in media_types:
        raise HTTPException(404, "Formato no disponible.")
    try:
        content = await export_statement(
            session,
            import_id=import_id,
            actor=current_empleado,
            export_format=export_format,
        )
    except WorkflowConflict:
        raise HTTPException(
            409,
            "No se puede descargar el conjunto completo. Revisa los cortes"
            " incorporados.",
        ) from None
    return Response(
        content,
        media_type=media_types[export_format],
        headers={
            "Content-Disposition": (
                f'attachment; filename="amex-polizas.{export_format}"'
            )
        },
    )
