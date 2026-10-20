"""Finance review and confirmation of one AMEX journal per report cut."""

import json
from datetime import date
from html import escape
from typing import cast
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, raiseload

from ..models import (
    AmexAccountingCut,
    CuentaContable,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
)
from ..services.amex_accounting_cut_service import (
    confirm_amex_approved_informe_cut,
    create_amex_accounting_cut,
    preview_amex_approved_informe,
    review_amex_partida,
)
from ..services.amex_attention_policy import load_amex_attention_context
from ..services.amex_cut_export_service import cut_expense_cfdis
from ..services.amex_expense_service import (
    FINANCE_AMEX_ROLES,
    company_amex_sql_condition,
)
from ..services.amex_recognition_service import load_amex_card_display_identities
from ..services.coi_poliza_exporter import (
    generate_coi_poliza_csv,
    generate_coi_poliza_xlsx,
    generate_coi_poliza_zip,
)
from ..services.documento_semantics import effective_document_beneficiary_name
from .dependencies import get_db_session, require_admin_finanzas

router = APIRouter()
BASE = "/admin/contabilidad/amex"


AMEX_CONTENT_START = "<!-- amex-page-content:start -->"
AMEX_CONTENT_END = "<!-- amex-page-content:end -->"


def extract_amex_page_content(page: str) -> str:
    """Extract only controlled content, excluding navigation and its scripts."""
    if page.count(AMEX_CONTENT_START) != 1 or page.count(AMEX_CONTENT_END) != 1:
        raise ValueError("La vista AMEX no contiene un fragmento único.")
    _, _, rest = page.partition(AMEX_CONTENT_START)
    content, separator, _ = rest.partition(AMEX_CONTENT_END)
    if not separator:
        raise ValueError("El fragmento AMEX está incompleto.")
    return content


def _page(
    title: str, content: str, actor: Empleado, *, workflow: str = "report"
) -> str:
    # user_routes mounts this router. Resolve presentation helpers only when
    # rendering a request, after module initialization has completed.
    from .user_routes import (
        _contabilidad_subnav,
        _gastos_breadcrumb_html,
        _render_workspace_hero,
        _workspace_shell_styles,
        render_top_navigation,
    )

    styles = _workspace_shell_styles("1380px", layout="data")
    navigation = render_top_navigation(actor, "contabilidad")
    direct = workflow == "direct"
    breadcrumbs = _gastos_breadcrumb_html(
        [
            ("Contabilidad", "/admin/contabilidad/coi"),
            (
                "AMEX directo" if direct else "AMEX",
                (
                    "/admin/contabilidad/amex/estados/directo"
                    if direct
                    else "/admin/contabilidad/amex/estados"
                ),
            ),
            (title, None),
        ]
    )
    if direct:
        description = (
            "Del estado original a la conciliación fiscal, presupuesto y póliza."
        )
        actions_html = '<a class="button secondary" href="/admin/contabilidad/amex/estados/directo">Estados directos</a>'
        if (
            bool(getattr(actor, "activo", False))
            and str(getattr(actor, "rol", "")).strip().lower() in FINANCE_AMEX_ROLES
        ):
            actions_html += (
                '<a class="button secondary" href="/admin/contabilidad/amex/estados/directo/nuevo">Cargar estado directo</a> '
                '<a class="button secondary" href="/admin/gastos/amex/conciliacion">Programar pago de AMEX</a>'
            )
        side_html = (
            '<div class="eyebrow">Cómo continuar</div><p class="lead">'
            "Carga el estado y revisa su evidencia fiscal. Contabilidad clasifica, "
            "Control Presupuestal asigna la partida y Finanzas confirma el corte "
            "completo. Programar la tarjeta no registra un pago.</p>"
        )
    else:
        description = "Del estado de cuenta al informe completo y su póliza."
        actions_html = (
            '<a class="button secondary" href="/admin/contabilidad/amex/estados">'
            "Estados de cuenta</a> "
            '<a class="button secondary" href="/admin/contabilidad/amex">'
            "Informes aprobados</a> "
            '<a class="button secondary" href="/admin/gastos/amex/conciliacion">'
            "Conciliación AMEX</a>"
        )
        side_html = (
            '<div class="eyebrow">Cómo continuar</div>'
            '<p class="lead">Carga el estado, resuelve los pendientes y confirma '
            "cada informe completo. El vínculo bancario conserva su estado propio.</p>"
        )
    hero = _render_workspace_hero(
        eyebrow="Contabilidad · AMEX",
        title=title,
        description=description,
        actions_html=actions_html,
        side_html=side_html,
    )
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)}</title><style>{styles}
    .amex-content{{min-width:0}}
    .amex-content label{{display:block;margin:10px 0}}
    .amex-content .notice{{padding:12px;border:1px solid #fcd34d;
        background:#fffbeb;border-radius:14px;margin:12px 0}}
    .amex-content section{{margin:24px 0}}
    .person-context{{margin-top:8px;font-size:.9rem;white-space:normal;
        overflow-wrap:anywhere;max-width:28rem}}
    .table-shell td:nth-child(2),.table-shell td:nth-child(3){{white-space:nowrap}}
    .report-beneficiary{{overflow-wrap:anywhere}}
    @media(max-width:600px){{.table-shell table{{min-width:720px}}}}
    </style></head><body><div class="container">{navigation}
    {_contabilidad_subnav("amex")}{breadcrumbs}{hero}
    <main class="amex-content surface">{AMEX_CONTENT_START}{content}
    {AMEX_CONTENT_END}</main></div></body></html>"""


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
    _require_finance_actor(current_empleado)
    linked = exists().where(
        ExpenseReport.cuenta_gastos_id == Documento.cuenta_gastos_id,
        ExpenseReport.estado_gasto != "cancelado",
        company_amex_sql_condition(),
    )
    with session.no_autoflush:
        result = await session.execute(
            select(Documento)
            .options(raiseload("*"))
            .where(Documento.tipo == "INFORME", Documento.estado == "aprobado", linked)
            .order_by(Documento.aprobado_en.desc())
        )
    documents = result.scalars().all()
    context = await load_amex_attention_context(session, documents=documents)
    visible = [
        doc
        for doc in documents
        if context.document_decisions[cast(UUID, doc.id)].visible
    ]
    warnings = "".join(
        '<p class="notice">No se pudo verificar la exclusión por beneficiario; '
        "las partidas permanecen visibles para revisión.</p>"
        for warning in context.warnings
    )
    attention_reasons = {
        "amex_attention_missing_beneficiary": "Falta beneficiario explícito",
        "amex_attention_beneficiary_conflict": "Beneficiarios contradictorios",
        "amex_attention_multiple_or_missing_informes": "Informe ausente o ambiguo",
        "amex_attention_ambiguous_beneficiary": "Identidad de beneficiario ambigua",
        "amex_attention_configuration_disabled": (
            "Exclusión desactivada: revisar configuración"
        ),
    }
    rows = "".join(
        f'<tr><td>{escape(str(document.numero_referencia or "Informe"))}'
        f'<small>{escape(attention_reasons.get(
            context.document_decisions[cast(UUID, document.id)].reason, ""
        ))}</small></td>'
        f'<td><a href="{BASE}/informes/{document.id}">Preparar corte del informe</a></td></tr>'
        for document in visible[:100]
    )
    return Response(
        _page(
            "Revisión contable AMEX",
            warnings
            + "<p>Usa la comprobación aprobada y declara las decisiones pendientes "
            "en conjunto antes de confirmar el corte.</p>"
            f"<table><thead><tr><th>Informe aprobado</th><th>Acción</th></tr></thead>"
            f'<tbody>{rows or "<tr><td colspan=2>Sin informes AMEX aprobados.</td></tr>"}</tbody></table>',
            current_empleado,
        ),
        media_type="text/html",
    )


def _require_finance_actor(actor: Empleado) -> None:
    if not getattr(actor, "activo", False) or (
        str(getattr(actor, "rol", "") or "").strip().lower() not in FINANCE_AMEX_ROLES
    ):
        raise HTTPException(403, "Se requiere un perfil activo de Finanzas.")


def _exception_text(reason: str | None) -> str:
    explanations = {
        "amex_review_update_requires_confirmation": (
            "Confirma el informe completo para cambiar esta decisión contable "
            "y conservar su evidencia anterior."
        ),
        "stale_amex_finance_preview": "La fuente o las decisiones cambiaron. Vuelve a preparar el informe antes de confirmar.",
        "stale_amex_approval_evidence": "La evidencia de aprobación cambió. Vuelve a preparar el informe.",
        "incomplete_amex_finance_decisions": "Faltan decisiones para el conjunto completo de partidas del informe.",
        "invalid_amex_finance_decisions": "Revisa las partidas, tratamientos, cuentas y motivos declarados.",
        "amex_current_attestation_requires_reason": "Indica el motivo para atestar la fuente actual del informe.",
        "amex_finance_confirmation_payload_conflict": "Este corte ya fue confirmado con otra selección. Revisa el corte guardado.",
        "historical_amex_representation_requires_review": "Requiere revisión histórica AMEX separada.",
        "amex_recognition_not_activated": "El reconocimiento AMEX está pendiente de activación.",
        "missing_amex_card": "Falta identificar la tarjeta AMEX por Finanzas.",
        "invalid_amex_card_identity": "La tarjeta AMEX falta o ya no está activa.",
        "invalid_amex_liability": "Revisa la cuenta pasivo de la tarjeta AMEX.",
        "stale_review": "La decisión contable debe actualizarse porque cambió su fuente.",
        "stale_amex_review_source": "La decisión contable debe actualizarse porque cambió su fuente.",
        "unreviewed_amex_partida": "Falta declarar una decisión contable explícita.",
        "stale_amex_operational_source": "La fuente cambió desde la aprobación; requiere atestación actual de Finanzas.",
        "missing_amex_operational_attestation": "Falta atestación versionada; Finanzas confirmará la fuente actual con motivo.",
        "unsupported_mixed_amex_informe": "El informe contiene partidas de métodos distintos; revisa su contexto contable.",
        "missing_review": "Falta declarar una decisión contable explícita.",
        "closed_accounting_period": "El período contable está cerrado.",
        "invalid_amex_informe": "El informe no está aprobado o su contexto cambió.",
        "missing_final_informe_approval": "No hay evidencia de aprobación final disponible.",
        "stale_operational_amex_source": "La fuente cambió desde la aprobación; requiere atestación actual de Finanzas.",
    }
    return explanations.get(
        reason or "",
        "Revisa tarjeta, fuente, fiscalidad, cuentas y período antes de confirmar.",
    )


def _batch_decision_script() -> str:
    return """<script>
    (() => {
      const form = document.getElementById('amex-cut-confirmation');
      if (!form) return;
      const payload = document.getElementById('amex-decisions');
      const decisions = new Map();
      const error = document.getElementById('amex-batch-error');
      document.getElementById('amex-apply-batch').addEventListener('click', () => {
        error.textContent = '';
        const ids = Array.from(document.querySelectorAll('.amex-batch-item:checked'));
        const treatment = document.getElementById('amex-batch-treatment').value;
        const debtor = document.getElementById('amex-batch-debtor').value;
        const reason = document.getElementById('amex-batch-reason').value.trim();
        if (!ids.length || !treatment) {
          error.textContent = 'Selecciona partidas y declara el tratamiento del lote.';
          return;
        }
        if (treatment === 'partner_receivable' && (!debtor || !reason)) {
          error.textContent = 'Cargo al socio requiere cuenta y motivo.';
          return;
        }
        ids.forEach(input => {
          decisions.set(input.value, {
            treatment, debtor_account_id: treatment === 'expense' ? null : debtor,
            reason
          });
          const label = input.closest('tr').querySelector('.amex-pending-decision');
          label.textContent = treatment === 'expense'
            ? 'Declarado: Gasto de empresa' : 'Declarado: Cargo al socio por el total';
          input.checked = false;
        });
        const groups = new Map();
        decisions.forEach((decision, id) => {
          const key = JSON.stringify(decision);
          if (!groups.has(key)) groups.set(key, {...decision, expense_ids: []});
          groups.get(key).expense_ids.push(id);
        });
        payload.value = JSON.stringify(Array.from(groups.values()));
        document.getElementById('amex-batch-summary').textContent =
          decisions.size + ' partida(s) con decisión declarada para esta confirmación.';
      });
    })();
    </script>"""


@router.get(BASE + "/informes/{informe_id}", response_class=Response)
async def amex_accounting_report_review(
    informe_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_empleado: Empleado = require_admin_finanzas(),
):
    """Read the approved source and declare outstanding accounting choices in batches."""
    _require_finance_actor(current_empleado)
    preview = await preview_amex_approved_informe(
        session, informe_id=informe_id, actor=current_empleado
    )
    if getattr(preview, "reason", None) == "unauthorized_amex_accounting_actor":
        raise HTTPException(403, "Se requiere un perfil activo de Finanzas.")
    informe = preview.informe
    if informe is None or informe.tipo != "INFORME":
        raise HTTPException(404, "Informe no encontrado.")
    # Explicit beneficiary relations are loaded independently of the capturer.
    display_document = await session.get(
        Documento,
        informe_id,
        populate_existing=True,
        options=[
            raiseload("*"),
            joinedload(Documento.beneficiario_empleado),
            joinedload(Documento.beneficiario_proveedor_cliente),
            joinedload(Documento.cuenta_gastos).joinedload(
                CuentaDeGastos.beneficiario_empleado
            ),
            joinedload(Documento.cuenta_gastos).joinedload(
                CuentaDeGastos.beneficiario_proveedor_cliente
            ),
        ],
    )
    beneficiary_name = effective_document_beneficiary_name(
        display_document or informe, fallback="Sin identificar", explicit_only=True
    )
    expenses = list(preview.expenses or [])
    reviews = preview.reviews or {}
    fresh_ids = set(preview.fresh_review_ids or [])
    exceptions = preview.exceptions or {}
    versions = {
        str(key): int(value) for key, value in (preview.current_versions or {}).items()
    }
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
    identities = await load_amex_card_display_identities(
        session, [expense.id for expense in expenses]
    )
    actor_ids = {review.actor_id for review in reviews.values()}
    approval = preview.approval_event
    if approval is not None:
        actor_ids.add(approval.aprobador_id)
    actors = {}
    if actor_ids:
        actor_rows = await session.execute(
            select(Empleado).where(Empleado.id.in_(actor_ids))
        )
        actors = {actor.id: actor for actor in actor_rows.scalars().all()}
    approval_text = (
        "Aprobación final no disponible; no se atribuye una comprobación histórica."
    )
    if approval is not None:
        name = (
            getattr(actors.get(approval.aprobador_id), "nombre", None)
            or "Actor registrado"
        )
        stamp = approval.fecha.isoformat() if approval.fecha else "Fecha no disponible"
        approval_text = f"Aprobación operativa: {escape(name)} · {escape(stamp)}"
    evidence = preview.operational_evidence
    source_requires_attestation = evidence is None or "source" in exceptions
    source_notice = (
        "La fuente cambió desde la aprobación. Finanzas debe atestar la fuente actual con motivo."
        if evidence is not None and "source" in exceptions
        else (
            "Atestación operativa versionada disponible."
            if evidence is not None
            else "Falta atestación operativa versionada. Finanzas debe atestar la fuente actual "
            "con motivo; esto no reconstruye lo que vio el aprobador histórico."
        )
    )
    account_labels = {
        account.id: f"{account.codigo} · {account.nombre or ''}" for account in accounts
    }
    rows = []
    for expense in expenses:
        identity = identities.get(expense.id)
        holder = identity.cardholder_name if identity else "Sin identificar"
        last4 = (
            f"••••{identity.last4}"
            if identity and identity.last4
            else "Sin identificar"
        )
        declared_last4 = str(getattr(expense, "ultimos_4_digitos", None) or "")
        declared_card_hint = (
            f"Terminación declarada: ••••{escape(declared_last4)}"
            if identity is None
            and len(declared_last4) == 4
            and declared_last4.isascii()
            and declared_last4.isdigit()
            else ""
        )
        review = reviews.get(expense.id)
        review_text = (
            "Sin decisión contable; selecciona un lote y declara su tratamiento."
        )
        if review is not None:
            treatment = (
                "Gasto de empresa"
                if review.treatment == "expense"
                else "Cargo al socio por el total"
            )
            review_actor = (
                getattr(actors.get(review.actor_id), "nombre", None)
                or "Actor registrado"
            )
            reviewed_at = (
                review.reviewed_at.isoformat()
                if review.reviewed_at
                else "Fecha no disponible"
            )
            freshness = (
                "Decisión vigente conservada"
                if expense.id in fresh_ids
                else "Decisión requiere actualización explícita"
            )
            debtor = (
                account_labels.get(review.debtor_account_id, "Cuenta no disponible")
                if review.debtor_account_id
                else ""
            )
            review_text = (
                f"{freshness}: {treatment} · {review_actor} · {reviewed_at} · "
                f"versión {review.version}"
                + (f" · {debtor}" if debtor else "")
                + (f" · Motivo: {review.reason}" if review.reason else "")
            )
        exception = exceptions.get(str(expense.id)) or exceptions.get(expense.id)
        exception_text = (
            f'<p class="notice">{escape(_exception_text(exception))}</p>'
            if exception
            else ""
        )
        cfdi = getattr(expense, "cfdi_report", None)
        cfdi_uuid = getattr(cfdi, "cfdi_uuid", None) or getattr(
            expense, "cfdi_uuid_manual", None
        )
        fiscal_text = escape(str(cfdi_uuid)) if cfdi_uuid else "Sin CFDI vinculado"
        if getattr(expense, "cfdi_report_id", None):
            fiscal_text = f'<a href="/gastos/{expense.id}">{fiscal_text}</a>'
        account = getattr(expense, "cuenta_contable", None)
        mapping = (
            getattr(account, "codigo", None)
            or getattr(expense, "cuenta_contable_base", None)
            or "Cuenta pendiente"
        )
        amount = (
            f"${expense.gasto_cantidad:,.2f}"
            if expense.gasto_cantidad is not None
            else "Sin importe"
        )
        row_id = escape(str(expense.numero_referencia or expense.id))
        rows.append(
            f"""<tr>
            <td><input type="checkbox" class="amex-batch-item" value="{expense.id}"
                aria-label="Seleccionar {row_id} para tratamiento del lote"></td>
            <td><a href="/gastos/{expense.id}">{row_id}</a><br>
                {escape(str(expense.concepto or ''))}
                <div class="person-context"><strong>Titular AMEX:</strong> {escape(holder)}
                <br><strong>Tarjeta:</strong> {escape(last4)}
                <br>{declared_card_hint}
                <br><strong>Pago:</strong> {escape(str(expense.metodo_pago or 'Sin método'))}</div></td>
            <td>{expense.fecha.date().isoformat() if expense.fecha else 'Sin fecha'}</td>
            <td>{amount}<br>{escape(str(getattr(expense, 'currency', None) or 'Moneda pendiente'))}</td>
            <td>{fiscal_text}<br>{escape(str(mapping))}{exception_text}</td>
            <td><div>{escape(review_text)}</div>
                <div class="amex-pending-decision" aria-live="polite"></div></td>
        </tr>"""
        )
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
    global_exception = (
        f'<p class="notice">{escape(_exception_text(preview.reason))}</p>'
        if preview.status == "pending"
        else ""
    )
    for key, exception in exceptions.items():
        if str(key) not in versions:
            global_exception += (
                f'<p class="notice">{escape(_exception_text(exception))}</p>'
            )
    currencies = {getattr(expense, "currency", None) for expense in expenses}
    total_text = "Importe total no disponible"
    if expenses and all(expense.gasto_cantidad is not None for expense in expenses):
        if len(currencies) == 1 and None not in currencies:
            total_text = (
                f"${sum(expense.gasto_cantidad for expense in expenses):,.2f} "
                f"{escape(str(next(iter(currencies))))}"
            )
        else:
            total_text = "Monedas distintas o pendientes; sin total consolidado."

    account_options = (
        '<option value="">Selecciona la cuenta del socio</option>'
        + "".join(
            f'<option value="{account.id}">{escape(str(account.codigo))} · '
            f'{escape(str(account.nombre or ""))}</option>'
            for account in accounts
        )
    )
    approval_id = str(approval.id) if approval is not None else ""
    approval_version = (
        str(preview.approval_version) if preview.approval_version is not None else ""
    )
    reason_required = "required" if cuts or source_requires_attestation else ""
    disabled = "disabled" if preview.status == "pending" else ""
    cut_button_label = (
        "Generar una póliza de ajuste" if cuts else "Generar una póliza del corte"
    )
    content = f"""<p class="report-beneficiary"><strong>Beneficiario del informe:</strong>
        {escape(beneficiary_name)}</p><p>{approval_text}</p>
        <p><strong>Total de partidas activas:</strong> {total_text}</p>
        <p class="notice">{source_notice}</p>{global_exception}
        {f'<p class="notice">{escape(notice)}</p>' if notice else ''}
        <p>La comprobación aprobada es la fuente del informe. Finanzas declara en conjunto
        las decisiones contables pendientes; el corte conserva todas las partidas activas.</p>
        <div class="table-shell"><table><thead><tr><th>Lote</th><th>Partida y pago</th>
        <th>Fecha</th><th>Total</th><th>Evidencia y cuentas</th><th>Decisión contable</th></tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan="6">Sin partidas activas.</td></tr>'}</tbody></table></div>
        <section><h2>Declarar tratamiento del lote seleccionado</h2>
        <p>Las decisiones vigentes se conservan con su autor y versión. Selecciona únicamente
        las partidas cuyo tratamiento quieres declarar o cambiar.</p>
        <label>Tratamiento<select id="amex-batch-treatment">
            <option value="">Selecciona un tratamiento</option>
            <option value="expense">Gasto de empresa</option>
            <option value="partner_receivable">Cargo al socio por el total</option>
        </select></label>
        <label>Cuenta de deudores socios<select id="amex-batch-debtor">{account_options}</select></label>
        <label>Motivo del lote<input id="amex-batch-reason" maxlength="1000"></label>
        <button class="button secondary" type="button" id="amex-apply-batch">
            Declarar para seleccionados</button>
        <p id="amex-batch-error" role="alert"></p><p id="amex-batch-summary" aria-live="polite"></p>
        </section><section><h2>{'Reclasificación del informe' if cuts else 'Corte contable del informe'}</h2>
        <form id="amex-cut-confirmation" method="POST" action="{BASE}/informes/{informe_id}/cortes">
            <input type="hidden" name="confirmation_mode" value="approved_informe">
            <input type="hidden" name="expected_source_key" value="{escape(preview.source_key or '', quote=True)}">
            <input type="hidden" name="expected_versions" value="{escape(json.dumps(versions), quote=True)}">
            <input type="hidden" name="expected_approval_id" value="{approval_id}">
            <input type="hidden" name="expected_approval_version" value="{approval_version}">
            <input type="hidden" id="amex-decisions" name="decisions" value="[]">
            <input type="hidden" name="adjustment" value="{'true' if cuts else 'false'}">
            <label>Fecha contable<input type="date" name="accounting_date" value="{cut_date.isoformat()}" required></label>
            <label>Motivo de atestación o ajuste<input name="reason" maxlength="1000" {reason_required}></label>
            <label><input type="checkbox" name="confirmed" value="true" required>
                Confirmo la fuente actual, las decisiones declaradas y el informe completo</label>
            <button class="button primary" type="submit" {disabled}>
                {cut_button_label}
            </button>
        </form></section><section><h2>Cortes guardados</h2><ul>
        {downloads or '<li>Aún no se ha generado un corte.</li>'}</ul></section>"""
    return Response(
        _page(
            f"Informe {informe.numero_referencia or ''} · AMEX",
            content + _batch_decision_script(),
            current_empleado,
        ),
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
            informe_id,
            "No se guardó la revisión. " + _exception_text(result.reason),
            error=True,
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
    confirmation_mode: str | None = Form(None),
    expected_source_key: str | None = Form(None),
    expected_approval_id: str | None = Form(None),
    expected_approval_version: str | None = Form(None),
    decisions: str | None = Form(None),
):
    if not confirmed:
        return _redirect(
            informe_id, "Confirma la revisión del informe completo.", error=True
        )
    metadata = (
        confirmation_mode,
        expected_source_key,
        expected_approval_id,
        expected_approval_version,
        decisions,
    )
    consolidated = any(isinstance(value, str) for value in metadata)
    source_key_value = (
        expected_source_key if isinstance(expected_source_key, str) else ""
    )
    try:
        versions = json.loads(expected_versions)
        minimum = 0 if consolidated else 1
        if not isinstance(versions, dict) or not all(
            isinstance(key, str) and type(value) is int and value >= minimum
            for key, value in versions.items()
        ):
            raise ValueError
        if consolidated:
            _require_finance_actor(current_empleado)
            if confirmation_mode != "approved_informe" or not source_key_value:
                raise ValueError
            if not isinstance(decisions, str):
                raise ValueError
            groups = json.loads(decisions)
            if not isinstance(groups, list):
                raise ValueError
            selected = set()
            for group in groups:
                if not isinstance(group, dict) or not group.get("expense_ids"):
                    raise ValueError
                if not isinstance(group["expense_ids"], list):
                    raise ValueError
                if group.get("treatment") not in {"expense", "partner_receivable"}:
                    raise ValueError
                if not isinstance(group.get("reason", ""), str):
                    raise ValueError
                debtor = group.get("debtor_account_id")
                if debtor is not None:
                    UUID(debtor)
                for item in group["expense_ids"]:
                    UUID(item)
                    if item not in versions or item in selected:
                        raise ValueError
                    selected.add(item)
            for item in versions:
                UUID(item)
            approval_id = UUID(expected_approval_id) if expected_approval_id else None
            approval_version = (
                int(expected_approval_version) if expected_approval_version else None
            )
            if approval_version is not None and approval_version <= 0:
                raise ValueError
        else:
            # Only the complete old shape reaches the legacy service and its guards.
            groups = []
            approval_id = None
            approval_version = None
    except (ValueError, TypeError):
        return _redirect(
            informe_id,
            "La fuente, selección o decisiones del informe son inválidas.",
            error=True,
        )
    try:
        if consolidated:
            result = await confirm_amex_approved_informe_cut(
                session,
                informe_id=informe_id,
                actor=current_empleado,
                expected_source_key=source_key_value,
                expected_versions=versions,
                expected_approval_id=approval_id,
                expected_approval_version=approval_version,
                decisions=groups,
                accounting_date=accounting_date,
                confirmed=confirmed,
                adjustment=adjustment,
                reason=reason,
            )
        else:
            result = await create_amex_accounting_cut(
                session,
                informe_id=informe_id,
                actor=current_empleado,
                expected_versions=versions,
                accounting_date=accounting_date,
                adjustment=adjustment,
                reason=reason,
            )
    except Exception:
        await session.rollback()
        raise
    if result.status == "pending":
        await session.rollback()
        return _redirect(
            informe_id,
            "No se generó el corte. " + _exception_text(result.reason),
            error=True,
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
