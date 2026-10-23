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
from ..services import amex_direct_account_treatment_service as treatments
from ..services import amex_direct_accounting_service as accounting
from ..services import amex_direct_budget_service as budget
from ..services import amex_direct_cfdi_search_service as finder
from ..services import amex_direct_context_service as context
from ..services import amex_direct_intake_service as intake
from ..services import amex_direct_matching_service as matching
from ..services import amex_direct_no_cfdi_service as no_cfdi
from ..services import amex_direct_recovery_service as recovery
from ..services import amex_direct_status_service as status
from ..services import amex_direct_suggestion_service as suggestions
from ..services import amex_expense_account_settlement_service as settlements
from ..services.amex_direct_common import DirectSchemaUnavailable
from . import dependencies
from .admin_amex_accounting_routes import (
    FINANCE_AMEX_ROLES,
    _page,
    _require_finance_actor,
)
from .amex_workspace_ui import (
    dialog_source,
    row_bucket,
    workspace_assets,
    workspace_filters,
    workspace_steps,
    workspace_summary,
    workspace_toolbar,
    workspace_view,
)
from .dependencies import get_current_empleado, get_db_session, require_admin_finanzas

router = APIRouter()
BASE = "/admin/contabilidad/amex/estados"
QUEUE = "/documentos/control-presupuestal/amex-directo"
_CSRF_KEY = "amex_direct_csrf"
_MAX_BYTES = 10 * 1024 * 1024
_TREATMENT_LABELS = {
    "benefit_expense": "Prestación absorbida por la empresa",
    "partner_receivable": "Cargo a la cuenta deudora del socio",
    "project_expense": "Gasto de proyecto / torneo",
}


def _treatment_kind(item) -> str:
    return getattr(item, "treatment_kind", None) or "project_expense"


def _treatment_form(
    request, import_id, version, versions, options, initial=None
) -> str:
    initial = initial or {}
    if initial.get("action") == "revoke":
        body = _open(request, import_id, "tratamiento/preview", version)
        body += _hidden("expected_versions", json.dumps(versions))
        for value in initial.get("item_ids", []):
            body += _hidden("item_ids", value)
        for key in ("treatment", "debtor_account_id"):
            body += _hidden(key, initial.get(key) or "")
        body += _hidden("decision_action", "revoke")
        body += '<label>Motivo del retiro<input name="reason" required maxlength="1000" value="'
        body += escape(str(initial.get("reason", "")), quote=True) + '"></label>'
        return body + _submit("Revisar retiro de tratamiento") + "</form>"
    partner = initial.get("treatment") == "partner_receivable"
    copied = "".join(
        _hidden("item_ids", value) for value in initial.get("item_ids", [])
    )
    accounts = "".join(
        f'<option value="{escape(str(row["id"]), quote=True)}"'
        + (
            " selected"
            if str(row["id"]) == str(initial.get("debtor_account_id"))
            else ""
        )
        + ">"
        f'{_text(row.get("label") or (str(row.get("code", "")) + " · " + str(row.get("name", ""))))}</option>'
        for row in options.get("partner_accounts", [])
    )
    choices = "".join(
        f'<option value="{key}"'
        + (" selected" if key == initial.get("treatment") else "")
        + f">{label}</option>"
        for key, label in _TREATMENT_LABELS.items()
    )
    return (
        '<section aria-labelledby="direct-treatment-heading">'
        '<h2 id="direct-treatment-heading">Tratamiento de los cargos seleccionados</h2>'
        "<p>Prestaciones: Juan Pablo asigna la partida, sin proyecto ni categoría previos. "
        "Cargos al socio: elige su cuenta deudora, sin envío a presupuesto. "
        "Los gastos de proyecto conservan su clasificación.</p>"
        + _open(request, import_id, "tratamiento/preview", version)
        + _hidden("expected_versions", json.dumps(versions))
        + '<div class="direct-copy-selection">'
        + copied
        + "</div>"
        + '<label>Tratamiento<select name="treatment" id="direct-treatment" required>'
        + '<option value="">Selecciona el tratamiento real</option>'
        + choices
        + '</select></label><label id="direct-debtor-label"'
        + ("" if partner else " hidden")
        + '>Cuenta deudora del socio<select name="debtor_account_id" id="direct-debtor"'
        + (" required>" if partner else " disabled>")
        + '<option value="">Selecciona una cuenta autorizada</option>'
        + accounts
        + '</select></label><label>Motivo<input name="reason" required maxlength="1000" value="'
        + escape(str(initial.get("reason", "")), quote=True)
        + '"></label>'
        + _submit("Revisar tratamiento de los cargos seleccionados")
        + "</form><p>La revisión muestra los cargos antes de confirmar; no genera la póliza.</p></section>"
    )


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
        "account_treatment_policy_unconfigured": "Finanzas debe habilitar el catálogo autorizado de tratamientos, partidas y cuentas antes de continuar.",
        "benefit_budget_catalog_unconfigured": "Las prestaciones requieren un catálogo de partidas autorizado. Finanzas debe configurarlo; no hace falta capturar un proyecto ficticio.",
        "account_treatment_head_conflict": "Cambió el tratamiento del cargo. Revisa la decisión vigente y vuelve a previsualizar.",
        "account_treatment_source_changed": "Cambió la fuente del tratamiento. Revisa los cargos y sus versiones actuales.",
        "account_treatment_policy_changed": "Cambió el catálogo autorizado de partidas o cuentas. Revisa las opciones actuales antes de confirmar.",
        "account_treatment_account_changed": "Cambió la cuenta elegida o su estado. Vuelve a revisar la cuenta del cargo.",
        "account_treatment_operational_project_conflict": "Este tratamiento no lleva proyecto operativo. Revisa la clasificación existente antes de cambiarlo.",
        "account_treatment_reason_required": "Explica el motivo del tratamiento de los cargos seleccionados.",
        "account_treatment_new_debit_required": "El tratamiento sólo aplica a cargos nuevos pendientes; revisa abonos o movimientos ya reconocidos.",
        "account_treatment_transition_conflict": "La selección tiene un tratamiento incompatible. Revisa los cargos; no se modificó el lote.",
        "account_treatment_economics_changed": "Cambió el importe o la identidad del cargo. Revisa el estado antes de confirmar.",
        "account_treatment_homogeneous_batch_required": "Selecciona un lote de cargos con tratamiento compatible o revísalos en grupos separados.",
        "account_treatment_confirmation_required": "Confirma explícitamente el tratamiento de todos los cargos revisados.",
        "account_treatment_request_key_required": "La confirmación perdió su identidad de solicitud. Previsualiza el lote otra vez.",
        "account_treatment_idempotency_conflict": "La solicitud ya se usó con otros datos. Revisa el resultado y previsualiza de nuevo.",
        "account_treatment_preview_changed": "La revisión cambió. Conservamos tus datos para que previsualices otra vez antes de confirmar.",
        "invalid_account_treatment": "Selecciona un tratamiento contable válido.",
        "invalid_amex_partner_decision": "El cargo al socio requiere una cuenta deudora activa y un motivo explícito.",
        "partner_budget_conflict": "El cargo al socio no debe conservar una imputación presupuestal. Solicita su devolución o liberación antes de cambiar el tratamiento.",
        "all_sources_already_recognized": "Todos los movimientos están cubiertos o declarados como no nuevo gasto. Se conservan sus pólizas; no se genera una póliza de importe cero.",
        "statement_row_unresolved": "Hay filas omitidas o signos sin resolver. Finanzas debe revisar el estado completo antes de generar la póliza.",
        "credit_exclusion_required": "Finanzas debe declarar cada crédito conocido como no nuevo gasto; no se registra un pago.",
        "budget_assignment_required": "Control Presupuestal debe asignar la partida de las prestaciones y los gastos de proyecto antes del corte completo.",
        "budget_assignment_changed": "Cambió la asignación presupuestal. Control Presupuestal debe revisar la partida y versión actuales.",
        "budget_version_not_operational": "La versión presupuestal no está habilitada para estos movimientos. Control Presupuestal debe revisar su estado.",
        "stale_item_context": "La fila pertenece a una revisión anterior de contexto. Contabilidad debe revisar la fuente actual.",
        "fiscal_identity_changed": "Cambió la identidad fiscal de la factura. Finanzas debe revisar el vínculo antes de confirmar.",
        "fiscal_source_changed": "Cambió la fuente fiscal del cargo. Finanzas debe conciliar su evidencia actual.",
        "fresh_vigente_receipt_required": "Finanzas debe consultar vigencia actual del XML antes de confirmar el corte.",
        "merchant_identity_changed": "Cambió la identidad de comercio confirmada. Finanzas debe revisar y conciliar el cargo de nuevo.",
        "no_cfdi_decision_head_conflict": "La evidencia de la decisión Sin CFDI no coincide con el cargo. Recarga el estado y revisa su historial.",
        "no_cfdi_context_conflict": "El contexto fiscal cambió. Revisa la declaración Sin CFDI antes de continuar.",
        "no_cfdi_new_debit_required": "Sin CFDI sólo admite cargos débito nuevos con fuente verificable. Revisa la selección completa.",
        "no_cfdi_amount_currency_conflict": "El importe o moneda del cargo no admite este tratamiento. No se aplicó el lote.",
        "no_cfdi_fiscal_identity_conflict": "Hay evidencia fiscal vinculada al cargo. Revisa su factura; no se elimina para marcar Sin CFDI.",
        "no_cfdi_manual_tax_conflict": "Hay impuestos o propina declarados. Revisa la evidencia antes de cambiar el tratamiento.",
        "no_cfdi_source_changed": "Cambió la fuente económica de la declaración Sin CFDI. Revisa su evidencia actual.",
        "no_cfdi_request_key_required": "Falta una clave válida de petición. Recarga el estado antes de confirmar.",
        "no_cfdi_selection_required": "Selecciona cargos explícitos para el lote Sin CFDI.",
        "no_cfdi_idempotency_conflict": "La petición ya se usó con otros datos. Recarga el estado y revisa su historial antes de repetirla.",
        "no_cfdi_transition_conflict": "La selección contiene un tratamiento Sin CFDI incompatible con esta acción. Revisa todas las filas; no se aplicó el lote.",
        "no_cfdi_historical_source_conflict": "El cargo tiene cobertura histórica o reconocimiento previo. Se conserva su evidencia sin declarar un gasto nuevo.",
        "no_cfdi_confirmation_required": "Confirma explícitamente el tratamiento Sin CFDI para los cargos seleccionados.",
        "no_cfdi_reason_required": "Explica el motivo de la declaración o revocación Sin CFDI (máximo 1000 caracteres).",
        "no_cfdi_revoke_required": "Primero revoca explícitamente Sin CFDI con motivo antes de cambiar el vínculo o contexto fiscal.",
        "no_cfdi_budget_release_required": "Control Presupuestal debe devolver o liberar los cargos antes de cambiar su tratamiento Sin CFDI.",
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
        "budget_line_context_mismatch": "La partida o versión no corresponde al tratamiento y ámbito autorizados. Control Presupuestal debe revisar la selección.",
        "budget_account_mapping_required": "Contabilidad debe completar la cuenta activa del catálogo de la partida presupuestal.",
        "invalid_amex_liability": "Finanzas debe revisar la cuenta pasivo activa de esta tarjeta; no se sustituye por una cuenta genérica.",
        "account_mapping_conflict": "La cuenta existente contradice la del tratamiento actual. Contabilidad debe revisar el conflicto; no se sobrescribe.",
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
        "statement_period_mismatch": (
            "El CSV contiene fechas fuera del "
            "período declarado. Revisa las "
            "fechas mostradas y corrige el "
            "período antes de cargar."
        ),
        ("recovery_period_must_contain_origina" "l"): (
            "El período corregido debe conservar "
            "el original y cubrir todas las "
            "fechas del archivo."
        ),
        "recovery_reason_required": "Explica el motivo de la corrección de cobertura.",
        "invalid_cfdi_search": (
            "Revisa los filtros de fecha, " "importe y texto de la búsqueda."
        ),
        "invalid_cfdi_search_cursor": (
            "La búsqueda cambió o quedó "
            "desactualizada. Inicia una búsqueda "
            "nueva."
        ),
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
        "sign_corrected_pending_cfdi": "Signo corregido. Revisa el CFDI del cargo o declara Sin CFDI si corresponde.",
        "sign_corrected_credit": "Abono confirmado mediante corrección auditada de signos.",
        "sign_correction_period_recovery_unsupported": "Este estado tiene una corrección de signos. Soporte debe revisar cualquier cambio de período por separado.",
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
        "prior_bank_requires_explicit_selection": (
            "Selecciona la póliza de origen para asociar este cargo "
            "a su reconocimiento anterior."
        ),
        "historical_cfdi_xml_required": (
            "Falta el XML de la factura original de la cuenta de gastos. "
            "Revisa su comprobación antes de seleccionar el crédito."
        ),
        "historical_cfdi_identity_conflict": (
            "La factura original y su XML se contradicen. "
            "Revisa la comprobación de origen."
        ),
        "expense_account_original_fiscal_identity_changed": (
            "Cambió la factura original del crédito seleccionado. "
            "Recarga y revisa la evidencia de origen."
        ),
        "expense_account_selection_stale": (
            "Cambió el cargo desde la selección de origen. Revisa y "
            "selecciona de nuevo la cuenta de gastos."
        ),
        "expense_account_origin_changed": (
            "Cambió la evidencia o el saldo del asiento original. Revisa "
            "el origen antes de confirmar."
        ),
        "expense_account_credit_not_proven": (
            "No hay un crédito contable original con evidencia suficiente"
            " para cancelar este cargo."
        ),
        "expense_account_source_scope_mismatch": (
            "La cuenta de gastos no pertenece al ámbito fiscal, proyecto "
            "o edición autorizado del estado."
        ),
        "expense_account_residual_not_attributable": (
            "El saldo tiene otros movimientos sin atribución suficiente; "
            "Contabilidad debe revisar el origen."
        ),
        "expense_account_credit_exhausted": (
            "El crédito disponible no alcanza para este cargo o ya fue " "cancelado."
        ),
        "expense_account_already_amex_recognized": (
            "El gasto ya tiene reconocimiento AMEX; corresponde asociarlo"
            " sin generar otro pasivo."
        ),
        "expense_account_selection_confirmation_required": (
            "Confirma el origen y explica por qué corresponde al cargo."
        ),
        "expense_account_origin_identity_mismatch": (
            "El asiento de origen no corresponde a la cuenta y partida "
            "seleccionadas. Recarga las opciones."
        ),
        "expense_account_bank_already_recognized": (
            "Este cargo ya tiene reconocimiento contable. Revisa su " "póliza original."
        ),
        "direct_cfdi_tax_evidence_incomplete": (
            "El XML no contiene evidencia fiscal suficiente. Revisa la "
            "factura; no se estimó IVA ni se sustituyó por cero."
        ),
        "direct_cfdi_tax_evidence_inconsistent": (
            "El desglose fiscal del XML es inconsistente. Revisa la "
            "factura antes de generar la póliza."
        ),
        "direct_completion_request_conflict": (
            "El estado ya se finalizó con otra confirmación. Revisa el "
            "recibo conservado."
        ),
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


def _version(form: FormData, name: str = "expected_source_version") -> int:
    try:
        value = int(_value(form, name, required=True))
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


def _redirect(
    import_id: UUID, message: str, *, error: bool = False
) -> RedirectResponse:
    parameters = {"msg": message}
    if error:
        parameters["error_msg"] = message
    return RedirectResponse(f"{BASE}/{import_id}/directo?{urlencode(parameters)}", 303)


def _html(
    title: str,
    body: str,
    actor: Empleado,
    *,
    kind: str = "detail",
    statement_url: str = "",
    source_version: int | None = None,
    pending_budget: int = 0,
) -> Response:
    styles = """<style>.direct-table{overflow-x:auto;max-width:100%}
    .amex-content .direct-table td{white-space:normal;overflow-wrap:anywhere;
    max-width:24rem;min-width:7rem}.direct-form{max-width:760px}
    .direct-form.direct-classification{max-width:none}
    .direct-classification>label{max-width:760px}
    .direct-form label{display:block;margin:12px 0}.direct-form input:not([type=checkbox]),
    .direct-form select{width:100%;box-sizing:border-box}.direct-actions{display:flex;
    gap:12px;flex-wrap:wrap}.direct-table table{min-width:1200px}</style>"""
    content = workspace_view(
        title,
        body,
        kind=kind,
        statement_url=statement_url,
        source_version=source_version,
        pending_budget=pending_budget,
    )
    page = _page(title, styles + content, actor, workflow="direct")
    page = page.replace("</head>", workspace_assets() + "</head>", 1)
    return Response(
        page,
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
    form_id = ' id="direct-classification-form"' if action == "clasificar" else ""
    return (
        f'<form{form_id} class="{form_class}" method="POST" action="{BASE}/{import_id}/directo/{action}">'
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
    return _html(
        "Cargar estado AMEX directo",
        workspace_steps(intake=True) + body,
        actor,
        kind="intake",
    )


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
        posting_label = "Sin corte directo confirmado"
        if cut is not None:
            posting_label = "Corte congelado: " + _text(cut.accounting_date)
        elif summary.get("completion_receipt"):
            posting_label = "Finalizado: cargos reconocidos previamente"
        rows.append(
            f'<tr><td>{_text(getattr(card, "card_label", None))} · ••••{_text(getattr(card, "last4", None))}</td>'
            f"<td>{_text(statement.filename)}<br>{_text(statement.period_start)} a {_text(statement.period_end)}</td>"
            f'<td>Cargos: {_text(counts.get("debit"))}<br>Créditos: {_text(counts.get("credit"))}<br>Signo desconocido: {_text(counts.get("unknown"))}<br>Omitidas: {_text(counts.get("skipped"))}</td>'
            f'<td>CFDI confirmado: {_text(counts.get("matched"))}</td>'
            f'<td>Clasificados: {_text(counts.get("classified"))}<br>En Control Presupuestal: {_text(counts.get("sent"))}<br>Partida asignada: {_text(counts.get("assigned"))}</td>'
            f"<td>{posting_label}<br>Consumos reconocidos: "
            f'{_text(counts.get("recognized"))}</td>'
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
        evaluation = intake.evaluate_direct_period(
            content,
            profile=profile,
            currency=currency,
            period_start=date.fromisoformat(
                _value(form, "period_start", required=True)
            ),
            period_end=date.fromisoformat(_value(form, "period_end", required=True)),
        )
        rows = evaluation["rows"]
    except ValueError as exc:
        raise HTTPException(422, _reason(str(exc))) from None
    # Decimal and dates are rendered from the parser, never reconstructed from abs.
    rows = [
        {**row, "reason_text": _reason(row["reason"]) if row.get("reason") else ""}
        for row in rows
    ]
    return JSONResponse(
        json.loads(
            json.dumps({**evaluation, "rows": rows, "profile": profile}, default=str)
        ),
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
    session: AsyncSession, actor_id: UUID, import_id: UUID, version: int
) -> tuple[int, str, bool]:
    """Reuse approved fresh fiscal receipts after the original intake is committed."""
    try:
        result = await _call(
            matching.reconcile_direct_statement(
                session,
                actor_id=actor_id,
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
    actor_id = _actor_id(actor)
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
                actor_id=actor_id,
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
            session, actor_id, import_id, version
        )
        if local_message:
            message += " " + local_message
    else:
        message = "Carga existente conservada. Continúa desde su estado guardado."
    if _confirmed(form, "sat_consented") and can_consult:
        # Both local transactions are closed before bounded external SAT chunks.
        observed = await _consult(actor_id, import_id, version, key)
        if observed.status == "pending":
            message += " " + _reason(observed.reason)
    return _redirect(import_id, message)


def _fiscal_evidence_label(candidate: dict) -> str:
    complement = {
        "available": "Disponible",
        "not_local": "No localizado en las facturas descargadas",
        "not_verifiable": "No verificable",
    }.get(str(candidate.get("complement_status") or ""), "No verificable")
    return (
        "<p>Método de pago: "
        + _text(candidate.get("payment_method"))
        + " · Complemento: "
        + escape(complement)
        + (
            ". Su ausencia no bloquea la conciliación ni la póliza.</p>"
            if candidate.get("policy_applies")
            else ". Evidencia informativa; no acredita pago por sí sola.</p>"
        )
    )


def _candidate_differences(candidate: dict, bank_date=None, bank_amount=None) -> str:
    if "amount_delta" not in candidate:
        return _fiscal_evidence_label(candidate)
    return (
        "<p>Fecha del cargo: "
        + _text(bank_date)
        + " · Importe del cargo: "
        + _text(bank_amount)
        + " · Diferencia CFDI − cargo: "
        + _text(candidate.get("amount_delta"))
        + " MXN · Días entre emisión y cargo: "
        + _text(candidate.get("days_delta"))
        + ". Diferencia visible para revisión; no acredita propina.</p>"
        + _fiscal_evidence_label(candidate)
    )


def _suggestion_summary(import_id, row: dict | None) -> str:
    if row is None:
        return ""
    html = (
        '<section aria-label="Facturas sugeridas"><strong>Facturas sugeridas</strong>'
    )
    for candidate in row.get("candidates", []):
        query = urlencode({"item_id": row["item_id"]})
        html += (
            "<p>"
            + _text(candidate.get("issuer_name"))
            + " · MXN "
            + _text(candidate.get("total"))
            + " · "
            + _text(candidate.get("date"))
            + "<br>RFC: "
            + _text(candidate.get("issuer_rfc"))
            + "<br>UUID: "
            + _text(candidate.get("uuid"))
            + _candidate_differences(
                candidate, row.get("bank_date"), row.get("bank_amount")
            )
            + '<a href="'
            + BASE
            + "/"
            + str(import_id)
            + "/directo/cfdi?"
            + escape(query, quote=True)
            + '">Revisar propuestas</a></p>'
        )
    if not row.get("candidates"):
        message = (
            "Sin candidatas en la búsqueda completa. Puedes buscar manualmente."
            if row.get("coverage_complete")
            else (
                "La búsqueda aún no está completa o falta evidencia; "
                "revisa el detalle."
            )
        )
        html += "<p>" + message + "</p>"
    if row.get("has_more"):
        parameters = {"item_id": row["item_id"]}
        if row.get("cursor"):
            parameters["suggest_cursor"] = row["cursor"]
        query = urlencode(parameters)
        html += (
            '<a href="'
            + BASE
            + "/"
            + str(import_id)
            + "/directo/cfdi?"
            + escape(query, quote=True)
            + '">Más candidatas</a>'
        )
    return html + "</section>"


def _no_cfdi_decision(item) -> dict:
    decision = getattr(item, "no_cfdi_decision", None)
    if decision is None:
        return {}
    return decision if isinstance(decision, dict) else vars(decision)


def _no_cfdi_forms(request, import_id, version, versions) -> str:
    body = '<section aria-label="Decisión Sin CFDI"><h2>Cargos seleccionados: Sin CFDI</h2>'
    body += (
        "<p>El importe completo será gasto contra el pasivo de la tarjeta, "
        "sin IVA acreditable o pendiente, retenciones ni propina. No representa "
        "una factura exenta o a tasa cero y no determina deducibilidad. "
        "Continúan clasificación, partida y confirmación del corte completo.</p>"
        "<p>El lote se aplica completo o se rechaza completo. Si los cargos "
        "ya están enviados o asignados, Control Presupuestal debe devolverlos "
        "o liberarlos primero. Un corte final conserva su evidencia.</p>"
    )
    for action, label, confirmation in (
        (
            "sin-cfdi",
            "Marcar Sin CFDI",
            "Confirmo declarar Sin CFDI los cargos seleccionados con este tratamiento",
        ),
        (
            "revocar-sin-cfdi",
            "Volver a buscar factura",
            "Confirmo revocar Sin CFDI en los cargos seleccionados y conservar su historial",
        ),
    ):
        body += _open(request, import_id, action, version)
        body += _hidden("expected_versions", json.dumps(versions))
        body += _hidden("request_key", uuid4())
        body += '<div class="direct-copy-selection"></div>'
        body += '<label>Motivo obligatorio (máximo 1000 caracteres)<input name="reason" required maxlength="1000"></label>'
        body += _confirm(confirmation) + _submit(label) + "</form>"
    return body + "</section>"


def _row(
    item, suggestion_html: str = "", *, bucket: str = "review", disabled: bool = False
) -> str:
    kind = {
        "debit": "Cargo",
        "credit": "Crédito",
        "unknown": "Signo desconocido",
        "skipped": "Fila omitida",
    }.get(item.movement_kind, "Tipo pendiente")
    fiscal = f"{_stage_label(item.match_status)}<br>{_text(getattr(item, 'cfdi_uuid', None) or item.cfdi_id)}"
    decision = _no_cfdi_decision(item)
    if decision.get("action") == "declare":
        fiscal = (
            "<strong>Sin CFDI</strong><br>Motivo: "
            + _text(decision.get("reason"))
            + "<br>Declarado por: "
            + _text(decision.get("actor_id"))
            + "<br>Fecha: "
            + _text(decision.get("created_at"))
            + "<br>Revisión: "
            + _text(decision.get("revision"))
            + "<br>Recibo de decisión: "
            + _text(decision.get("id"))
            + (
                "<br>Importe completo a la cuenta deudora del socio, sin movimientos fiscales."
                if decision.get("treatment") == "full_amount_partner_receivable_no_tax"
                else "<br>Importe completo como gasto, sin movimientos fiscales."
            )
        )
        suggestion_html = ""
    if item.match_reason and decision.get("action") != "declare":
        fiscal += "<br>" + escape(_reason(item.match_reason))
    if decision.get("action") != "declare":
        fiscal += f"<br>Vigencia: {_text(getattr(item, 'sat_status', None))}"
        fiscal += f"<br>Observada: {_text(getattr(item, 'status_observed_at', None))}"
    if decision.get("action") == "revoke":
        fiscal += (
            "<br>Sin CFDI revocado. Motivo: "
            + _text(decision.get("reason"))
            + "<br>Por: "
            + _text(decision.get("actor_id"))
            + " · "
            + _text(decision.get("created_at"))
            + "<br>Recibo de decisión: "
            + _text(decision.get("id"))
        )
    fiscal += suggestion_html
    kind_for_budget = _treatment_kind(item)
    budget_eligible = (
        kind_for_budget in {"benefit_expense", "project_expense"}
        and item.budget_status == "classified"
    )
    select_item = f'<input type="checkbox" name="item_ids" form="direct-classification-form" value="{item.id}" aria-label="Seleccionar fila {item.row_number}" data-budget-eligible="{str(budget_eligible).lower()}">'
    if disabled:
        select_item = select_item.replace(">", " disabled>", 1)
    if item.movement_kind != "debit" or item.accounting_status == "recognized":
        select_item = ""
    treatment = _treatment_kind(item)
    if treatment == "project_expense":
        classification = (
            _text(
                getattr(item, "tournament_name", None)
                or getattr(item, "project_name", None)
                or item.tournament_id
                or item.project_code
            )
            + "<br>Categoría: "
            + _text(
                getattr(item, "budget_concept_name", None) or item.budget_concept_id
            )
        )
    elif treatment in {"benefit_expense", "partner_receivable"}:
        classification = (
            _text(_TREATMENT_LABELS[treatment]) + "<br>Sin proyecto requerido"
        )
    else:
        classification = "Tratamiento por elegir"
    if treatment == "partner_receivable":
        budget_cell = "No aplica: cargo a la cuenta deudora del socio"
        account_label = "Cuenta deudora"
    elif treatment == "unclassified":
        budget_cell = "Elige primero el tratamiento del cargo"
        account_label = "Cuenta"
    else:
        budget_cell = (
            f'{_stage_label(item.budget_status)}<br>Partida: {_text(getattr(item, "budget_line_label", None) or item.budget_line_id)}'
            f'<br>Versión: {_text(getattr(item, "budget_version_label", None) or item.budget_version_id)}'
        )
        account_label = "Gasto"
    return (
        f'<tr data-item-id="{item.id}" data-amex-bucket="{escape(bucket, quote=True)}" '
        f'data-amex-search="{escape(f"{item.fecha} {item.descriptor} {item.amount} {item.currency}", quote=True)}">'
        f"<td>{select_item}{item.row_number}</td>"
        f"<td>{_text(item.fecha)}<br>{_text(item.descriptor)}</td>"
        f"<td>Celda: {_text(item.raw_amount)}<br>Firmado: {_text(item.raw_signed_amount)}<br>{kind}<br>{escape(_profile_label(item.profile))}</td>"
        f"<td>{_text(item.amount)} {_text(item.currency)}</td><td>{fiscal}</td>"
        f"<td>{classification}</td>"
        f"<td>{budget_cell}</td>"
        f"<td>{account_label}: {_text(item.account_code)}<br>Pasivo tarjeta: {_text(item.liability_code)}</td>"
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
    if request.query_params.get("workspace_poll") == "1":
        return JSONResponse(
            {"source_version": _source_version(view)},
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
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

    treatment_options = {}
    treatment_allowed = classification_allowed
    treatment_message = ""
    if classification_allowed:
        try:
            treatment_options = await _call(
                treatments.list_direct_account_treatment_options(
                    session, actor_id=_actor_id(actor), import_id=import_id
                )
            )
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail == _reason(
                "account_treatment_policy_unconfigured"
            ):
                treatment_allowed = False
                treatment_message = str(exc.detail)
            elif exc.status_code == 403:
                treatment_allowed = False
                treatment_message = "Tu acceso actual no permite elegir estos tratamientos. Los controles de clasificación autorizados se conservan."
            else:
                raise

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
    body = workspace_steps(
        finalized=bool(
            view.cut
            or preview.cut
            or (preview.progress or {}).get("completion_receipt")
        )
    ) + (
        f"<p>{_text(statement.filename)} · {_text(statement.period_start)} a {_text(statement.period_end)}</p>"
        f'<p>Entidad: {_text(getattr(ctx, "company_code", None))} · Moneda: {_text(getattr(ctx, "currency", None))} · '
        f'Perfil: {escape(_profile_label(getattr(ctx, "profile", None)))}</p>'
        f'<p id="amex-workspace-status" role="status">{escape(request.query_params.get("msg", ""))}</p>'
        "<p>Resuelve los cargos de este estado y revisa su póliza completa. "
        "El archivo y todos sus movimientos se conservan.</p>"
    )
    coverage = view.progress or {}
    if coverage.get("sign_revision_ids"):
        profiles = ", ".join(
            _profile_label(value) for value in coverage.get("effective_profiles", [])
        )
        body += (
            "<p>Signos corregidos mediante revisión auditada. "
            f"Perfil efectivo: {escape(profiles)}. "
            "El archivo y el perfil originales se conservan. "
            f"Revisión: {_text(coverage['sign_revision_ids'])}</p>"
        )
    effective_start = coverage.get("effective_period_start") or statement.period_start
    effective_end = coverage.get("effective_period_end") or statement.period_end
    if coverage.get("recovery_revision_ids"):
        body += (
            "<p>Declaración original conservada. "
            "Cobertura efectiva corregida: "
            f"{_text(effective_start)}"
            " a "
            f"{_text(effective_end)}"
            " · Revisión auditada: "
            f"{_text(coverage['recovery_revision_ids'])}"
            "</p>"
        )
    card = view.card
    body += f'<p>Tarjeta declarada: {_text(getattr(card, "card_label", None))} · ••••{_text(getattr(card, "last4", None))}</p>'
    if finance and not view.cut:
        body += (
            '<p><a href="'
            f"{BASE}"
            "/"
            f"{import_id}"
            '/directo/periodo">Revisar cobertura '
            "y recuperar cargos omitidos por "
            "período</a></p>"
        )
    completion = (preview.progress or {}).get("completion_receipt")
    frozen = view.cut is not None or preview.cut is not None or bool(completion)
    suggested = {}
    suggestion_page = {}
    suggestion_message = ""
    if finance and not frozen:
        try:
            suggestion_page = await _call(
                suggestions.suggest_direct_statement_cfdis(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    navigation=request.session,
                    cursor=request.query_params.get("suggest_cursor"),
                )
            )
            suggested = {row["item_id"]: row for row in suggestion_page["items"]}
        except HTTPException as exc:
            if exc.status_code not in {403, 409}:
                raise
            suggestion_message = (
                "No se pudieron consultar sugerencias con el contexto o "
                "permiso actual; la lectura del estado se conserva."
            )
    if frozen:
        body += "<p>Fuente congelada: las decisiones se conservan y no se modifican desde esta pantalla.</p>"
    versions = {str(item.id): item.source_version for item in view.items}
    active_no_cfdi = sum(
        _no_cfdi_decision(item).get("action") == "declare" for item in view.items
    )
    pending_invoice = sum(
        item.movement_kind == "debit"
        and _no_cfdi_decision(item).get("action") != "declare"
        and item.match_status != "matched"
        and item.accounting_status != "recognized"
        for item in view.items
    )
    pending_accounting = sum(
        item.movement_kind == "debit" and item.accounting_status != "recognized"
        for item in view.items
    )
    pending_budget = sum(
        item.movement_kind == "debit"
        and item.accounting_status != "recognized"
        and _treatment_kind(item) in {"benefit_expense", "project_expense"}
        and item.budget_status != "assigned"
        for item in view.items
    )
    body += (
        f"<p>Pendientes de factura: {pending_invoice} · Sin CFDI declarados: {active_no_cfdi}. "
        f"Pendientes de presupuesto: {pending_budget} · Pendientes de contabilización: {pending_accounting}.</p>"
    )
    summary, buckets = workspace_summary(view.items, preview)
    body += (
        summary
        + workspace_filters()
        + '<p id="direct-selection-count" role="status">0 cargos seleccionados</p>'
        + workspace_toolbar(
            classification=classification_allowed,
            treatment=treatment_allowed,
            finance=finance,
            frozen=frozen,
        )
    )
    if treatment_allowed and not frozen:
        body += dialog_source(
            "treatment",
            _treatment_form(
                request, import_id, _source_version(view), versions, treatment_options
            ),
        )
    elif treatment_message and not frozen:
        body += '<p role="status">' + escape(treatment_message) + "</p>"
    body += '<section class="direct-classification" data-amex-charges><h2>2. Resolver cargos</h2>'
    body += (
        '<div class="direct-table '
        'table-shell"><table><thead><tr><th><'
        'input type="checkbox" '
        'id="direct-select-all" '
        'aria-label="Seleccionar todos los '
        "cargos "
        'disponibles">Fila</th><th>Fecha/come'
        "rcio</th><th>Original/signo</th><th>"
        "Importe "
        "económico</th><th>CFDI/SAT</th><th>C"
        "lasificación</th><th>Presupuesto</th"
        "><th>Cuentas</th><th>Contabilidad</t"
        "h></tr></thead><tbody>"
    )
    body += (
        "".join(
            _row(
                item,
                _suggestion_summary(import_id, suggested.get(str(item.id))),
                bucket=row_bucket(item, preview),
                disabled=frozen,
            ).replace(
                "</td></tr>",
                (
                    (
                        '<br><a href="'
                        f"{BASE}"
                        "/"
                        f"{import_id}"
                        "/directo/cfdi?item_id="
                        f"{item.id}"
                        '">Buscar CFDI</a> · <a href="'
                        f"{BASE}/{import_id}/directo/cuenta-gastos?item_id={item.id}"
                        '">Cuenta de gastos reconocida</a>'
                        + (
                            f'<br><a href="{BASE}/{import_id}/directo/tratamiento/revocar?item_id={item.id}">Retirar tratamiento actual</a>'
                            if getattr(item, "account_treatment_decision_id", None)
                            and _treatment_kind(item) in _TREATMENT_LABELS
                            else ""
                        )
                        + "</td></tr>"
                    )
                    if item.movement_kind == "debit" and not frozen
                    else "</td></tr>"
                ),
            )
            for item in view.items
        )
        + "</tbody></table></div></section>"
    )
    if suggestion_message:
        body += '<p role="status">' + escape(suggestion_message) + "</p>"
    if suggestion_page.get("has_more"):
        query = urlencode({"suggest_cursor": suggestion_page["next_cursor"]})
        body += (
            '<p><a href="?'
            + escape(query, quote=True)
            + (
                '">Sugerencias de los siguientes 25 cargos</a>. Todos los'
                " movimientos originales permanecen visibles.</p>"
            )
        )
    project_start = len(body)
    if not classification_allowed:
        body += "<p>Tu acceso de lectura se conserva; no tienes autorización para clasificar cargos.</p>"
    body += _open(request, import_id, "clasificar", _source_version(view))
    body += _hidden("expected_versions", json.dumps(versions))
    body += '<details id="direct-project-classification"><summary>Clasificación sólo para gastos de proyecto / torneo</summary>'
    project_opts = "".join(
        (
            '<option value="t:'
            f"{escape(str(entry['id']), quote=True)}"
            '">'
            f"{_text(entry['name'])}"
            "</option>"
        )
        for entry in options.get("tournaments", [])
    ) + "".join(
        (
            '<option value="p:'
            f"{escape(str(entry['code']), quote=True)}"
            '">'
            f"{_text(entry['name'])}"
            "</option>"
        )
        for entry in options.get("projects", [])
    )
    concept_opts = "".join(
        (
            '<option value="'
            f"{entry['id']}"
            '" data-project="'
            f"{_project_option_key(entry)}"
            '">'
            f"{_text(entry['name'])}"
            "</option>"
        )
        for entry in options.get("concepts", [])
    )
    body += (
        "<label>Proyecto / torneo<select "
        'id="direct-project" '
        "required><option "
        'value="">Selecciona proyecto '
        "autorizado</option>"
        f"{project_opts}"
        "</select></label>"
    )
    body += (
        '<input type="hidden" '
        'name="tournament_id" '
        'id="direct-tournament"><input '
        'type="hidden" name="project_code" '
        'id="direct-project-code">'
    )
    body += (
        "<label>Categoría del "
        'catálogo<select id="direct-concept" '
        'name="budget_concept_id" required '
        "disabled><option "
        'value="">Selecciona categoría del '
        "proyecto</option>"
        f"{concept_opts}"
        "</select></label>"
    )
    edition_options = "".join(
        f'<option value="{year}">{year}</option>'
        for year in options.get("edition_years", [])
    )
    body += f'<label>Edición presupuestal<select name="edition_year" required><option value="">Selecciona edición autorizada</option>{edition_options}</select></label><label>Motivo<input name="reason"></label>'
    body += (
        _submit("Guardar clasificación de cargos seleccionados") + "</details></form>"
    )
    body = body[:project_start] + dialog_source(
        "project", body[project_start:], disabled=not classification_allowed or frozen
    )
    budget_start = len(body)
    body += _open(request, import_id, "enviar-presupuesto", _source_version(view))
    body += _hidden("expected_versions", json.dumps(versions))
    body += (
        "<div "
        'id="direct-send-selection"></div><p>'
        "Enviar utiliza únicamente la "
        "clasificación que ya guardaste. Los "
        "cambios sin guardar no se "
        "envían.</p>"
    )
    body += _confirm("Confirmo enviar los cargos seleccionados a Control Presupuestal")
    body += '<p id="direct-budget-selection-count" role="status">0 cargos listos para enviar a Control Presupuestal.</p>'
    body += '<button id="direct-send-budget" class="button primary" type="submit" disabled>Enviar seleccionados a Control Presupuestal</button></form>'
    body = body[:budget_start] + dialog_source(
        "budget", body[budget_start:], disabled=not classification_allowed or frozen
    )
    if finance and not frozen:
        body += dialog_source(
            "no-cfdi",
            _no_cfdi_forms(request, import_id, _source_version(view), versions),
        )
    review_start = len(body)
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
    if not finance:
        body += "</fieldset>"
    body = body[:review_start] + dialog_source(
        "review", body[review_start:], disabled=frozen
    )
    body += '<details id="amex-workspace-blockers"><summary>Bloqueos y siguiente acción</summary><ul>'
    for issue in (preview.progress or {}).get("issues", []):
        body += (
            "<li>Fila "
            f"{_text(issue.get('row_number'))}"
            ": "
            f"{escape(_reason(issue.get('reason')))}"
            " · "
            f"{_text(issue.get('responsible'))}"
            " · "
            f"{_text(issue.get('next_action'))}"
            "</li>"
        )
    for issue in (preview.progress or {}).get("global_issues", []):
        body += (
            "<li>"
            f"{escape(_reason(issue.get('reason')))}"
            " · "
            f"{_text(issue.get('responsible'))}"
            " · "
            f"{_text(issue.get('next_action'))}"
            "</li>"
        )
    for exception in (preview.progress or {}).get("exceptions", []):
        code = (
            exception.get("reason") if isinstance(exception, dict) else str(exception)
        )
        body += f"<li>{escape(_reason(code))}</li>"
    body += "</ul></details>"
    journal_start = len(body)
    body += _journal_preview(preview)
    body += "<section><h2>Confirmar póliza completa</h2>"
    if completion:
        body += (
            "<p>Todos los cargos ya reconocidos. Finalización conservada: "
            + _text(completion.get("id"))
            + ". No se creó otra póliza; consulta los reconocimientos originales.</p>"
        )
    elif preview.cut is not None:
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
        body += (
            "<label>Fecha contable "
            'declarada<input type="date" '
            'name="accounting_date" min="'
            f"{effective_start}"
            '" max="'
            f"{effective_end}"
            '" required></label>'
        )
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
    journal_body = body[journal_start:]
    body = (
        body[:journal_start]
        + '<section id="amex-stage-policy"><h2>3. Obtener póliza</h2>'
    )
    if frozen:
        body += journal_body
    else:
        button_class = (
            "primary" if preview.status == "ready" and finance else "secondary"
        )
        body += f'<button type="button" class="button {button_class}" data-amex-open="journal" data-amex-title="Revisar póliza">Revisar póliza</button>'
        if preview.status != "ready":
            body += "<p>La confirmación se habilita cuando el estado completo esté listo. Revisa arriba los bloqueos y su siguiente acción.</p>"
        body += dialog_source("journal", journal_body)
    body += "</section>"
    if finance:
        body += f'<p><a href="{BASE}/{import_id}/directo/original">Descargar archivo original privado</a></p>'
    body += '<p><a class="button secondary" href="/admin/gastos/amex/conciliacion">Programar pago de AMEX</a> · Programar no registra pago. Este recorrido no agrega un bloqueo de pago por CFDI.</p>'
    return _html(
        "Estado AMEX directo",
        body,
        actor,
        kind="statement",
        statement_url=f"{BASE}/{import_id}/directo",
        source_version=_source_version(view),
        pending_budget=buckets.get("budget", 0),
    )


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
    body += (
        '<form class="direct-form" '
        'method="GET" action="'
        f"{BASE}"
        "/"
        f"{import_id}"
        '/directo/cfdi"><h3>Buscar factura '
        "del cargo</h3><label>Cargo<select "
        'name="item_id" required><option '
        'value="">Selecciona cargo</option>'
        f"{items}"
        "</select></label>"
    )
    body += _submit("Buscar CFDI SAT por datos de negocio") + "</form>"
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


def _project_option_key(entry: dict) -> str:
    key = (
        "t:" + str(entry["tournament_id"])
        if entry.get("tournament_id")
        else "p:" + str(entry.get("project_code") or "")
    )
    return escape(key, quote=True)


def _journal_preview(preview) -> str:
    if (preview.progress or {}).get("completion_receipt") or (
        preview.progress or {}
    ).get("completion_only"):
        return (
            "<section><h2>Reconocimientos contables previos</h2><p>Todos "
            "los cargos están cubiertos por sus pólizas originales. La "
            "finalización conserva su evidencia y no genera un asiento "
            "nuevo.</p></section>"
        )
    lines = (preview.progress or {}).get("lines", [])
    if preview.cut is not None:
        snapshot = getattr(preview.cut, "snapshot_json", None) or {}
        lines = snapshot.get("lines", lines)
    title = (
        "Póliza congelada Debe / Haber"
        if preview.cut is not None
        else "Propuesta contable Debe / Haber"
    )
    body = f"<section><h2>{title}</h2>"
    if preview.cut is None and preview.status != "ready":
        body += (
            "<p>Propuesta incompleta: el detalle "
            "disponible no autoriza una póliza "
            "parcial. Resuelve todos los "
            "bloqueos antes de confirmar.</p>"
        )
    if not lines:
        return body + (
            "<p>Propuesta incompleta: resuelve "
            "los bloqueos para obtener el "
            "detalle canónico.</p></section>"
        )
    body += (
        "<table "
        'id="direct-journal"><thead><tr><th>C'
        "uenta</th><th>Concepto</th><th>Debe<"
        "/th><th>Haber</th></tr></thead><tbod"
        "y>"
    )
    for line in lines:
        account = line.get("cuenta_codigo") or line.get("account_code")
        account = account or line.get("cuenta")
        concept = line.get("concept") or line.get("concepto")
        debit = line.get("debit") if "debit" in line else line.get("debe")
        credit = line.get("credit") if "credit" in line else line.get("haber")
        columns = (account, concept, debit, credit)
        body += "<tr>" + "".join(f"<td>{_text(v)}</td>" for v in columns) + "</tr>"

    totals = {
        "Debe": str(
            sum((Decimal(str(line.get("debe", "0"))) for line in lines), Decimal("0"))
        ),
        "Haber": str(
            sum((Decimal(str(line.get("haber", "0"))) for line in lines), Decimal("0"))
        ),
    }
    return body + (
        "</tbody></table><p>Total Debe: "
        f"{_text(totals['Debe'])}"
        " · Total Haber: "
        f"{_text(totals['Haber'])}"
        "</p></section>"
    )


@router.get(BASE + "/{import_id}/directo/cfdi")
async def direct_cfdi_search(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    """Business picker; source access and fiscal scope are enforced by its owner."""
    _require_finance_actor(actor)
    params = request.query_params
    try:
        item_id = UUID(params.get("item_id", ""))
        manual = any(
            params.get(key)
            for key in ("search", "q", "date_from", "date_to", "amount", "cursor")
        )
        bank_date = bank_amount = None
        if not manual:
            proposal = await _call(
                suggestions.suggest_direct_statement_cfdis(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    item_ids=[item_id],
                    navigation=request.session,
                    cursor=params.get("suggest_cursor"),
                )
            )
            rows = proposal["items"]
            if not rows:
                raise HTTPException(422, "Selecciona un cargo con fuente válida.")
            row = rows[0]
            bank_date, bank_amount = row.get("bank_date"), row.get("bank_amount")
            result = {**row, "source_version": proposal["source_version"]}
        else:
            result = await _call(
                finder.search_direct_cfdis(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    item_id=item_id,
                    navigation=request.session,
                    q=params.get("q", ""),
                    date_from=(
                        date.fromisoformat(params["date_from"])
                        if params.get("date_from")
                        else None
                    ),
                    date_to=(
                        date.fromisoformat(params["date_to"])
                        if params.get("date_to")
                        else None
                    ),
                    amount=Decimal(params["amount"]) if params.get("amount") else None,
                    cursor=params.get("cursor"),
                )
            )
    except (ValueError, ArithmeticError):
        raise HTTPException(422, "Revisa los filtros de búsqueda.") from None
    body = (
        '<p><a href="'
        f"{BASE}"
        "/"
        f"{import_id}"
        '/directo">Volver al '
        "estado</a></p><h2>CFDI del cargo · "
        "fila "
        f"{_text(result['row_number'])}"
        "</h2>"
    )
    body += (
        "<p>La búsqueda propone facturas. "
        "Consultar vigencia no vincula ni "
        "reserva. Confirma la "
        "correspondencia y los importes "
        "antes de vincular.</p>"
    )
    body += (
        (
            '<p role="status">'
            f"{escape(params.get('msg', ''))}"
            '</p><form method="GET" '
            'class="direct-form">'
        )
        + _hidden("item_id", item_id)
        + _hidden("search", "1")
    )
    if not manual:
        body += (
            "<p>Propuestas ordenadas por identidad, importe, comercio y "
            "fechas. La primera propuesta no está confirmada.</p>"
        )
    for field, label, kind in (
        ("q", "RFC, emisor o UUID fiscal", "search"),
        ("date_from", "Desde", "date"),
        ("date_to", "Hasta", "date"),
        ("amount", "Importe", "text"),
    ):
        body += (
            (
                "<label>"
                f"{label}"
                '<input type="'
                f"{kind}"
                '" name="'
                f"{field}"
                '" value="'
                f"{escape(params.get(field, ''), quote=True)}"
                '"'
            )
            + (' maxlength="120"' if field == "q" else "")
            + "></label>"
        )
    body += _submit("Buscar facturas") + "</form>"
    for candidate in result["candidates"]:
        body += _candidate_differences(candidate, bank_date, bank_amount)
        body += (
            '<section data-cfdi-uuid="'
            f"{candidate['uuid']}"
            '"><h3>'
            f"{_text(candidate['issuer_name'])}"
            " · "
            f"{_text(candidate['issuer_rfc'])}"
            "</h3><p>UUID fiscal: "
            f"{_text(candidate['uuid'])}"
            " · "
            f"{_text(candidate['date'])}"
            " · MXN "
            f"{_text(candidate['total'])}"
            "</p>"
        )
        body += (
            "<p>Vigencia: "
            f"{_text(candidate['status'])}"
            " · Observada: "
            f"{_text(candidate['observed_at'])}"
            "</p>"
        )
        body += (
            "<p>Disponible provisionalmente.</p>"
            if candidate["available"]
            else (
                "<p>Uso existente: requiere revisar "
                "la aplicación compartida explícita. "
                "No se muestran documentos de otros "
                "usuarios.</p>"
            )
        )
        common = _hidden("item_id", item_id) + _hidden("cfdi_id", candidate["id"])
        body += (
            _open(request, import_id, "cfdi/vigencia", result["source_version"])
            + common
        )
        body += (
            _hidden("expected_item_version", result["item_version"])
            + _hidden("expected_identity_hash", candidate["identity_hash"])
            + _hidden("request_key", uuid4())
        )
        body += (
            _confirm("Autorizo consultar vigencia SAT de esta factura seleccionada")
            + _submit("Consultar vigencia de esta factura")
            + "</form>"
        )
        body += _open(request, import_id, "vincular", result["source_version"]) + common
        body += (
            "<label>Importe aplicado declarado "
            "(opcional)<input "
            'name="applied_amount" '
            'inputmode="decimal"></label><label>P'
            "ropina declarada (opcional)<input "
            'name="tip_amount" '
            'inputmode="decimal"></label><label><'
            'input type="checkbox" '
            'name="shared_confirmed" '
            'value="true">Declaro factura '
            "compartida</label><label>Motivo/evid"
            'encia<input name="reason" '
            "required></label>"
        )
        body += (
            _confirm("Confirmo correspondencia fiscal e importes de esta factura")
            + _submit("Vincular factura seleccionada")
            + "</form></section>"
        )
    if not result["candidates"]:
        body += (
            (
                "<p>No hay candidatos válidos en esta página examinada. "
                "La búsqueda aún está incompleta; puedes continuar.</p>"
            )
            if result.get("has_more") or not result.get("coverage_complete", True)
            else "<p>No hay candidatos válidos en la búsqueda completa.</p>"
        )
    if result["has_more"]:
        query = dict(params)
        query["cursor" if manual else "suggest_cursor"] = result["cursor"]
        body += (
            "<p>Hay más filas por revisar. <a "
            'href="?'
            f"{escape(urlencode(query), quote=True)}"
            '">Siguiente página</a></p>'
        )
    return _html("Buscar CFDI SAT del cargo AMEX", body, actor)


@router.post(BASE + "/{import_id}/directo/cfdi/vigencia")
async def direct_selected_cfdi_status(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(actor)
    actor_id = _actor_id(actor)
    form = await _form(request)
    item_id = _uuid(form, "item_id")
    if not _confirmed(form):
        raise HTTPException(422, "Confirma la consulta SAT de esta factura.")
    factory = dependencies._db_session_maker
    if factory is None:
        raise HTTPException(503, "La consulta SAT no está disponible.")
    await session.rollback()
    result = await _call(
        status.consult_selected_direct_cfdi_status(
            factory,
            actor_id=actor_id,
            import_id=import_id,
            item_id=item_id,
            cfdi_id=_uuid(form, "cfdi_id"),
            expected_source_version=_version(form),
            expected_item_version=_version(form, "expected_item_version"),
            expected_identity_hash=_value(
                form, "expected_identity_hash", required=True
            ),
            request_key=_value(form, "request_key", required=True),
            consented=True,
        )
    )
    message = (
        _reason(result.reason)
        if result.status == "pending"
        else "Vigencia observada: "
        + str(result.progress.get("status"))
        + ". Revisa y confirma el vínculo por separado."
    )
    return RedirectResponse(
        f"{BASE}/{import_id}/directo/cfdi?"
        + urlencode(
            {"item_id": str(item_id), "msg": message}
            | ({"error_msg": message} if result.status == "pending" else {})
        ),
        303,
    )


@router.get(BASE + "/{import_id}/directo/periodo")
async def direct_period_form(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(actor)
    view = await _call(
        context.list_direct_statement(
            session, actor_id=_actor_id(actor), import_id=import_id
        )
    )
    body = (
        '<p><a href="'
        f"{BASE}"
        "/"
        f"{import_id}"
        '/directo">Volver al '
        "estado</a></p><p>Declaración "
        "original: "
        f"{_text(view.statement.period_start)}"
        " a "
        f"{_text(view.statement.period_end)}"
        ". La corrección conserva archivo, "
        "recibos y cargos existentes; sólo "
        "recupera cargos demostrados "
        "omitidos por período.</p>"
    )
    body += _open(request, import_id, "periodo/previsualizar", _source_version(view))
    body += (
        "<label>Inicio corregido<input "
        'name="period_start" type="date" '
        "required></label><label>Fin "
        'corregido<input name="period_end" '
        'type="date" '
        "required></label><label>Motivo<input"
        ' name="reason" required></label>'
    )
    body += _submit("Previsualizar recuperación de período") + "</form>"
    return _html("Revisar cobertura del estado", body, actor)


@router.post(BASE + "/{import_id}/directo/periodo/{operation}")
async def direct_period_action(
    import_id: UUID,
    operation: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    _require_finance_actor(actor)
    form = await _form(request)
    if operation not in {"previsualizar", "confirmar"}:
        raise HTTPException(404, "Acción no disponible.")
    try:
        payload = dict(
            actor_id=_actor_id(actor),
            import_id=import_id,
            period_start=date.fromisoformat(
                _value(form, "period_start", required=True)
            ),
            period_end=date.fromisoformat(_value(form, "period_end", required=True)),
            reason=_value(form, "reason", required=True),
            expected_source_version=_version(form),
        )
        if operation == "confirmar":
            result = await _call(
                recovery.confirm_direct_period_recovery(
                    session,
                    **payload,
                    preview_hash=_value(form, "preview_hash", required=True),
                    idempotency_key=_value(form, "idempotency_key", required=True),
                    confirmed=_confirmed(form),
                )
            )
            if result.status not in {"recovered", "exists"}:
                await session.rollback()
                return _redirect(import_id, _reason(result.reason), error=True)
            await session.commit()
            return _redirect(
                import_id,
                (
                    "Cobertura efectiva corregida. Los "
                    "cargos recuperados requieren CFDI, "
                    "clasificación y partida ordinarios."
                ),
            )
        result = await _call(
            recovery.preview_direct_period_recovery(session, **payload)
        )
        plan = result.progress or {}
        message = (
            _reason(result.reason)
            if result.reason
            else "Revisa la recuperación propuesta."
        )
        body = f"<p>{escape(message)}</p>"
        body += (
            "<p>Período corregido: "
            f"{_text(plan.get('period_start'))}"
            " a "
            f"{_text(plan.get('period_end'))}"
            ". Cargos por recuperar: "
            f"{_text(plan.get('expected_count'))}"
            ".</p>"
        )
        if plan.get("original_counts"):
            body += (
                "<table><thead><tr><th>Movimiento</th"
                "><th>Original "
                "conservado</th><th>Cobertura "
                "efectiva</th></tr></thead><tbody>"
            )
            for key, label in (
                ("debit", "Cargos"),
                ("credit", "Créditos"),
                ("skipped", "Omitidas"),
                ("unknown", "Sin resolver"),
            ):
                body += (
                    "<tr><td>"
                    f"{label}"
                    "</td><td>"
                    f"{_text(plan['original_counts'].get(key))}"
                    "</td><td>"
                    f"{_text(plan['effective_counts'].get(key))}"
                    "</td></tr>"
                )
            body += (
                "</tbody></table><table><thead><tr><t"
                "h>Fila original</th><th>Fecha "
                "económica</th><th>Importe</th></tr><"
                "/thead><tbody>"
            )
            for row in plan.get("rows", []):
                body += (
                    "<tr><td>"
                    f"{_text(row.get('row_number'))}"
                    "</td><td>"
                    f"{_text(row.get('date'))}"
                    "</td><td>"
                    f"{_text(row.get('amount'))}"
                    "</td></tr>"
                )
            body += "</tbody></table>"
        if result.status == "preview":
            body += _open(request, import_id, "periodo/confirmar", _version(form))
            for name in ("period_start", "period_end", "reason"):
                body += _hidden(name, _value(form, name, required=True))
            body += _hidden("preview_hash", result.progress["preview_hash"]) + _hidden(
                "idempotency_key", uuid4()
            )
            body += (
                _confirm(
                    (
                        "Confirmo los límites y recuperar "
                        "únicamente los cargos omitidos "
                        "demostrados"
                    )
                )
                + _submit("Confirmar recuperación de período")
                + "</form>"
            )
        return _html("Preview de recuperación de período", body, actor)
    except (ValueError, ArithmeticError):
        await session.rollback()
        raise HTTPException(
            422, "Revisa el período y los datos de recuperación."
        ) from None
    except Exception:
        await session.rollback()
        raise


@router.get(BASE + "/{import_id}/directo/cuenta-gastos")
async def direct_expense_account_sources(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = require_admin_finanzas(),
):
    """Review evidenced original credits; listing never settles an account."""
    _require_finance_actor(actor)
    try:
        item_id = UUID(request.query_params.get("item_id", ""))
    except ValueError:
        raise HTTPException(422, "Selecciona el cargo del estado.") from None
    result = await _call(
        settlements.list_settlement_sources(
            session,
            actor_id=_actor_id(actor),
            import_id=import_id,
            item_id=item_id,
            navigation=request.session,
            cursor=request.query_params.get("cursor"),
        )
    )
    body = f'<p><a href="{BASE}/{import_id}/directo">Volver al estado</a></p>'
    body += (
        "<p>Selecciona únicamente el reconocimiento contable que "
        "corresponde al cargo. La selección no cancela saldos: se "
        "confirma con el estado completo.</p>"
    )
    for candidate in result.get("candidates", []):
        body += "<section><h2>" + _text(candidate.get("concepto")) + "</h2>"
        body += (
            "<p>Pasivo AMEX ya reconocido: sólo asociar el cargo, sin otro asiento.</p>"
            if candidate.get("coverage_kind") == "prior"
            else (
                "<p>Crédito de cuenta de gastos pendiente: cancelar "
                "contra AMEX, sin nuevo gasto ni IVA.</p>"
            )
        )
        body += (
            "<p>Cuenta original: "
            + _text(candidate.get("origin_account_code"))
            + " · Crédito: "
            + _text(candidate.get("credit_amount"))
            + " · Disponible: "
            + _text(candidate.get("residual_amount"))
            + " · CFDI: "
            + _text(candidate.get("cfdi_uuid"))
            + "</p>"
        )
        body += _open(request, import_id, "cuenta-gastos", result["source_version"])
        body += _hidden("item_id", item_id) + _hidden(
            "expected_item_version", result["item_version"]
        )
        for field in (
            "origin_poliza_id",
            "origin_line_id",
            "origin_expense_id",
            "origin_document_id",
            "origin_cuenta_gastos_id",
            "origin_account_id",
        ):
            body += _hidden(field, candidate[field])
        body += (
            '<label>Motivo de la correspondencia<input name="reason" required></label>'
        )
        body += (
            _confirm("Confirmo el origen contable que corresponde a este cargo")
            + _submit("Seleccionar cuenta de gastos de origen")
            + "</form></section>"
        )
    if not result.get("candidates"):
        body += (
            "<p>No hay créditos de origen con evidencia suficiente en "
            "este ámbito. No se infiere una cuenta por el titular o el "
            "importe.</p>"
        )
    if result.get("has_more"):
        continuation = urlencode(
            {"item_id": str(item_id), "cursor": result["next_cursor"]}
        )
        body += (
            '<p><a href="?'
            + escape(continuation, quote=True)
            + (
                '">Siguientes orígenes contables</a>. Hay más opciones '
                "dentro de tu ámbito.</p>"
            )
        )
    for reason in result.get("exceptions", []):
        body += "<p>" + escape(_reason(str(reason))) + "</p>"
    return _html("Cuenta de gastos reconocida", body, actor)


def _treatment_input(form: FormData) -> dict:
    permitted = {
        "_csrf_token",
        "expected_source_version",
        "item_ids",
        "expected_versions",
        "treatment",
        "debtor_account_id",
        "reason",
        "decision_action",
        "expected_preview_hash",
        "idempotency_key",
        "confirmed",
    }
    if set(form) - permitted:
        raise HTTPException(422, "El formulario contiene campos ajenos al tratamiento.")
    ids, versions = _selection(form)
    kind = _value(form, "treatment", required=True)
    action = _value(form, "decision_action") or "set"
    if kind not in _TREATMENT_LABELS or action not in {"set", "revoke"}:
        raise HTTPException(422, "Selecciona un tratamiento válido.")
    debtor = _optional_uuid(form, "debtor_account_id")
    if action == "set" and (kind == "partner_receivable") != (debtor is not None):
        raise HTTPException(
            422, "El cargo al socio requiere una cuenta deudora explícita."
        )
    return {
        "item_ids": ids,
        "expected_versions": versions,
        "treatment": kind,
        "debtor_account_id": debtor,
        "reason": _value(form, "reason", required=True),
        "action": action,
    }


async def _treatment_retry(
    request, session, actor, import_id, values, reason, *, human_message=False
):
    """Retain entered values and require a fresh preview against current rows."""
    await session.rollback()
    view = await _call(
        context.list_direct_statement(
            session, actor_id=_actor_id(actor), import_id=import_id
        )
    )
    message = str(reason) if human_message else _reason(reason)
    body = '<p role="alert">' + escape(message) + "</p>"
    body += f'<p><a href="{BASE}/{import_id}/directo">Volver al estado completo</a></p>'
    body += "<p>Tratamiento solicitado: " + _text(
        _TREATMENT_LABELS[values["treatment"]]
    )
    body += "<br>Motivo conservado: " + _text(values["reason"]) + "</p>"
    selected = {str(value) for value in values["item_ids"]}
    current = [row for row in view.items if str(row.id) in selected]
    if (
        len(current) == len(selected)
        and getattr(view, "cut", None) is None
        and all(
            row.movement_kind == "debit" and row.accounting_status != "recognized"
            for row in current
        )
    ):
        options = await _call(
            treatments.list_direct_account_treatment_options(
                session, actor_id=_actor_id(actor), import_id=import_id
            )
        )
        body += "<p>Los datos ingresados se conservan. Revisa las versiones actuales y previsualiza otra vez antes de confirmar.</p>"
        body += _treatment_form(
            request,
            import_id,
            _source_version(view),
            {str(row.id): row.source_version for row in current},
            options,
            values,
        )
    else:
        body += "<p>La selección cambió o ya está reconocida. Revisa el estado antes de volver a seleccionar cargos.</p>"
    response = _html("Revisar el tratamiento AMEX", body, actor)
    response.status_code = 409
    await session.rollback()
    return response


@router.post(BASE + "/{import_id}/directo/tratamiento/preview")
async def direct_treatment_preview(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Preview an explicit batch through its owner without business writes."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    form = await _form(request)
    try:
        values = _treatment_input(form)
        try:
            result = await _call(
                treatments.preview_direct_account_treatment(
                    session, actor_id=_actor_id(actor), import_id=import_id, **values
                )
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return await _treatment_retry(
                request,
                session,
                actor,
                import_id,
                values,
                exc.detail,
                human_message=True,
            )
        if result.status != "preview":
            return await _treatment_retry(
                request, session, actor, import_id, values, result.reason
            )
        progress = result.progress
        manifest = progress["manifest"]
        body = f'<p><a href="{BASE}/{import_id}/directo">Volver al estado sin confirmar</a></p>'
        body += "<h2>Revisa los cargos seleccionados</h2><p>Acción: "
        body += (
            "Retirar el tratamiento actual"
            if values["action"] == "revoke"
            else "Asignar o cambiar tratamiento"
        )
        body += "</p><p>Tratamiento: "
        body += _text(_TREATMENT_LABELS[values["treatment"]]) + "</p>"
        account = (manifest.get("account_snapshot") or {}).get("debtor")
        if account:
            body += (
                "<p>Cuenta deudora: "
                + _text(account.get("code"))
                + " · "
                + _text(account.get("name"))
                + "</p>"
            )
        body += "<p>Motivo: " + _text(values["reason"]) + "</p>"
        body += '<div class="table-shell"><table><thead><tr><th>Fila</th><th>Comercio</th><th>Importe</th></tr></thead><tbody>'
        for item in manifest["items"]:
            body += (
                "<tr><td>"
                + _text(item.get("row_number"))
                + "</td><td>"
                + _text(item.get("description"))
                + "</td><td>"
                + _text(item.get("amount"))
                + "</td></tr>"
            )
        body += (
            "</tbody></table></div><p>"
            + str(len(manifest["items"]))
            + " cargos seleccionados. Se conservarán sus facturas e historia.</p>"
        )
        body += _open(request, import_id, "tratamiento", manifest["source_version"])
        for item_id in values["item_ids"]:
            body += _hidden("item_ids", item_id)
        body += _hidden("expected_versions", json.dumps(values["expected_versions"]))
        for key in ("treatment", "reason", "debtor_account_id"):
            body += _hidden(key, values[key] if values[key] is not None else "")
        body += _hidden("decision_action", values["action"])
        body += _hidden("expected_preview_hash", progress["preview_hash"])
        body += _hidden("idempotency_key", str(uuid4()))
        body += _confirm(
            "Confirmo retirar el tratamiento de todos los cargos mostrados"
            if values["action"] == "revoke"
            else "Confirmo aplicar este tratamiento a todos los cargos mostrados"
        )
        body += (
            _submit(
                "Confirmar retiro del tratamiento"
                if values["action"] == "revoke"
                else "Confirmar tratamiento del lote"
            )
            + "</form>"
        )
        return _html("Revisión del tratamiento AMEX", body, actor)
    finally:
        await session.rollback()


@router.post(BASE + "/{import_id}/directo/tratamiento")
async def direct_treatment_confirm(
    import_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Confirm the exact reviewed batch; the owner enforces atomicity and scope."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    form = await _form(request)
    try:
        values = _treatment_input(form)
        if not _confirmed(form):
            raise HTTPException(
                422, "Confirma el tratamiento de todos los cargos revisados."
            )
        try:
            result = await _call(
                treatments.confirm_direct_account_treatment(
                    session,
                    actor_id=_actor_id(actor),
                    import_id=import_id,
                    **values,
                    expected_preview_hash=_value(
                        form, "expected_preview_hash", required=True
                    ),
                    idempotency_key=_value(form, "idempotency_key", required=True),
                    confirmed=_confirmed(form),
                )
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return await _treatment_retry(
                request,
                session,
                actor,
                import_id,
                values,
                exc.detail,
                human_message=True,
            )
        if result.status == "pending":
            return await _treatment_retry(
                request, session, actor, import_id, values, result.reason
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    message = (
        "Tratamiento retirado; historial conservado. Elige el nuevo tratamiento antes de continuar."
        if values["action"] == "revoke"
        else "Tratamiento del lote guardado. Las prestaciones requieren partida de Control Presupuestal; los cargos al socio no se envían a presupuesto."
    )
    return _redirect(import_id, message)


@router.get(BASE + "/{import_id}/directo/tratamiento/revocar")
async def direct_treatment_revoke_form(
    import_id: UUID,
    item_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    actor: Empleado = Depends(get_current_empleado),
):
    """Read the current treatment for an explicit, separately confirmed withdrawal."""
    if not actor.activo:
        raise HTTPException(403, "La cuenta está inactiva.")
    view = await _call(
        context.list_direct_statement(
            session, actor_id=_actor_id(actor), import_id=import_id
        )
    )
    await _call(
        treatments.list_direct_account_treatment_options(
            session, actor_id=_actor_id(actor), import_id=import_id
        )
    )
    item = next((row for row in view.items if row.id == item_id), None)
    if (
        item is None
        or getattr(view, "cut", None) is not None
        or not getattr(item, "account_treatment_decision_id", None)
        or _treatment_kind(item) not in _TREATMENT_LABELS
        or item.accounting_status == "recognized"
    ):
        raise HTTPException(
            409,
            "El tratamiento no está disponible para retiro. Revisa el estado actual.",
        )
    body = f'<p><a href="{BASE}/{import_id}/directo">Volver al estado</a></p>'
    body += "<p>Retirar: " + _text(_TREATMENT_LABELS[_treatment_kind(item)])
    body += "<br>Cargo: " + _text(item.descriptor) + " · " + _text(item.amount) + "</p>"
    body += "<p>Los vínculos fiscales y el historial se conservan. Si existe una asignación presupuestal, primero debe devolverse o liberarse por su autoridad.</p>"
    body += _open(request, import_id, "tratamiento/preview", _source_version(view))
    body += _hidden("item_ids", item.id)
    body += _hidden(
        "expected_versions", json.dumps({str(item.id): item.source_version})
    )
    body += _hidden("treatment", _treatment_kind(item))
    body += _hidden("debtor_account_id", getattr(item, "debtor_account_id", None) or "")
    body += _hidden("decision_action", "revoke")
    body += '<label>Motivo del retiro<input name="reason" required maxlength="1000"></label>'
    body += _submit("Revisar retiro de tratamiento") + "</form>"
    return _html("Retirar tratamiento AMEX", body, actor)


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
                common["actor_id"],
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
                error=result.status == "pending",
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
        elif action in {"sin-cfdi", "revocar-sin-cfdi"}:
            ids, versions = _selection(form)
            reason = _value(form, "reason", required=True)
            request_key = _value(form, "request_key", required=True)
            if not _confirmed(form):
                raise HTTPException(422, _reason("no_cfdi_confirmation_required"))
            if len(reason) > 1000:
                raise HTTPException(422, _reason("no_cfdi_reason_required"))
            if len(request_key) > 160:
                raise HTTPException(
                    422, "La clave de petición no es válida. Recarga la pantalla."
                )
            owner = (
                no_cfdi.declare_no_cfdi_items
                if action == "sin-cfdi"
                else no_cfdi.revoke_no_cfdi_items
            )
            result = await _call(
                owner(
                    session,
                    **common,
                    item_ids=ids,
                    expected_versions=versions,
                    expected_source_version=version,
                    reason=reason,
                    request_key=request_key,
                    confirmed=True,
                )
            )
        elif action == "vincular":
            result = await _call(
                matching.link_direct_cfdi_manual(
                    session,
                    actor_id=common["actor_id"],
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
                    actor_id=common["actor_id"],
                    import_id=import_id,
                    item_id=_uuid(form, "item_id"),
                    expected_source_version=version,
                    reason=_value(form, "reason", required=True),
                    confirmed=_confirmed(form),
                )
            )
        elif action == "cuenta-gastos":
            result = await _call(
                settlements.select_settlement_source(
                    session,
                    **common,
                    item_id=_uuid(form, "item_id"),
                    origin_poliza_id=_uuid(form, "origin_poliza_id"),
                    origin_line_id=_uuid(form, "origin_line_id"),
                    origin_expense_id=_uuid(form, "origin_expense_id"),
                    origin_document_id=_uuid(form, "origin_document_id"),
                    origin_cuenta_gastos_id=_uuid(form, "origin_cuenta_gastos_id"),
                    origin_account_id=_uuid(form, "origin_account_id"),
                    expected_item_version=_version(form, "expected_item_version"),
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
            return _redirect(import_id, _reason(result.reason), error=True)
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
            common["actor_id"],
            import_id,
            _source_version(result),
            context_request_key,
        )
    messages = {
        "sin-cfdi": "Lote declarado Sin CFDI. Revisa el tratamiento, sus requisitos contables y la confirmación del corte completo.",
        "revocar-sin-cfdi": "Declaración Sin CFDI revocada para el lote; historial conservado. Puedes volver a buscar factura.",
        "clasificar": "Clasificación guardada; no acredita asignación ni póliza.",
        "enviar-presupuesto": "Cargos enviados a Control Presupuestal; todavía requieren partida.",
        "cuenta-gastos": (
            "Origen seleccionado; revisa el estado completo antes de "
            "confirmar la cancelación."
        ),
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
    body = "<p>Asigna la partida de los gastos de proyecto y las prestaciones. Las prestaciones no requieren proyecto ni categoría previos: la partida determina su cuenta. Los cargos a cuenta deudora del socio no entran a esta cola. El plan no se edita aquí.</p>"
    for item in rows:
        can_assign = getattr(item, "can_assign", item.budget_status == "sent")
        lines = (
            []
            if not can_assign
            else await _call(
                budget.list_direct_budget_lines(
                    session, actor_id=_actor_id(actor), item_id=item.id
                )
            )
        )
        options = "".join(
            f'<option value="{entry["id"]}">{_text(entry["label"])}</option>'
            for entry in lines
        )
        if not lines and item.budget_status != "assigned":
            body += "<p>No hay partidas compatibles en este contexto. Devuelve el cargo a Contabilidad con el motivo de revisión.</p>"
        body += f"<section><h2>Fila {item.row_number} · {_text(item.descriptor)}</h2><p>{_text(item.amount)} {_text(item.currency)}"
        if _treatment_kind(item) == "benefit_expense":
            body += " · Prestación sin proyecto; la partida determina la cuenta.</p>"
        else:
            body += f" · Proyecto: {_text(getattr(item, 'tournament_name', None) or getattr(item, 'project_name', None) or item.tournament_id or item.project_code)} · Categoría: {_text(getattr(item, 'budget_concept_name', None) or item.budget_concept_id)}</p>"
        hidden = _csrf(request) + _hidden(
            "expected_source_version", item.source_version
        )
        if item.budget_status == "sent" and can_assign:
            body += (
                '<form class="direct-form" '
                'method="POST" action="'
                f"{QUEUE}"
                "/"
                f"{item.id}"
                '/asignar">'
                f"{hidden}"
            )
            body += _hidden("expected_source_hash", item.source_hash) + _hidden(
                "idempotency_key", uuid4()
            )
            body += (
                "<label>Partida y versión "
                "presupuestal<select "
                'name="budget_line_id" '
                "required><option "
                'value="">Selecciona partida</option>'
                f"{options}"
                "</select></label>"
            )
            body += _submit("Asignar partida existente") + "</form>"
        body += (
            f'<form class="direct-form" method="POST" action="{QUEUE}/{item.id}/rechazar">{hidden}<label>Motivo de devolución<input name="reason" required></label>'
            + _submit("Devolver a Contabilidad")
            + "</form></section>"
        )
    if not rows:
        body += "<p>No hay cargos pendientes dentro de tu ámbito autorizado.</p>"
    body += f'<p role="status">{escape(request.query_params.get("msg", ""))}</p>'
    return _html("Control Presupuestal AMEX directo", body, actor, kind="budget")


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
