import pytest

from devnous.gastos.workflow_guidance import (
    build_document_workflow_guidance,
    build_payment_run_guidance,
)


def test_rejected_document_preserves_recorded_reason_and_actor() -> None:
    guidance = build_document_workflow_guidance(
        state="rechazado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        rejection_reason="Falta la orden de compra",
        rejection_actor="Aprobador de Operaciones",
    )

    assert guidance.status_label == "Rechazado"
    assert guidance.next_owner == "Solicitante"
    assert guidance.next_action == "Corregir y reenviar"
    assert guidance.blocker == "Falta la orden de compra"
    assert "Aprobador de Operaciones" in guidance.why_here


def test_rejected_document_does_not_invent_missing_reason() -> None:
    guidance = build_document_workflow_guidance(
        state="rechazado",
        document_type="INFORME",
        is_owner=False,
        can_approve_or_reject=False,
    )

    assert guidance.blocker == "Motivo de rechazo no disponible"
    assert guidance.next_action == "Esperar corrección del solicitante"


def test_sent_document_distinguishes_current_approver_from_owner() -> None:
    approver = build_document_workflow_guidance(
        state="enviado",
        document_type="SOLICITUD",
        is_owner=False,
        can_approve_or_reject=True,
    )
    owner = build_document_workflow_guidance(
        state="enviado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
    )

    assert approver.next_owner == "Aprobador autorizado (tú)"
    assert approver.next_action == "Revisar evidencia y decidir"
    assert owner.next_owner == "Aprobador configurado"
    assert owner.next_action == "Esperar la decisión del aprobador"


def test_document_states_keep_next_owner_and_blocker_explicit() -> None:
    cases = {
        "borrador": ("Solicitante", None),
        "control_presupuestal": (
            "Control Presupuestal",
            "Asignación presupuestal pendiente",
        ),
        "aprobado": ("Finanzas / Tesorería", None),
        "en_proceso_pago": (
            "Confirmador de pago autorizado",
            "Comprobante de pago pendiente",
        ),
        "cancelado": ("Sin responsable pendiente", None),
    }

    for state, (owner, blocker) in cases.items():
        guidance = build_document_workflow_guidance(
            state=state,
            document_type="SOLICITUD",
            is_owner=True,
            can_approve_or_reject=False,
        )
        assert guidance.next_owner == owner
        assert guidance.blocker == blocker


def test_paid_document_describes_only_visible_payment_evidence() -> None:
    complete = build_document_workflow_guidance(
        state="pagado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        has_payment_timestamp=True,
        has_payment_proof=True,
    )
    incomplete = build_document_workflow_guidance(
        state="pagado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
    )
    missing_date = build_document_workflow_guidance(
        state="pagado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        has_payment_proof=True,
    )
    missing_proof = build_document_workflow_guidance(
        state="pagado",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        has_payment_timestamp=True,
    )

    assert complete.why_here == "Hay fecha de pago y comprobante registrados."
    assert complete.blocker is None
    assert missing_date.blocker == "Fecha de pago visible no disponible"
    assert missing_proof.blocker == "Comprobante de pago no disponible"
    assert incomplete.blocker == "Evidencia visible de pago no disponible"


def test_unknown_document_state_fails_closed() -> None:
    guidance = build_document_workflow_guidance(
        state="pendiente_nuevo",
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
    )

    assert guidance.next_owner == "No determinado"
    assert guidance.next_action == "No determinado"
    assert guidance.blocker == "Estado sin orientación configurada"


def test_payment_run_guidance_separates_cutoff_from_payment() -> None:
    scheduled = build_payment_run_guidance(
        status="programada",
        can_close=True,
        can_close_run=True,
        can_upload_payment_proof=False,
        can_confirm_payment=False,
    )
    in_process = build_payment_run_guidance(
        status="en proceso de pago",
        can_close=False,
        can_close_run=False,
        can_upload_payment_proof=True,
        can_confirm_payment=True,
    )
    overdue = build_payment_run_guidance(
        status="vencida",
        can_close=True,
        can_close_run=True,
        can_upload_payment_proof=False,
        can_confirm_payment=False,
    )

    assert scheduled.next_action == "Seleccionar para el siguiente corte"
    assert overdue.next_action == "Seleccionar para el siguiente corte"
    assert "todavía no forma parte" in scheduled.why_here
    assert "esto no prueba el pago" in in_process.why_here
    assert in_process.next_action == "Cargar o revisar el comprobante"


def test_payment_run_amount_issue_blocks_selection_guidance() -> None:
    guidance = build_payment_run_guidance(
        status="programada",
        can_close=True,
        can_close_run=True,
        can_upload_payment_proof=False,
        can_confirm_payment=False,
        amount_issue="Monto final no disponible",
    )

    assert guidance.next_action == "Resolver el bloqueo antes de seleccionar"
    assert guidance.blocker == "Monto final no disponible"


@pytest.mark.parametrize(
    "state,proof,timestamp,blocker",
    [
        ("aprobado", False, False, None),
        ("autorizado", False, False, None),
        ("en_proceso_pago", False, False, "Comprobante de pago pendiente"),
        ("en_proceso_pago", True, False, "Confirmación final de pago pendiente"),
        ("pagado", True, True, None),
        ("pagado", False, True, "Comprobante de pago no disponible"),
    ],
)
def test_edit_lock_does_not_block_the_next_payment_action(
    state, proof, timestamp, blocker
):
    guidance = build_document_workflow_guidance(
        state=state,
        document_type="SOLICITUD",
        is_owner=True,
        can_approve_or_reject=False,
        locked_reason="autorizacion registrada",
        has_payment_proof=proof,
        has_payment_timestamp=timestamp,
    )
    assert guidance.blocker == blocker
