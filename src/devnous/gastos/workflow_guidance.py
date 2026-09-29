"""Presentation-only guidance for existing document and payment states.

The builders in this module consume facts that route and service owners have
already resolved.  They do not authorize actions, infer state transitions, or
write business data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .status_semantics import document_status_visual, payment_run_status_visual


@dataclass(frozen=True)
class WorkflowGuidance:
    """Human explanation of an already-canonical workflow state."""

    status_label: str
    status_note: str
    semantic: str
    why_here: str
    next_owner: str
    next_action: str
    blocker: Optional[str] = None


def _clean(value: object) -> str:
    return str(value or "").strip()


def build_document_workflow_guidance(
    *,
    state: Optional[str],
    document_type: Optional[str],
    is_owner: bool,
    can_approve_or_reject: bool,
    rejection_reason: Optional[str] = None,
    rejection_actor: Optional[str] = None,
    locked_reason: Optional[str] = None,
    has_payment_timestamp: bool = False,
    has_payment_proof: bool = False,
) -> WorkflowGuidance:
    """Describe document state without changing or predicting the workflow."""

    normalized = _clean(state).lower()
    doc_type = _clean(document_type).upper()
    visual = document_status_visual(state)
    # Edit locks only block steps that require editing or resubmitting.
    explicit_blocker = (
        (_clean(locked_reason) or None)
        if normalized in {"borrador", "rechazado"}
        else None
    )

    if normalized == "borrador":
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            "El documento todavía no se ha enviado a revisión.",
            "Solicitante",
            "Completar y enviar" if is_owner else "Esperar al solicitante",
            explicit_blocker,
        )

    if normalized == "rechazado":
        actor = _clean(rejection_actor)
        why_here = "La última decisión registrada fue un rechazo."
        if actor:
            why_here = f"La última decisión registrada fue un rechazo de {actor}."
        reason = _clean(rejection_reason)
        next_action = (
            "Corregir y reenviar" if is_owner else "Esperar corrección del solicitante"
        )
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            why_here,
            "Solicitante",
            next_action,
            explicit_blocker or reason or "Motivo de rechazo no disponible",
        )

    if normalized == "control_presupuestal":
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            (
                "El flujo está detenido hasta completar la clasificación "
                "presupuestal."
            ),
            "Control Presupuestal",
            "Asignar la partida o concepto válido",
            explicit_blocker or "Asignación presupuestal pendiente",
        )

    if normalized in {"enviado", "en_revision", "en revisión"}:
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            "El documento fue enviado y requiere una decisión de aprobación.",
            (
                "Aprobador autorizado (tú)"
                if can_approve_or_reject
                else "Aprobador configurado"
            ),
            (
                "Revisar evidencia y decidir"
                if can_approve_or_reject
                else "Esperar la decisión del aprobador"
            ),
            explicit_blocker,
        )

    if normalized in {"aprobado", "autorizado"}:
        is_payment_request = doc_type == "SOLICITUD"
        next_owner = (
            "Finanzas / Tesorería" if is_payment_request else "Responsable del flujo"
        )
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            "La autorización está registrada; esto todavía no prueba el pago.",
            next_owner,
            (
                "Incluir en el siguiente corte de pagos"
                if is_payment_request
                else "Continuar con la siguiente acción autorizada"
            ),
            explicit_blocker,
        )

    if normalized == "en_proceso_pago":
        if has_payment_proof:
            why_here = "El comprobante está disponible y el pago sigue en proceso."
            default_blocker = "Confirmación final de pago pendiente"
        else:
            why_here = "El documento forma parte de un corte; esto no prueba el pago."
            default_blocker = "Comprobante de pago pendiente"
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            why_here,
            "Confirmador de pago autorizado",
            "Cargar o revisar el comprobante de pago",
            explicit_blocker or default_blocker,
        )

    if normalized == "pagado":
        if has_payment_proof and has_payment_timestamp:
            why_here = "Hay fecha de pago y comprobante registrados."
            blocker = explicit_blocker
        elif has_payment_proof:
            why_here = "Hay un comprobante de pago registrado."
            blocker = explicit_blocker or "Fecha de pago visible no disponible"
        elif has_payment_timestamp:
            why_here = "Hay una fecha de pago registrada."
            blocker = explicit_blocker or "Comprobante de pago no disponible"
        else:
            why_here = (
                "El estado indica pago, pero la evidencia visible es " "incompleta."
            )
            blocker = explicit_blocker or "Evidencia visible de pago no disponible"
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            why_here,
            "Sin responsable pendiente",
            "Sin acción operativa pendiente",
            blocker,
        )

    if normalized in {"cancelado", "cerrado", "liquidado", "reembolsado"}:
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            "El ciclo ya no tiene una acción operativa pendiente.",
            "Sin responsable pendiente",
            "Sin acción operativa pendiente",
            explicit_blocker,
        )

    return WorkflowGuidance(
        visual.label,
        visual.note,
        visual.semantic,
        "No hay orientación configurada para este estado.",
        "No determinado",
        "No determinado",
        explicit_blocker or "Estado sin orientación configurada",
    )


def build_payment_run_guidance(
    *,
    status: Optional[str],
    can_close: bool,
    can_close_run: bool,
    can_upload_payment_proof: bool,
    can_confirm_payment: bool,
    amount_issue: Optional[str] = None,
) -> WorkflowGuidance:
    """Describe an existing Payment Run row from its explicit action flags."""

    normalized = _clean(status).lower()
    visual = payment_run_status_visual(status)
    blocker = _clean(amount_issue) or None

    if normalized in {"programada", "vencida", "aprobado"}:
        can_select = can_close and can_close_run and not blocker
        if blocker:
            next_action = "Resolver el bloqueo antes de seleccionar"
        elif can_select:
            next_action = "Seleccionar para el siguiente corte"
        else:
            next_action = "Esperar selección por un operador autorizado"
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            ("Está autorizada, pero todavía no forma parte de un corte " "cerrado."),
            "Finanzas / Tesorería",
            next_action,
            blocker,
        )

    if normalized in {"cerrada", "en proceso de pago", "en_proceso_pago"}:
        can_handle_proof = can_upload_payment_proof and can_confirm_payment
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            "Ya forma parte de un corte cerrado; esto no prueba el pago.",
            (
                "Confirmador de pago autorizado (tú)"
                if can_handle_proof
                else "Confirmador de pago autorizado"
            ),
            (
                "Cargar o revisar el comprobante"
                if can_handle_proof
                else "Esperar comprobante y confirmación"
            ),
            blocker or "Comprobante de pago pendiente",
        )

    if normalized in {"pagada", "pagado"}:
        return WorkflowGuidance(
            visual.label,
            visual.note,
            visual.semantic,
            "El pago está registrado en el flujo canónico.",
            "Sin responsable pendiente",
            "Sin acción operativa pendiente",
            blocker,
        )

    return WorkflowGuidance(
        visual.label,
        visual.note,
        visual.semantic,
        "No hay orientación configurada para este estado de Payment Run.",
        "No determinado",
        "No determinado",
        blocker or "Estado sin orientación configurada",
    )
