"""Explain existing expense blockers without changing their business conditions."""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


# Existing redirect codes also need a visible explanation when no text was sent.
_CODE_CAUSES = {
    "cancelled_expenses": "Las partidas seleccionadas incluyen gastos cancelados.",
    "coi_not_ready": "Faltan requisitos contables para generar la póliza COI.",
    "cuenta_cerrada": "El informe está cerrado y no admite esta acción.",
    "cuenta_not_found": "No se encontró el informe seleccionado.",
    "currency_mismatch": "Las monedas del gasto y del informe no coinciden.",
    "documento_not_borrador": "El documento ya no está en borrador.",
    "documento_not_found": "No se encontró el documento seleccionado.",
    "duplicate_referencia": "La referencia indicada ya pertenece a otro informe.",
    "empty_selection": "No seleccionaste registros para esta operación.",
    "exceso_monto": "El monto indicado excede el límite de esta operación.",
    "expense_not_found": "No se encontró la partida seleccionada.",
    "informe_not_approved": "Esta acción requiere que el informe esté aprobado.",
    "invalid_cuenta_id": "El identificador del informe seleccionado no es válido.",
    "invalid_documento_id": "El identificador del documento no es válido.",
    "invalid_estado": "El estado actual del registro no permite esta acción.",
    "invalid_fecha": "La fecha indicada no es válida.",
    "invalid_fecha_pago": "La fecha de pago indicada no es válida.",
    "invalid_gasto_ids": "La selección contiene identificadores de gasto inválidos.",
    "invalid_item": "El registro seleccionado no es válido para esta operación.",
    "invalid_item_id": "El identificador del registro seleccionado no es válido.",
    "invalid_monto": "El monto indicado no es válido.",
    "invalid_proveedor": "El proveedor seleccionado no es válido.",
    "invalid_tipo": "El tipo de registro seleccionado no permite esta operación.",
    "locked_cuenta": "Un gasto seleccionado pertenece a un informe cerrado.",
    "locked_expenses": "Hay gastos bloqueados por su estado actual.",
    "missing_budget_concept": "Falta la clasificación de Control Presupuestal.",
    "missing_concepto": "Falta el concepto del gasto.",
    "missing_proveedor": "Falta seleccionar el proveedor.",
    "no_cuenta_selected": "Falta seleccionar un informe de gastos.",
    "no_gastos": "No hay gastos disponibles para esta operación.",
    "no_gastos_selected": "Falta seleccionar los gastos.",
    "no_informe_doc": "El informe no tiene documento INFORME vinculado.",
    "payment_proof_required": "Falta el comprobante de pago requerido.",
    "permission_denied": "Tu perfil no tiene permiso para esta acción.",
    "unauthorized_expenses": "No tienes permiso para usar los gastos seleccionados.",
    "workflow_locked_document": "El estado actual del documento bloquea esta acción.",
    "bulk_action_partial": (
        "La acción no se completó en todos los registros. "
        "Revisa el resultado de cada partida antes de repetir la operación."
    ),
    "viaje_validation": (
        "La validación de los datos del viaje impide continuar. "
        "Revisa sus requisitos con Operaciones."
    ),
}


def explain_block(message: str, *, status_code: Optional[int] = None) -> str:
    """Keep the known cause and append a relevant, non-bypassing next step."""
    message = (message or "").strip()
    if not message or "Qué hacer:" in message:
        return message
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", message.lower())
        if not unicodedata.combining(char)
    )
    if any(
        word in normalized
        for word in ("traceback", "sqlalchemy", "asyncpg", "database_url")
    ):
        message = (
            "No se pudo completar la operación. "
            "La causa técnica requiere revisión de Soporte."
        )
        normalized = message.lower()
    if "no se pudo" in normalized or "ocurrio un error" in normalized:
        action = (
            "Contacta a Soporte e indica la operación y el folio. "
            "No se pudo confirmar la causa específica con la información disponible."
        )
    elif "factura" in normalized and any(
        word in normalized for word in ("vinculada", "reservada", "compartida")
    ):
        action = (
            "Solicita a Finanzas y Operaciones que revisen "
            "la vinculación de la factura."
        )
    elif "solo lectura" in normalized or "sólo lectura" in normalized:
        action = (
            "Solicita el cambio al responsable del informe o a Finanzas; "
            "tu acceso permite consultar."
        )
    elif any(
        word in normalized for word in ("permiso", "acceso denegado", "access denied")
    ):
        action = (
            "Solicita la acción al responsable autorizado; "
            "tu acceso actual no permite realizarla."
        )
    elif "cancelad" in normalized or "eliminad" in normalized:
        action = "Revisa el estado del registro con Operaciones antes de continuar."
    elif any(
        word in normalized
        for word in (
            "cerrad",
            "aprob",
            "borrador",
            "estado",
            "presupuestal",
            "bloquead",
        )
    ):
        action = (
            "Revisa el estado y el requisito pendiente "
            "con el responsable del informe o Finanzas."
        )
    elif "moneda" in normalized:
        action = "Usa gastos y un informe de la misma moneda."
    elif "fecha" in normalized:
        action = "Revisa la fecha indicada y captura una fecha válida."
    elif any(
        word in normalized
        for word in ("xml", "pdf", "archivo", "adjunt", "comprobante")
    ):
        action = (
            "Revisa el archivo indicado y carga un comprobante "
            "que cumpla el requisito mostrado."
        )
    elif any(
        word in normalized
        for word in (
            "monto",
            "total",
            "subtotal",
            "sub total",
            "numero",
            "negativo",
            "mayor a cero",
        )
    ):
        action = "Revisa el importe o dato indicado y su comprobante antes de guardar."
    elif any(
        word in normalized
        for word in ("requerid", "seleccion", "beneficiario", "concepto", "descripcion")
    ):
        action = "Completa o corrige el dato indicado antes de continuar."
    elif status_code == 404 or any(
        word in normalized for word in ("no encontrad", "no se encontr", "not found")
    ):
        action = (
            "Verifica el registro seleccionado; "
            "si sigue sin aparecer, contacta a Soporte."
        )
    else:
        action = (
            "Solicita al responsable de la operación que revise el requisito indicado."
        )
    return f"{message.rstrip('.')}. Qué hacer: {action}"


def duplicate_invoice_message(
    *,
    amount: Decimal,
    currency: str,
    expense_reference: Optional[str],
    report_reference: Optional[str],
    operations_reference: Optional[str],
    report_name: Optional[str],
    beneficiary: Optional[str],
    state: Optional[str],
    current_reference: Optional[str] = None,
    current_operations: Optional[str] = None,
) -> str:
    """Render only verified, authorized facts supplied by the read owner."""
    where = (
        f" en la partida {expense_reference}"
        if expense_reference
        else " en otra partida"
    )
    if report_reference:
        where += f", informe {report_reference}"
    if operations_reference:
        where += f", referencia de Operaciones {operations_reference}"
    if report_name:
        where += f", “{report_name}”"
    if beneficiary:
        where += f", cuyo beneficiario es {beneficiary}"
    cause = f"Esta factura ya está registrada por ${amount:,.2f} {currency}{where}."
    if state:
        cause += f" Estado del registro: {state}."
    target = (
        f" en tu informe {current_reference}"
        if current_reference
        else " en esta partida"
    )
    if current_operations:
        target += f", referencia {current_operations}"
    return (
        cause
        + f" Por eso no puede volver a capturarse{target}."
        + " Esto no significa que tú la hayas usado antes."
        + " Solicita a Finanzas y Operaciones que revisen ese registro."
        + " No marques ‘Factura compartida’ para continuar."
    )


def expense_lock_reason(expense, document=None) -> str:
    """Name all actual lock conditions, rather than listing possible causes."""
    reasons = []
    if document is not None and document.estado != "borrador":
        reasons.append(
            f"el documento {document.numero_referencia or document.id} "
            f"está en estado {document.estado}"
        )
    if expense.estado_factura in {"en_proceso", "completada"}:
        reasons.append(f"la factura está en estado {expense.estado_factura}")
    return "; ".join(reasons)


class ExpenseBlockRoute(APIRoute):
    """Present existing failures consistently on expense/document routes only."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        prefixes = (
            "/gastos",
            "/informes-de-gastos",
            "/documentos",
            "/api/gastos",
            "/api/informes-de-gastos",
            "/api/documentos",
        )
        scoped = any(
            self.path == prefix or self.path.startswith(prefix + "/")
            for prefix in prefixes
        )

        async def handle(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                if scoped:
                    for error in exc.errors():
                        field = ".".join(str(item) for item in error["loc"][1:])
                        if error["type"] == "missing":
                            error["msg"] = (
                                f"El campo {field} es obligatorio. "
                                "Completa este dato antes de continuar."
                            )
                        else:
                            error["msg"] = f"Revisa el campo {field}: {error['msg']}"
                raise
            except HTTPException as exc:
                if (
                    scoped
                    and isinstance(exc.detail, str)
                    and exc.status_code in {400, 403, 404, 409, 422}
                ):
                    exc.detail = explain_block(exc.detail, status_code=exc.status_code)
                raise
            if (
                scoped
                and response.status_code >= 400
                and "application/json" in response.headers.get("content-type", "")
            ):
                payload = json.loads(response.body)
                if isinstance(payload, dict):
                    for key in ("detail", "message", "error"):
                        value = payload.get(key)
                        if isinstance(value, str) and (key != "error" or " " in value):
                            payload[key] = explain_block(
                                value, status_code=response.status_code
                            )
                    response.body = json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    response.headers["content-length"] = str(len(response.body))
            location = response.headers.get("location")
            if scoped and location and response.status_code in {302, 303, 307, 308}:
                parts = urlsplit(location)
                params = parse_qsl(parts.query, keep_blank_values=True)
                if any(key == "error_msg" for key, _ in params):
                    params = [
                        (key, explain_block(value) if key == "error_msg" else value)
                        for key, value in params
                    ]
                elif any(key == "error" for key, _ in params):
                    error_code = next(value for key, value in params if key == "error")
                    if error_code != "expense_cfdi_duplicate":
                        cause = _CODE_CAUSES.get(
                            error_code, "No se pudo completar esta operación."
                        )
                        params.append(("error_msg", explain_block(cause)))
                if any(key == "error_msg" for key, _ in params):
                    response.headers["location"] = urlunsplit(
                        (
                            parts.scheme,
                            parts.netloc,
                            parts.path,
                            urlencode(params),
                            parts.fragment,
                        )
                    )
            return response

        return handle
