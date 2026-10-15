"""Statement-native AMEX UI; financial mutations remain service-owned."""

import json
import secrets
from datetime import date
from decimal import Decimal
from html import escape
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData, UploadFile

from ..models import Empleado
from ..services import amex_direct_accounting_service as accounting
from ..services import amex_direct_budget_service as budget
from ..services import amex_direct_context_service as context
from ..services import amex_direct_intake_service as intake
from ..services import amex_direct_matching_service as matching
from ..services import amex_direct_status_service as status
from ..services.amex_direct_common import DirectSchemaUnavailable
from . import dependencies
from .admin_amex_accounting_routes import (
    FINANCE_AMEX_ROLES,
    _page,
    _require_finance_actor,
)
from .dependencies import get_current_empleado, get_db_session, require_admin_finanzas

router = APIRouter()
BASE = "/admin/contabilidad/amex/estados"
QUEUE = "/documentos/control-presupuestal/amex-directo"
_CSRF_KEY = "amex_direct_csrf"
_MAX_BYTES = 10 * 1024 * 1024


def _text(value) -> str:
    return escape(str(value)) if value is not None and value != "" else "Pendiente"


def _profile_label(profile: str | None) -> str:
    return {
        "debit_negative": "Negativo = cargo; positivo = crédito",
        "debit_positive": "Positivo = cargo; negativo = crédito",
        "separate_columns": "Columnas separadas cargo/abono",
    }.get(profile or "", "Perfil pendiente")


def _stage_label(stage: str | None) -> str:
    return {
        "pending": "Pendiente",
        "matched": "Vínculo fiscal confirmado",
        "candidate": "Propuesta por revisar",
        "ambiguous": "Coincidencia ambigua",
        "unclassified": "Sin clasificación",
        "classified": "Clasificado por Contabilidad",
        "sent": "En Control Presupuestal",
        "assigned": "Partida asignada",
        "rejected": "Devuelto a Contabilidad",
        "recognized": "Reconocido",
        "posted": "Contabilizado",
        "bound": "Cubierto por reconocimiento previo",
        "excluded": "No nuevo gasto declarado",
    }.get(stage or "", "Estado pendiente de lectura")


def _reason(code: str | None) -> str:
    return {
        "all_sources_already_recognized": "Todos los movimientos están cubiertos o declarados como no nuevo gasto. Se conservan sus pólizas; no se genera una póliza de importe cero.",
        "statement_row_unresolved": "Hay filas omitidas o signos sin resolver. Finanzas debe revisar el estado completo antes de generar la póliza.",
        "credit_exclusion_required": "Finanzas debe declarar cada crédito conocido como no nuevo gasto; no se registra un pago.",
        "budget_assignment_required": "Control Presupuestal debe asignar la partida de cada cargo nuevo antes del corte completo.",
        "budget_assignment_changed": "Cambió la asignación presupuestal. Control Presupuestal debe revisar la partida y versión actuales.",
        "budget_version_not_operational": "La versión presupuestal no está habilitada para estos movimientos. Control Presupuestal debe revisar su estado.",
        "stale_item_context": "La fila pertenece a una revisión anterior de contexto. Contabilidad debe revisar la fuente actual.",
        "fiscal_identity_changed": "Cambió la identidad fiscal de la factura. Finanzas debe revisar el vínculo antes de confirmar.",
        "fiscal_source_changed": "Cambió la fuente fiscal del cargo. Finanzas debe conciliar su evidencia actual.",
        "fresh_vigente_receipt_required": "Finanzas debe consultar vigencia actual del XML antes de confirmar el corte.",
        "merchant_identity_changed": "Cambió la identidad de comercio confirmada. Finanzas debe revisar y conciliar el cargo de nuevo.",
        "fiscal_match_required": "Falta confirmar la coincidencia fiscal del cargo con una factura válida.",
        "fiscal_source_missing": "Falta una fuente fiscal activa y verificable del cargo. Contabilidad debe revisar la evidencia.",
        "recognized_source_receipt_incomplete": "El reconocimiento previo tiene evidencia incompleta. Finanzas debe revisar su recibo sin duplicar gasto.",
        "recognized_source_identity_changed": "La fuente ya reconocida contradice su evidencia anterior. Finanzas debe revisar el conflicto sin sobrescribirla.",
        "recognized_source_card_mismatch": "El reconocimiento previo pertenece a otra tarjeta. Finanzas debe revisar la identidad económica.",
        "direct_cut_request_conflict": "Ya existe un corte con otra confirmación. Revisa su evidencia congelada; no se generó otra póliza.",
        "direct_confirmation_required": "Finanzas debe confirmar el estado completo, su fecha y la petición actual.",
        "stale_direct_source": "Cambió la fuente completa del corte. Recarga el preview antes de confirmar.",
        "accounting_date_outside_statement": "Finanzas debe elegir una fecha contable dentro del período declarado del estado.",
        "explicit_open_accounting_period_required": "Contabilidad debe habilitar un período contable abierto para la fecha elegida; no existe un período registrado que permita confirmar la póliza.",
        "closed_amex_accounting_period": "El período contable está cerrado. Finanzas debe elegir una fecha permitida y Contabilidad revisar el período antes de confirmar.",
        "expense_date_outside_budget_edition": "Contabilidad debe seleccionar una edición presupuestal compatible con la fecha original del cargo; no se cambia su fecha.",
        "accounting_date_outside_budget_edition": "Finanzas debe revisar la fecha contable y Contabilidad la edición presupuestal de los cargos; deben ser compatibles antes de confirmar.",
        "retention_account_mapping_conflict": "La cuenta de retención contradice el mapping fiscal vigente. Contabilidad debe revisar el conflicto; no se sobrescribe la cuenta ni se confirma una póliza parcial.",
        "unbalanced_direct_journal": "La propuesta contable no está balanceada. Contabilidad debe revisar cuentas y evidencia; no se genera una póliza parcial.",
        "direct_cut_required": "Finanzas debe confirmar un corte completo antes de descargar su póliza.",
        "explicit_project_required": "Contabilidad debe elegir exactamente un torneo o proyecto autorizado para los cargos.",
        "budget_category_context_mismatch": "La categoría no corresponde al proyecto o edición. Contabilidad debe revisar la selección del catálogo.",
        "classification_return_required": "Control Presupuestal debe devolver los cargos antes de que Contabilidad cambie su clasificación.",
        "classification_required": "Contabilidad debe clasificar los cargos en el contexto actual antes de enviarlos.",
        "budget_submission_confirmation_required": "Contabilidad debe confirmar explícitamente los cargos seleccionados para Control Presupuestal.",
        "stale_item_version": "El cargo cambió. Recarga sus datos antes de asignar o clasificar.",
        "budget_submission_required": "El cargo debe estar enviado a Control Presupuestal en el contexto actual para asignarle partida.",
        "budget_line_context_mismatch": "La partida o versión no corresponden al proyecto, categoría o edición. Control Presupuestal debe revisar la selección.",
        "budget_account_mapping_required": "Contabilidad debe completar la cuenta activa del catálogo de la partida presupuestal.",
        "invalid_amex_liability": "Finanzas debe revisar la cuenta pasivo activa de esta tarjeta; no se sustituye por una cuenta genérica.",
        "account_mapping_conflict": "La cuenta existente contradice la del catálogo presupuestal. Contabilidad debe revisar el conflicto; no se sobrescribe.",
        "already_recognized_source": "El consumo ya tiene reconocimiento real. Su póliza y evidencia se conservan sin generar otro gasto.",
        "budget_return_reason_required": "Control Presupuestal debe devolver el cargo actual con un motivo explícito.",
        "direct_item_missing": "El cargo directo ya no está disponible en esta selección. Recarga el estado.",
        "sat_consent_required": "Finanzas debe autorizar explícitamente la consulta de vigencia SAT.",
        "sat_consultation_in_progress": "Ya hay una consulta SAT en curso para este tramo. Revisa su progreso antes de continuar.",
        "sat_progress_source_changed": "La fuente cambió durante la consulta. Los resultados observados se conservan; recarga el contexto para continuar.",
        "stale_source_version": "Los datos cambiaron. Recarga el estado antes de confirmar.",
        "authorized_context_changed": "Cambió la configuración autorizada. Finanzas debe revisar el contexto antes de continuar.",
        "context_confirmation_required": "Finanzas debe confirmar explícitamente el contexto fiscal.",
        "context_correction_reason_required": "Finanzas debe explicar el motivo de la corrección de contexto.",
        "csv_profile_change_requires_review": "El perfil y signo originales se conservan. Finanzas debe revisar el archivo; este cambio no reinterpreta los cargos.",
        "file_context_conflict": "El archivo ya existe con otro contexto. Revisa la carga original; no se duplicó ni se modificó.",
        "source_confirmation_required": "Confirma explícitamente el archivo, tarjeta, período y contexto.",
        "source_type_invalid": "Selecciona un CSV permitido. No se conserva otro tipo de archivo como estado.",
        "source_size_invalid": "El CSV debe contener datos y no exceder 10 MiB.",
        "missing_columns": "Revisa las columnas fecha, descripción e importe del CSV y el perfil declarado.",
        "invalid_utf8": "El CSV requiere texto UTF-8; revisa el archivo antes de cargarlo.",
        "no_statement_rows": "El archivo no contiene movimientos; no se creó una póliza.",
        "inactive_context": "Finanzas debe revisar la tarjeta o titular activos de esta carga.",
        "employee_context_denied": "Finanzas debe elegir un titular autorizado para esta tarjeta y contexto fiscal antes de cargar el estado.",
        "row_outside_declared_period": "La fecha original queda fuera del período declarado. Finanzas debe revisar el archivo y período.",
        "invalid_date": "Falta una fecha válida en la fila original; Finanzas debe revisar el CSV.",
        "missing_description": "Falta la descripción original del movimiento; Finanzas debe revisar el CSV.",
        "invalid_signed_amount": "El importe original no es verificable. Finanzas debe revisar la celda y su signo.",
        "contradictory_debit_credit_columns": "Las columnas cargo y abono son contradictorias. Finanzas debe revisar el CSV.",
        "contradictory_column_sign": "El signo contradice la columna cargo/abono. Finanzas debe revisar el archivo.",
        "csv_profile_required": "Finanzas debe declarar el perfil de signo del archivo.",
        "statement_currency_required": "Finanzas debe declarar la moneda del estado; no se presume MXN.",
        "unsupported_direct_currency": "La primera entrega reconoce sólo MXN. La moneda original se conserva sin conversión.",
        "fiscal_identity_pending": "Finanzas debe confirmar identidad de comercio y vigencia; Contabilidad revisa la factura SAT.",
        "classification_context_changed": "Cambió el contexto: Contabilidad debe revisar la clasificación y Control Presupuestal la partida.",
        "merchant_identity_or_fresh_sat_evidence_pending": "Finanzas debe revisar el RFC o alias exacto del comercio y la vigencia SAT; importe y fecha solos no confirman una factura.",
        "merchant_identity_confirmation_required": "Finanzas debe confirmar el descriptor exacto, RFC emisor y motivo del alias.",
        "ambiguous_cfdi_candidates": "Hay varias facturas posibles. Contabilidad debe resolver la coincidencia; no se eligió una automáticamente.",
        "global_bidirectional_match_conflict": "Otro cargo o uso compite por esta factura. Contabilidad debe revisar la identidad; no se duplica su aplicación.",
        "fresh_sat_vigency_required": "Finanzas debe consultar vigencia SAT actual ligada al XML; descarga o evidencia antigua no acredita vigencia.",
        "canonical_sat_xml_required": "Contabilidad debe revisar el XML canónico descargado del SAT; faltan datos fiscales verificables.",
        "cfdi_material_identity_conflict": "Los datos de factura y XML se contradicen. Contabilidad debe revisar la fuente fiscal.",
        "invoice_type_i_required": "Esta factura no es de tipo ingreso. Contabilidad debe atender la excepción fiscal.",
        "receiver_or_currency_conflict": "La factura no coincide con el receptor o moneda declarados. Finanzas debe revisar el contexto sin modificar el XML.",
        "cfdi_missing": "Contabilidad debe seleccionar una factura canónica disponible.",
        "cfdi_already_used": "La factura ya tiene otro uso. Contabilidad debe revisar la aplicación sin duplicar gasto.",
        "cfdi_global_usage_conflict": "La factura tiene un uso incompatible. Contabilidad debe revisar la distribución declarada.",
        "foreign_cfdi_application_conflict": "La aplicación fiscal pertenece a otra fuente. No se modifica desde este cargo.",
        "manual_applied_amount_conflict": "El importe aplicado y la evidencia fiscal no coinciden. Revisa la distribución explícita.",
        "invalid_confirmed_tip": "Revisa la propina declarada; no se calcula por diferencia automáticamente.",
        "manual_matching_confirmation_required": "Finanzas debe confirmar el vínculo manual con motivo y fuente actual.",
        "item_context_mismatch": "El movimiento no pertenece al estado o tipo esperado. Recarga la selección actual.",
        "credit_exclusion_confirmation_required": "Finanzas debe confirmar el crédito como no nuevo gasto con motivo explícito.",
        "known_credit_required": "Sólo un crédito original conocido puede excluirse como no nuevo gasto.",
        "recognized_source_immutable": "La fuente ya fue reconocida. Su evidencia se conserva; una corrección requiere un ajuste autorizado separado.",
        "direct_cut_immutable": "El corte está congelado. Su fuente y decisiones no se modifican desde esta pantalla.",
        "original_source_missing": "Esta carga no conserva los bytes originales; no se reconstruye el archivo.",
        "original_source_integrity_failed": "No pudo verificarse la integridad del archivo original. Soporte debe revisar su custodia antes de descargar.",
        "contexto_fiscal_pendiente": "Finanzas debe declarar un contexto fiscal autorizado para esta tarjeta.",
        "missing_merchant_identity": "Finanzas debe confirmar el RFC del comercio o su alias exacto.",
        "merchant_identity_missing": "Finanzas debe confirmar el RFC del comercio o su alias exacto.",
        "missing_cfdi": "Contabilidad debe revisar la factura SAT del cargo.",
        "ambiguous_cfdi": "Hay varias facturas posibles; Contabilidad debe resolver la coincidencia.",
        "sat_status_pending": "Falta consultar la vigencia SAT con consentimiento de Finanzas.",
        "sat_status_stale": "La vigencia SAT debe consultarse de nuevo; la evidencia tiene más de 24 horas.",
        "unknown_movement": "Finanzas debe revisar el perfil y signo original; no se presume que sea un gasto.",
        "credit_requires_exclusion": "Finanzas debe declarar el crédito como no nuevo gasto; esto no registra un pago.",
        "source_changed": "Los datos cambiaron. Recarga el estado antes de confirmar.",
        "stale_source": "Los datos cambiaron. Recarga el estado antes de confirmar.",
        "missing_budget_assignment": "Control Presupuestal debe asignar la partida del proyecto y categoría declarados.",
        "missing_budget_mapping": "Falta el catálogo de cuenta contable activo de la partida presupuestal.",
        "statement_incomplete": "Hay cargos pendientes. La póliza requiere el estado completo.",
        "no_new_consumptions": "Todos los consumos están cubiertos o no generan gasto; no se crea una póliza cero.",
        "missing_original_source": "Esta carga no conserva los bytes originales; no se reconstruye el archivo.",
    }.get(
        code or "",
        "Revisa el contexto, la evidencia fiscal y presupuestal actuales. No se confirmó el cambio.",
    )


def _csrf(request: Request) -> str:
    token = str(request.session.get(_CSRF_KEY) or "")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_KEY] = token
    return (
        f'<input type="hidden" name="_csrf_token" value="{escape(token, quote=True)}">'
    )


async def _form(request: Request) -> FormData:
    form = await request.form()
    expected = str(request.session.get(_CSRF_KEY) or "")
    supplied = str(form.get("_csrf_token") or "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(
            403, "La acción no pertenece a esta sesión. Recarga la pantalla."
        )
    return form


def _csv_file(form: FormData) -> UploadFile:
    files = form.getlist("file")
    file = files[0] if len(files) == 1 else None
    if not isinstance(file, UploadFile):
        raise HTTPException(422, "Selecciona un archivo CSV.")
    if (
        not str(file.filename or "").lower().endswith(".csv")
        or file.content_type not in intake.MIME_TYPES
    ):
        raise HTTPException(422, "Selecciona un CSV con un tipo de archivo permitido.")
    return file


def _value(form: FormData, name: str, *, required: bool = False) -> str:
    values = form.getlist(name)
    if len(values) > 1 or (values and not isinstance(values[0], str)):
        raise HTTPException(
            422, "El formulario contiene campos inválidos o duplicados."
        )
    value = str(values[0] if values else "").strip()
    if required and not value:
        raise HTTPException(422, "Faltan datos obligatorios. Revisa el formulario.")
    return value


def _uuid(form: FormData, name: str) -> UUID:
    value = _value(form, name, required=True)
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(
            422, "La selección no tiene una identidad válida."
        ) from None


def _optional_uuid(form: FormData, name: str) -> UUID | None:
    return _uuid(form, name) if _value(form, name) else None


def _version(form: FormData) -> int:
    try:
        value = int(_value(form, "expected_source_version", required=True))
        if value < 1:
            raise ValueError
        return value
    except ValueError:
        raise HTTPException(
            422, "La versión de fuente no es válida. Recarga la pantalla."
        ) from None


def _confirmed(form: FormData, name: str = "confirmed") -> bool:
    return _value(form, name) == "true"


def _selection(form: FormData) -> tuple[list[UUID], dict[str, int]]:
    try:
        ids = [UUID(str(value)) for value in form.getlist("item_ids")]
        versions = json.loads(_value(form, "expected_versions", required=True))
        if (
            not ids
            or len(ids) != len(set(ids))
            or not isinstance(versions, dict)
            or any(type(v) is not int or v < 1 for v in versions.values())
            or any(str(item_id) not in versions for item_id in ids)
        ):
            raise ValueError
        return ids, {str(item_id): versions[str(item_id)] for item_id in ids}
    except (ValueError, TypeError):
        raise HTTPException(
            422, "Selecciona cargos explícitos y recarga sus versiones actuales."
        ) from None


def _source_version(result) -> int:
    version = result.source_version
    if type(version) is not int or version < 1:
        raise HTTPException(409, "No se pudo verificar la versión actual de fuente.")
    return version


def _redirect(import_id: UUID, message: str) -> RedirectResponse:
    return RedirectResponse(
        f"{BASE}/{import_id}/directo?{urlencode({'msg': message})}", 303
    )


def _html(title: str, body: str, actor: Empleado) -> Response:
    styles = """<style>.direct-table{overflow-x:auto;max-width:100%}
    .amex-content .direct-table td{white-space:normal;overflow-wrap:anywhere;
    max-width:24rem;min-width:7rem}.direct-form{max-width:760px}
    .direct-form.direct-classification{max-width:none}
    .direct-classification>label{max-width:760px}
    .direct-form label{display:block;margin:12px 0}.direct-form input:not([type=checkbox]),
    .direct-form select{width:100%;box-sizing:border-box}.direct-actions{display:flex;
    gap:12px;flex-wrap:wrap}.direct-table table{min-width:1200px}</style>"""
    return Response(
        _page(title, styles + body, actor, workflow="direct"),
        media_type="text/html",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _hidden(name: str, value) -> str:
    return (
        f'<input type="hidden" name="{name}" value="{escape(str(value), quote=True)}">'
    )


def _submit(label: str) -> str:
    return f'<button class="button primary" type="submit">{escape(label)}</button>'


def _confirm(label: str = "Confirmo esta acción sobre la fuente actual") -> str:
    return f'<label><input type="checkbox" name="confirmed" value="true" required>{label}</label>'


def _open(request: Request, import_id: UUID, action: str, version: int) -> str:
    form_class = (
        "direct-form direct-classification" if action == "clasificar" else "direct-form"
    )
    return (
        f'<form class="{form_class}" method="POST" action="{BASE}/{import_id}/directo/{action}">'
        + _csrf(request)
        + _hidden("expected_source_version", version)
    )


def _is_finance(actor: Empleado) -> bool:
    return bool(actor.activo) and str(actor.rol).strip().lower() in FINANCE_AMEX_ROLES


def _actor_id(actor: Empleado) -> UUID:
    return UUID(str(actor.id))


async def _call(awaitable):
    try:
        return await awaitable
    except DirectSchemaUnavailable:
        raise HTTPException(
            503,
            "El flujo directo está pendiente de instalación o verificación. Soporte debe habilitarlo antes de continuar.",
        ) from None
    except ValueError as exc:
        raise HTTPException(409, _reason(str(exc))) from None
    except PermissionError:
        raise HTTPException(
            403, "No tienes autoridad o ámbito para esta acción AMEX."
        ) from None


async def direct_upload_form(
    request: Request, session: AsyncSession, actor: Empleado
) -> Response:
    """Render explicit context intake without SAT or business writes."""
    _require_finance_actor(actor)
    options = await _call(
        context.list_direct_intake_options(session, actor_id=_actor_id(actor))
    )
    if not options.get("contexts") or not options.get("cards"):
        return _html(
            "Cargar estado AMEX directo",
            "<p>Finanzas debe configurar el contexto fiscal y ámbito autorizado de la tarjeta antes de cargar un estado directo.</p>"
            + f'<p><a href="{BASE}/nuevo">Recorrido de informes existente</a></p>',
            actor,
        )
    card_options = "".join(
        f'<option value="{card["id"]}">{_text(card["label"])} · ••••{_text(card["last4"])}</option>'
        for card in options.get("cards", [])
    )
    people_options = "".join(
        f'<option value="{person["id"]}">{_text(person["name"])}</option>'
        for person in options.get("people", [])
    )
    body = f"""<p>Estado directo: no requiere un INFORME. Conserva el archivo exacto y todos
    sus movimientos. Primera entrega: MXN, CSV de hasta 10 MiB.</p>
    <form id="amex-direct-intake" class="direct-form" method="POST" action="{BASE}/directo/nuevo" enctype="multipart/form-data">
    {_csrf(request)}{_hidden('workflow_mode', 'direct')}{_hidden('idempotency_key', uuid4())}
    <label>Tarjeta<select name="card_account_id" required><option value="">Selecciona tarjeta</option>{card_options}</select></label>
    <label>Titular declarado<select name="employee_id" required><option value="">Selecciona titular</option>{people_options}</select></label>
    <label>Inicio del período<input type="date" name="period_start" required></label>
    <label>Fin del período<input type="date" name="period_end" required></label>
    <label>Estado CSV<input type="file" name="file" accept=".csv,text/csv" required></label>
    {_context_inputs(options.get("contexts", []))}
    <label>Moneda declarada<select name="currency" required><option value="">Selecciona moneda</option><option value="MXN">MXN</option></select></label>
    <label>Sentido del archivo<select name="profile" required><option value="">Selecciona el perfil explícito</option>
    <option value="debit_negative">Negativo = cargo; positivo = crédito</option>
    <option value="debit_positive">Positivo = cargo; negativo = crédito</option>
    <option value="separate_columns">Columnas separadas cargo/abono</option></select></label>
    <label><input type="checkbox" name="sat_consented" value="true">Consultar vigencia y conciliar automáticamente</label>
    <p>Consulta máxima: 50 facturas y 30 segundos por petición. La continuación se solicita explícitamente;
    descargar del SAT no acredita vigencia.</p>{_confirm('Confirmo tarjeta, período y contexto declarados')}
    <button class="button secondary" type="button" id="amex-direct-preview">Previsualizar archivo y signo</button>
    <div id="amex-direct-preview-result" role="status"></div>
    <button class="button primary" id="amex-direct-import" type="submit" disabled>Cargar estado directo</button></form><p><a href="{BASE}/nuevo">Recorrido de informes existente</a></p>"""
    return _html("Cargar estado AMEX directo", body + _preview_script(), actor)


@router.get(BASE + "/directo")
async def direct_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Discover direct statements only within the current canonical source scope."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    summaries = await _call(
        context.list_direct_statements(session, actor_id=_actor_id(actor))
    )
    body = "<p>1. Cargar y conciliar el estado · 2. Clasificar y asignar partida · 3. Confirmar póliza y programar pago.</p>"
    if _is_finance(actor):
        body += f'<p><a class="button primary" href="{BASE}/directo/nuevo">Cargar estado directo</a></p>'
    body += f'<p><a href="{BASE}">Recorrido de informes existente</a> · Los estados directos conservan su propia fuente; no requieren INFORME.</p>'
    rows = []
    for summary in summaries:
        statement, card, counts = (
            summary["statement"],
            summary["card"],
            summary["counts"],
        )
        cut = summary.get("cut")
        rows.append(
            f'<tr><td>{_text(getattr(card, "card_label", None))} · ••••{_text(getattr(card, "last4", None))}</td>'
            f"<td>{_text(statement.filename)}<br>{_text(statement.period_start)} a {_text(statement.period_end)}</td>"
            f'<td>Cargos: {_text(counts.get("debit"))}<br>Créditos: {_text(counts.get("credit"))}<br>Signo desconocido: {_text(counts.get("unknown"))}<br>Omitidas: {_text(counts.get("skipped"))}</td>'
            f'<td>CFDI confirmado: {_text(counts.get("matched"))}</td>'
            f'<td>Clasificados: {_text(counts.get("classified"))}<br>En Control Presupuestal: {_text(counts.get("sent"))}<br>Partida asignada: {_text(counts.get("assigned"))}</td>'
            f'<td>{"Corte congelado: " + _text(cut.accounting_date) if cut is not None else "Sin corte directo confirmado"}<br>Consumos reconocidos: {_text(counts.get("recognized"))}</td>'
            f'<td><a class="button secondary" href="{BASE}/{statement.id}/directo">Continuar estado</a></td></tr>'
        )
    body += '<div class="direct-table table-shell"><table><thead><tr><th>Tarjeta</th><th>Fuente/período</th><th>Movimientos originales</th><th>Conciliación fiscal</th><th>Presupuesto</th><th>Contabilidad</th><th>Acción</th></tr></thead><tbody>'
    body += (
        "".join(rows)
        or '<tr><td colspan="7">No hay estados directos dentro de tu ámbito autorizado.</td></tr>'
    )
    body += "</tbody></table></div>"
    return _html("Estados AMEX directos", body, actor)


@router.get(BASE + "/directo/nuevo")
async def direct_new(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Canonical scoped direct intake, separate from the legacy route policy."""
    return await direct_upload_form(request, session, actor)


@router.post(BASE + "/directo/nuevo")
async def direct_new_upload(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Canonical explicit upload; service reloads authority and source scope."""
    return await direct_upload(request, session, actor)


def _preview_script() -> str:
    """Preview selected bytes in-place; source mutations still revalidate server-side."""
    return """<script>(()=>{
    const form=document.getElementById('amex-direct-intake');
    const button=document.getElementById('amex-direct-preview');
    const submit=document.getElementById('amex-direct-import');
    const output=document.getElementById('amex-direct-preview-result');
    let generation=0;
    form.addEventListener('change',event=>{
      if(['file','profile','currency','card_account_id','employee_id','period_start','period_end','company_code','receiver_rfc'].includes(event.target.name)){
        generation++;submit.disabled=true;output.replaceChildren();}});
    button.addEventListener('click',async()=>{
      const current=++generation;submit.disabled=true;output.textContent='Validando archivo y perfil…';
      button.disabled=true;
      try{
        const response=await fetch('/admin/contabilidad/amex/estados/directo/previsualizar',{
          method:'POST',body:new FormData(form),credentials:'same-origin'});
        const data=await response.json();
        if(current!==generation)return;
        if(!response.ok)throw new Error(data.detail||'No se pudo previsualizar el CSV.');
        output.replaceChildren();
        const summary=document.createElement('p');
        summary.textContent=`${data.rows.length} filas originales. Perfil declarado: ${data.profile}. Revisa signo y tipo antes de confirmar.`;
        output.append(summary);
        const shell=document.createElement('div');shell.className='direct-table table-shell';
        const table=document.createElement('table');
        const header=document.createElement('tr');
        ['Fila','Celda original','Importe firmado','Tipo','Importe del cargo','Motivo'].forEach(label=>{
          const cell=document.createElement('th');cell.textContent=label;header.append(cell);});
        table.append(header);
        data.rows.forEach(row=>{
          const tr=document.createElement('tr');
          [row.row_number,row.raw_amount,row.raw_signed_amount,({debit:'Cargo',credit:'Crédito',unknown:'Signo desconocido',skipped:'Fila omitida'}[row.movement_kind]||'Tipo pendiente'),row.amount,row.reason_text||''].forEach(value=>{
            const td=document.createElement('td');td.textContent=value===null||value===undefined?'Pendiente':String(value);tr.append(td);});
          table.append(tr);
        });shell.append(table);output.append(shell);submit.disabled=false;
      }catch(error){if(current===generation)output.textContent=error.message;}
      finally{button.disabled=false;}
    });})();</script>"""


@router.post(BASE + "/directo/previsualizar")
async def direct_csv_preview(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    """Pure signed-row preview after owner scope authorization; no fiscal call or persistence."""
    _require_finance_actor(actor)
    form = await _form(request)
    await _call(context.list_direct_intake_options(session, actor_id=_actor_id(actor)))
    file = _csv_file(form)
    content = await file.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise HTTPException(413, "El CSV excede el límite de 10 MiB.")
    profile = _value(form, "profile", required=True)
    currency = _value(form, "currency", required=True)
    try:
        rows = intake.parse_direct_csv(content, profile=profile, currency=currency)
    except ValueError as exc:
        raise HTTPException(422, _reason(str(exc))) from None
    # Decimal and dates are rendered from the parser, never reconstructed from abs.
    rows = [
        {**row, "reason_text": _reason(row["reason"]) if row.get("reason") else ""}
        for row in rows
    ]
    return JSONResponse(
        json.loads(json.dumps({"rows": rows, "profile": profile}, default=str)),
        headers={"Cache-Control": "no-store"},
    )


async def _consult(actor_id: UUID, import_id: UUID, version: int, request_key: str):
    factory = dependencies._db_session_maker
    if factory is None:
        raise HTTPException(
            503, "La consulta SAT no está disponible. La carga permanece guardada."
        )
    return await _call(
        status.consult_direct_statement_status(
            factory,
            actor_id=actor_id,
            import_id=import_id,
            expected_source_version=version,
            request_key=request_key,
            consented=True,
        )
    )


async def _fresh_match(
    session: AsyncSession, actor: Empleado, import_id: UUID, version: int
) -> tuple[int, str, bool]:
    """Reuse approved fresh fiscal receipts after the original intake is committed."""
    try:
        result = await _call(
            matching.reconcile_direct_statement(
                session,
                actor_id=_actor_id(actor),
                import_id=import_id,
                expected_source_version=version,
            )
        )
        if result.status == "pending":
            await session.rollback()
            return version, _reason(result.reason), False
        latest_version = _source_version(result)
        await session.commit()
        return latest_version, "", True
    except HTTPException as exc:
        await session.rollback()
        # The original file is already committed; this optional follow-up never removes it.
        return version, str(exc.detail), False
    except Exception:
        await session.rollback()
        raise


async def direct_upload(
    request: Request, session: AsyncSession, actor: Empleado
) -> Response:
    """Commit intake before optional bounded external status consultation."""
    _require_finance_actor(actor)
    form = await _form(request)
    file = _csv_file(form)
    content = await file.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise HTTPException(413, "El CSV excede el límite de 10 MiB.")
    key = _value(form, "idempotency_key", required=True)
    try:
        result = await _call(
            intake.import_direct_statement(
                session,
                actor_id=_actor_id(actor),
                employee_id=_uuid(form, "employee_id"),
                card_account_id=_uuid(form, "card_account_id"),
                period_start=date.fromisoformat(
                    _value(form, "period_start", required=True)
                ),
                period_end=date.fromisoformat(
                    _value(form, "period_end", required=True)
                ),
                content=content,
                filename=file.filename or "estado.csv",
                mime_type=file.content_type or "text/csv",
                profile=_value(form, "profile") or None,
                company_code=_value(form, "company_code") or None,
                receiver_rfc=_value(form, "receiver_rfc") or None,
                currency=_value(form, "currency") or None,
                idempotency_key=key,
                confirmed=_confirmed(form),
            )
        )
        if result.status == "pending":
            await session.rollback()
            raise HTTPException(409, _reason(result.reason))
        import_id = UUID(str(result.statement.id))
        version = _source_version(result)
        await session.commit()
    except ValueError:
        await session.rollback()
        raise HTTPException(422, "Revisa las fechas y los datos declarados.") from None
    except Exception:
        await session.rollback()
        raise
    message = "Archivo y movimientos conservados. Revisa los pendientes del estado."
    can_consult = result.status == "imported"
    if result.status == "imported":
        version, local_message, can_consult = await _fresh_match(
            session, actor, import_id, version
        )
        if local_message:
            message += " " + local_message
    else:
        message = "Carga existente conservada. Continúa desde su estado guardado."
    if _confirmed(form, "sat_consented") and can_consult:
        # Both local transactions are closed before bounded external SAT chunks.
        observed = await _consult(_actor_id(actor), import_id, version, key)
        if observed.status == "pending":
            message += " " + _reason(observed.reason)
    return _redirect(import_id, message)


def _row(item) -> str:
    kind = {
        "debit": "Cargo",
        "credit": "Crédito",
        "unknown": "Signo desconocido",
        "skipped": "Fila omitida",
    }.get(item.movement_kind, "Tipo pendiente")
    fiscal = f"{_stage_label(item.match_status)}<br>{_text(getattr(item, 'cfdi_uuid', None) or item.cfdi_id)}"
    if item.match_reason:
        fiscal += "<br>" + escape(_reason(item.match_reason))
    fiscal += f"<br>Vigencia: {_text(getattr(item, 'sat_status', None))}"
    fiscal += f"<br>Observada: {_text(getattr(item, 'status_observed_at', None))}"
    select_item = f'<input type="checkbox" name="item_ids" value="{item.id}" aria-label="Seleccionar fila {item.row_number}">'
    return (
        f'<tr data-item-id="{item.id}"><td>{select_item}{item.row_number}</td>'
        f"<td>{_text(item.fecha)}<br>{_text(item.descriptor)}</td>"
        f"<td>Celda: {_text(item.raw_amount)}<br>Firmado: {_text(item.raw_signed_amount)}<br>{kind}<br>{escape(_profile_label(item.profile))}</td>"
        f"<td>{_text(item.amount)} {_text(item.currency)}</td><td>{fiscal}</td>"
        f"<td>{_text(getattr(item, "tournament_name", None) or getattr(item, "project_name", None) or item.tournament_id or item.project_code)}<br>Categoría: {_text(getattr(item, "budget_concept_name", None) or item.budget_concept_id)}</td>"
        f"<td>{_stage_label(item.budget_status)}<br>Partida: {_text(getattr(item, "budget_line_label", None) or item.budget_line_id)}<br>Versión: {_text(getattr(item, "budget_version_label", None) or item.budget_version_id)}</td>"
        f"<td>Gasto: {_text(item.account_code)}<br>Pasivo tarjeta: {_text(item.liability_code)}</td>"
        f'<td>{_stage_label(item.accounting_status)}<br>{_text(", ".join(map(str, item.recognized_poliza_ids)) if item.recognized_poliza_ids else None)}</td></tr>'
    )


@router.get(BASE + "/{import_id}/directo")
async def direct_statement(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Read every original row; GET never calls SAT or confirms a cut."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    finance = _is_finance(actor)
    view = await _call(
        context.list_direct_statement(
            session, actor_id=_actor_id(actor), import_id=import_id
        )
    )
    if view.statement is None:
        raise HTTPException(404, "Estado no disponible.")
    classification_allowed = True
    try:
        options = await _call(
            context.list_direct_classification_options(
                session, actor_id=_actor_id(actor), import_id=import_id
            )
        )
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        classification_allowed = False
        options = {}

    context_allowed = False
    fiscal_options = {}
    if finance:
        try:
            fiscal_options = await _call(
                context.list_direct_context_options(
                    session, actor_id=_actor_id(actor), import_id=import_id
                )
            )
            context_allowed = True
        except HTTPException as exc:
            if exc.status_code != 403:
                raise

    preview = await _call(
        accounting.preview_direct_accounting(
            session, actor_id=_actor_id(actor), import_id=import_id
        )
    )
    statement, ctx = view.statement, view.context
    body = (
        f'<p><a href="{BASE}/directo">Estados directos</a> · <a href="{QUEUE}">Control Presupuestal AMEX directo</a></p>'
        f"<p>{_text(statement.filename)} · {_text(statement.period_start)} a {_text(statement.period_end)}</p>"
        f'<p>Entidad: {_text(getattr(ctx, "company_code", None))} · Moneda: {_text(getattr(ctx, "currency", None))} · '
        f'Perfil: {escape(_profile_label(getattr(ctx, "profile", None)))}</p>'
        f'<p role="status">{escape(request.query_params.get("msg", ""))}</p>'
        "<p>Conciliación fiscal, clasificación, partida, póliza y pago son hechos separados. "
        "Todos los cargos, créditos y filas pendientes permanecen visibles.</p>"
    )
    card = view.card
    body += f'<p>Tarjeta declarada: {_text(getattr(card, "card_label", None))} · ••••{_text(getattr(card, "last4", None))}</p>'
    frozen = view.cut is not None or preview.cut is not None
    if frozen:
        body += "<p>Fuente congelada: las decisiones se conservan y no se modifican desde esta pantalla.</p><fieldset disabled>"
    if not classification_allowed:
        body += "<p>Tu acceso de lectura se conserva; no tienes autorización para clasificar cargos.</p><fieldset disabled>"
    versions = {str(item.id): item.source_version for item in view.items}
    body += _open(request, import_id, "clasificar", _source_version(view)) + _hidden(
        "expected_versions", json.dumps(versions)
    )
    body += '<div class="direct-table table-shell"><table><thead><tr><th>Fila</th><th>Fecha/comercio</th><th>Original/signo</th><th>Importe económico</th><th>CFDI/SAT</th><th>Clasificación</th><th>Presupuesto</th><th>Cuentas</th><th>Contabilidad</th></tr></thead><tbody>'
    body += "".join(_row(item) for item in view.items) + "</tbody></table></div>"
    tournament_opts = "".join(
        f'<option value="{entry["id"]}">{_text(entry["name"])}</option>'
        for entry in options.get("tournaments", [])
    )
    project_opts = "".join(
        f'<option value="{entry["code"]}">{_text(entry["name"])}</option>'
        for entry in options.get("projects", [])
    )
    concept_opts = "".join(
        f'<option value="{entry["id"]}">{_text(entry["name"])}</option>'
        for entry in options.get("concepts", [])
    )
    body += f'<label>Torneo<select name="tournament_id"><option value="">Selecciona torneo</option>{tournament_opts}</select></label>'
    body += f'<label>Proyecto no torneo<select name="project_code"><option value="">Selecciona proyecto autorizado</option>{project_opts}</select></label>'
    body += f'<label>Categoría<select name="budget_concept_id" required><option value="">Selecciona categoría activa</option>{concept_opts}</select></label>'
    if classification_allowed and (
        not options.get("concepts") or not options.get("edition_years")
    ):
        body += "<p>Contabilidad debe revisar el catálogo de categorías, proyecto y edición autorizados antes de clasificar.</p>"
    edition_options = "".join(
        f'<option value="{year}">{year}</option>'
        for year in options.get("edition_years", [])
    )
    body += f'<label>Edición presupuestal<select name="edition_year" required><option value="">Selecciona edición autorizada</option>{edition_options}</select></label><label>Motivo<input name="reason"></label>'
    body += _submit("Guardar clasificación de cargos seleccionados")
    body += (
        f'<button class="button secondary" formaction="{BASE}/{import_id}/directo/enviar-presupuesto" formnovalidate>Enviar seleccionados a Control Presupuestal</button>'
        + _confirm("Confirmo enviar los cargos seleccionados a Control Presupuestal")
        + "</form>"
    )
    if not classification_allowed:
        body += "</fieldset>"
    if not finance:
        body += "<p>Finanzas atiende contexto fiscal, conciliación, vigencia y confirmación contable.</p><fieldset disabled>"
    if finance and not context_allowed:
        body += "<p>No tienes autorización para corregir el contexto fiscal.</p><fieldset disabled>"
    body += _context_form(
        request,
        import_id,
        _source_version(view),
        fiscal_options.get("contexts", []),
        getattr(ctx, "profile", None) or (view.items[0].profile if view.items else ""),
    )
    if finance and not context_allowed:
        body += "</fieldset>"
    body += (
        _open(request, import_id, "conciliar", _source_version(view))
        + _submit("Conciliar con evidencia vigente")
        + "</form>"
    )
    progress = view.progress or {}
    body += f'<p>Vigencia SAT observada: {_text(progress.get("observed"))}/{_text(progress.get("total"))}. Máximo 50 consultas y 30 segundos por petición; cursor derivado del servidor.</p>'
    body += _open(request, import_id, "vigencia", _source_version(view)) + _hidden(
        "request_key", progress.get("request_key") or uuid4()
    )
    body += (
        _confirm("Autorizo consultar vigencia del siguiente tramo y conciliar")
        + _submit("Consultar o continuar vigencia SAT")
        + "</form>"
    )
    body += _exception_forms(request, import_id, view)
    body += "<section><h2>Confirmar póliza completa</h2>"
    if preview.cut is not None:
        body += f"<p>Corte congelado: {_text(preview.cut.accounting_date)}. La descarga conserva su evidencia.</p>"
        if finance:
            body += " · ".join(
                f'<a href="{BASE}/{import_id}/directo/exportar.{fmt}">{fmt.upper()}</a>'
                for fmt in ("xlsx", "csv", "zip")
            )
    elif preview.status == "ready" and finance:
        body += (
            _open(request, import_id, "confirmar", _source_version(preview))
            + _hidden("expected_source_key", preview.source_key)
            + _hidden("idempotency_key", uuid4())
        )
        body += f'<label>Fecha contable declarada<input type="date" name="accounting_date" min="{statement.period_start}" max="{statement.period_end}" required></label>'
        body += (
            '<label>Motivo<input name="reason"></label>'
            + _confirm("Confirmo el estado completo y la fecha contable")
            + _submit("Generar póliza directa completa")
            + "</form>"
        )
    elif preview.status == "ready":
        body += "<p>La fuente está preparada. Finanzas debe confirmar el corte completo y la fecha contable.</p>"
    else:
        body += "<p>" + escape(_reason(preview.reason)) + "</p>"
    body += "</section>"
    if finance:
        body += f'<p><a href="{BASE}/{import_id}/directo/original">Descargar archivo original privado</a></p>'
    body += '<p><a class="button secondary" href="/admin/gastos/amex/conciliacion">Programar pago de AMEX</a> · Programar no registra pago. Este recorrido no agrega un bloqueo de pago por CFDI.</p>'
    return _html("Estado AMEX directo", body, actor)


def _context_inputs(contexts: list[dict]) -> str:
    companies = "".join(
        f'<option value="{escape(str(entry["company_code"]), quote=True)}">{_text(entry.get("label") or entry["company_code"])}</option>'
        for entry in contexts
    )
    rfcs = "".join(
        f'<option value="{escape(str(entry["receiver_rfc"]), quote=True)}">{_text(entry["receiver_rfc"])}</option>'
        for entry in contexts
    )
    return (
        f'<label>Entidad contable autorizada<select name="company_code" required><option value="">Selecciona entidad</option>{companies}</select></label>'
        f'<label>RFC receptor autorizado<select name="receiver_rfc" required><option value="">Selecciona RFC</option>{rfcs}</select></label>'
    )


def _context_form(
    request: Request, import_id: UUID, version: int, contexts: list[dict], profile: str
) -> str:
    return (
        _open(request, import_id, "contexto", version)
        + "<h2>Declarar o corregir contexto fiscal</h2>"
        + _context_inputs(contexts)
        + '<label>Moneda declarada<select name="currency" required><option value="">Selecciona moneda</option><option value="MXN">MXN</option></select></label>'
        + f"<p>Perfil original conservado: {escape(_profile_label(profile))}. El signo del archivo no se reescribe al corregir el contexto.</p>"
        + _hidden("profile", profile)
        + '<label>Motivo de esta revisión<input name="reason" required></label><label><input type="checkbox" name="sat_consented" value="true">Consultar vigencia y conciliar automáticamente</label>'
        + _hidden("request_key", uuid4())
        + _confirm()
        + _submit("Guardar revisión de contexto")
        + "</form>"
    )


def _exception_forms(request: Request, import_id: UUID, view) -> str:
    body = _open(request, import_id, "alias", _source_version(view))
    body += '<h2>Identidad de comercio y excepciones</h2><label>Descriptor exacto<input name="descriptor" required></label><label>RFC emisor<input name="issuer_rfc" required></label><label>Motivo<input name="reason" required></label>'
    body += (
        _confirm("Confirmo este alias exacto en el ámbito de entidad y tarjeta")
        + _submit("Confirmar alias de comercio")
        + "</form>"
    )
    items = "".join(
        f'<option value="{item.id}">Fila {item.row_number} · {_text(item.descriptor)}</option>'
        for item in view.items
        if item.movement_kind == "debit"
    )
    body += _open(request, import_id, "vincular", _source_version(view))
    body += f'<label>Cargo<select name="item_id" required><option value="">Selecciona cargo</option>{items}</select></label><label>UUID de registro CFDI<input name="cfdi_id" required></label>'
    body += '<label>Importe aplicado declarado (opcional)<input name="applied_amount" inputmode="decimal"></label><label>Propina declarada (opcional)<input name="tip_amount" inputmode="decimal"></label><label><input type="checkbox" name="shared_confirmed" value="true">Declaro factura compartida</label><label>Motivo/evidencia<input name="reason" required></label>'
    body += (
        _confirm("Confirmo el vínculo fiscal y sus importes explícitos")
        + _submit("Declarar vínculo fiscal manual")
        + "</form>"
    )
    credits = "".join(
        f'<option value="{item.id}">Fila {item.row_number} · {_text(item.raw_signed_amount)}</option>'
        for item in view.items
        if item.movement_kind == "credit"
    )
    if credits:
        body += _open(request, import_id, "excluir-credito", _source_version(view))
        body += f'<label>Crédito<select name="item_id" required><option value="">Selecciona crédito</option>{credits}</select></label><label>Motivo<input name="reason" required></label>'
        body += (
            _confirm(
                "Declaro que este crédito no es un nuevo gasto; no registro un pago"
            )
            + _submit("Declarar exclusión del crédito")
            + "</form>"
        )
    return body


@router.post(BASE + "/{import_id}/directo/{action}")
async def direct_action(
    import_id: UUID,
    action: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Dispatch explicit mutation payloads to the canonical direct owners."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    if action not in {"clasificar", "enviar-presupuesto"}:
        _require_finance_actor(actor)
    form = await _form(request)
    version = _version(form)
    common = {"actor_id": _actor_id(actor), "import_id": import_id}
    context_request_key = (
        _value(form, "request_key", required=True)
        if action == "contexto" and _confirmed(form, "sat_consented")
        else ""
    )
    try:
        if action == "vigencia":
            if not _confirmed(form):
                raise HTTPException(422, "Confirma el consentimiento de consulta SAT.")
            await session.rollback()  # Release dependency/auth reads before external SOAP.
            result = await _consult(
                _actor_id(actor),
                import_id,
                version,
                _value(form, "request_key", required=True),
            )
            return _redirect(
                import_id,
                (
                    _reason(result.reason)
                    if result.status == "pending"
                    else "Consulta observada. Revisa los resultados y la continuación pendiente."
                ),
            )
        if action == "contexto":
            result = await _call(
                context.set_direct_context(
                    session,
                    **common,
                    company_code=_value(form, "company_code", required=True),
                    receiver_rfc=_value(form, "receiver_rfc", required=True),
                    currency=_value(form, "currency", required=True),
                    profile=_value(form, "profile", required=True),
                    expected_source_version=version,
                    reason=_value(form, "reason", required=True),
                    confirmed=_confirmed(form),
                )
            )
        elif action == "conciliar":
            result = await _call(
                matching.reconcile_direct_statement(
                    session, **common, expected_source_version=version
                )
            )
        elif action == "alias":
            result = await _call(
                matching.confirm_merchant_alias(
                    session,
                    **common,
                    descriptor=_value(form, "descriptor", required=True),
                    issuer_rfc=_value(form, "issuer_rfc", required=True),
                    reason=_value(form, "reason", required=True),
                    confirmed=_confirmed(form),
                )
            )
        elif action in {"clasificar", "enviar-presupuesto"}:
            ids, versions = _selection(form)
            if action == "clasificar":
                result = await _call(
                    budget.classify_direct_items(
                        session,
                        **common,
                        item_ids=ids,
                        tournament_id=_optional_uuid(form, "tournament_id"),
                        project_code=_value(form, "project_code") or None,
                        budget_concept_id=_uuid(form, "budget_concept_id"),
                        edition_year=int(_value(form, "edition_year", required=True)),
                        expected_versions=versions,
                        reason=_value(form, "reason"),
                    )
                )
            else:
                result = await _call(
                    budget.send_direct_items_to_budget(
                        session,
                        **common,
                        item_ids=ids,
                        expected_versions=versions,
                        confirmed=_confirmed(form),
                    )
                )
        elif action == "vincular":
            result = await _call(
                matching.link_direct_cfdi_manual(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    item_id=_uuid(form, "item_id"),
                    cfdi_id=_uuid(form, "cfdi_id"),
                    expected_source_version=version,
                    reason=_value(form, "reason", required=True),
                    confirmed=_confirmed(form),
                    shared_confirmed=_confirmed(form, "shared_confirmed"),
                    applied_amount=(
                        Decimal(_value(form, "applied_amount"))
                        if _value(form, "applied_amount")
                        else None
                    ),
                    tip_amount=(
                        Decimal(_value(form, "tip_amount"))
                        if _value(form, "tip_amount")
                        else None
                    ),
                )
            )
        elif action == "excluir-credito":
            result = await _call(
                matching.exclude_direct_credit(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    item_id=_uuid(form, "item_id"),
                    expected_source_version=version,
                    reason=_value(form, "reason", required=True),
                    confirmed=_confirmed(form),
                )
            )
        elif action == "confirmar":
            result = await _call(
                accounting.confirm_direct_accounting(
                    session,
                    **common,
                    expected_source_key=_value(
                        form, "expected_source_key", required=True
                    ),
                    expected_source_version=version,
                    accounting_date=date.fromisoformat(
                        _value(form, "accounting_date", required=True)
                    ),
                    confirmed=_confirmed(form),
                    reason=_value(form, "reason"),
                    idempotency_key=_value(form, "idempotency_key", required=True),
                )
            )
        else:
            raise HTTPException(404, "Acción no disponible.")
        if result.status == "pending":
            await session.rollback()
            return _redirect(import_id, _reason(result.reason))
        await session.commit()
    except (ValueError, ArithmeticError):
        await session.rollback()
        raise HTTPException(
            422, "Los datos no son válidos. Revisa selecciones, fecha e importes."
        ) from None
    except Exception:
        await session.rollback()
        raise
    if action == "contexto" and _confirmed(form, "sat_consented"):
        await _consult(
            _actor_id(actor),
            import_id,
            _source_version(result),
            context_request_key,
        )
    messages = {
        "clasificar": "Clasificación guardada; no acredita asignación ni póliza.",
        "enviar-presupuesto": "Cargos enviados a Control Presupuestal; todavía requieren partida.",
        "confirmar": "Corte completo conservado. Revisa la póliza congelada y sus descargas.",
    }
    return _redirect(
        import_id,
        messages.get(
            action, "Acción conservada. Revisa la evidencia y los pendientes actuales."
        ),
    )


@router.get(BASE + "/{import_id}/directo/original")
async def direct_original(
    import_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    """Download source bytes only; query parameters cannot switch export owners."""
    return await _direct_download(import_id, None, session, actor)


@router.get(BASE + "/{import_id}/directo/exportar.{export_format}")
async def direct_export(
    import_id: UUID,
    export_format: str,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    """Download a frozen cut through its explicit export path."""
    return await _direct_download(import_id, export_format, session, actor)


async def _direct_download(
    import_id: UUID, export_format: str | None, session: AsyncSession, actor: Empleado
):
    """Only authorized original bytes or complete frozen export bytes."""
    _require_finance_actor(actor)
    if export_format is not None and export_format not in {"xlsx", "csv", "zip"}:
        raise HTTPException(404, "Formato no disponible.")
    try:
        if export_format is None:
            content, filename, mime = await _call(
                intake.download_direct_source(
                    session, actor_id=_actor_id(actor), import_id=import_id
                )
            )
        else:
            content, filename, mime = await _call(
                accounting.export_direct_cut(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    format=export_format,
                )
            )
    except ValueError:
        raise HTTPException(
            409,
            "No hay una fuente íntegra o un corte completo disponible para esta descarga.",
        ) from None
    # Strip path/header separators even if a future service changes its filename policy.
    filename = (
        str(filename)
        .replace("\\", "/")
        .rsplit("/", 1)[-1]
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": (
                "attachment; filename=\"amex-download\"; filename*=UTF-8''"
                + quote(filename, safe="")
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(QUEUE)
async def direct_budget_queue(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Only the budget owner resolves authorized project-scoped queue rows."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    rows = await _call(
        budget.list_direct_budget_queue(session, actor_id=_actor_id(actor))
    )
    body = "<p>Contabilidad declara proyecto y categoría. Aquí sólo se asigna la partida o se devuelve con motivo; el plan no se edita.</p>"
    for item in rows:
        lines = await _call(
            budget.list_direct_budget_lines(
                session, actor_id=_actor_id(actor), item_id=item.id
            )
        )
        options = "".join(
            f'<option value="{entry["id"]}">{_text(entry["label"])}</option>'
            for entry in lines
        )
        if not lines:
            body += "<p>No hay partidas compatibles en este contexto. Devuelve el cargo a Contabilidad con el motivo de revisión.</p>"
        body += f"<section><h2>Fila {item.row_number} · {_text(item.descriptor)}</h2><p>{_text(item.amount)} {_text(item.currency)} · Proyecto: {_text(getattr(item, "tournament_name", None) or getattr(item, "project_name", None) or item.tournament_id or item.project_code)} · Categoría: {_text(getattr(item, "budget_concept_name", None) or item.budget_concept_id)}</p>"
        hidden = _csrf(request) + _hidden(
            "expected_source_version", item.source_version
        )
        body += f'<form class="direct-form" method="POST" action="{QUEUE}/{item.id}/asignar">{hidden}'
        body += _hidden("expected_source_hash", item.source_hash) + _hidden(
            "idempotency_key", uuid4()
        )
        body += f'<label>Partida y versión presupuestal<select name="budget_line_id" required><option value="">Selecciona partida</option>{options}</select></label>'
        body += _submit("Asignar partida existente") + "</form>"
        body += (
            f'<form class="direct-form" method="POST" action="{QUEUE}/{item.id}/rechazar">{hidden}<label>Motivo de devolución<input name="reason" required></label>'
            + _submit("Devolver a Contabilidad")
            + "</form></section>"
        )
    if not rows:
        body += "<p>No hay cargos pendientes dentro de tu ámbito autorizado.</p>"
    body += f'<p role="status">{escape(request.query_params.get("msg", ""))}</p>'
    return _html("Control Presupuestal AMEX directo", body, actor)


@router.post(QUEUE + "/{item_id}/{action}")
async def direct_budget_action(
    item_id: UUID,
    action: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """CP accepts line identity only, never project/category/account rewrites."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    form = await _form(request)
    allowed = {"_csrf_token", "expected_source_version"}
    allowed |= (
        {"budget_line_id", "expected_source_hash", "idempotency_key"}
        if action == "asignar"
        else {"reason"}
    )
    if set(form) - allowed:
        raise HTTPException(
            422,
            "Control Presupuestal sólo puede seleccionar la partida o devolver con motivo.",
        )
    try:
        if action == "asignar":
            result = await _call(
                budget.assign_direct_budget_line(
                    session,
                    actor_id=_actor_id(actor),
                    item_id=item_id,
                    budget_line_id=_uuid(form, "budget_line_id"),
                    expected_source_version=_version(form),
                    expected_source_hash=_value(
                        form, "expected_source_hash", required=True
                    ),
                    idempotency_key=_value(form, "idempotency_key", required=True),
                )
            )
        elif action == "rechazar":
            result = await _call(
                budget.reject_direct_budget_item(
                    session,
                    actor_id=_actor_id(actor),
                    item_id=item_id,
                    expected_source_version=_version(form),
                    reason=_value(form, "reason", required=True),
                )
            )
        else:
            raise HTTPException(404, "Acción no disponible.")
        if result.status == "pending":
            await session.rollback()
            message = _reason(result.reason)
        else:
            await session.commit()
            message = (
                "Decisión presupuestal conservada; no registra una póliza ni un pago."
            )
    except Exception:
        await session.rollback()
        raise
    return RedirectResponse(QUEUE + "?" + urlencode({"msg": message}), 303)
