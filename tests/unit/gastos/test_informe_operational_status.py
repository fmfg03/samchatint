from types import SimpleNamespace

from uuid import uuid4

from devnous.gastos.routes.user_routes import (
    _derive_informe_operational_status,
    _informe_reembolso_payment_state,
    _informe_reembolso_proof_links,
)


def _status(**overrides):
    payload = {
        "cuenta_estado": "abierta",
        "informe_estado": "borrador",
        "solicitudes": [],
        "num_expenses": 0,
        "total_amex": 0.0,
        "total_pagado_empleado": 0.0,
        "monto_entregado": 0.0,
        "saldo": 0.0,
        "settlement_count": 0,
    }
    payload.update(overrides)
    return _derive_informe_operational_status(**payload)[0]


def test_informe_operational_status_uses_business_state_not_capture_state():
    assert _status(cuenta_estado="abierta", informe_estado="borrador") == "Borrador"
    assert _status(cuenta_estado="cerrada", informe_estado="enviado") == "En aprobaci\u00f3n"
    assert _status(cuenta_estado="cerrada", informe_estado="aprobado", saldo=-100) == "Autorizado"


def test_informe_operational_status_marks_paid_after_financial_settlement():
    assert _status(
        cuenta_estado="cerrada",
        informe_estado="aprobado",
        num_expenses=2,
        total_pagado_empleado=500,
        saldo=0,
        settlement_count=1,
    ) == "Pagado"


def test_informe_operational_status_marks_checked_when_approved_and_balanced():
    assert _status(
        cuenta_estado="cerrada",
        informe_estado="aprobado",
        num_expenses=2,
        total_pagado_empleado=500,
        monto_entregado=500,
        saldo=0,
    ) == "Comprobado"


def test_informe_operational_status_marks_amex_report_as_checked_when_approved_and_balanced():
    assert _status(
        cuenta_estado="cerrada",
        informe_estado="aprobado",
        num_expenses=2,
        total_amex=700,
        saldo=0,
    ) == "Comprobado"


def test_informe_operational_status_uses_paid_linked_solicitud_signal():
    paid_solicitud = SimpleNamespace(estado="pagado", pagado_en=None)

    assert _status(
        cuenta_estado="cerrada",
        informe_estado="aprobado",
        solicitudes=[paid_solicitud],
        monto_entregado=100,
        saldo=0,
    ) == "Comprobado"


def test_informe_operational_status_cancelled_stays_terminal():
    assert _status(cuenta_estado="cerrada", informe_estado="rechazado") == "Cancelado"


def test_reembolso_status_does_not_confuse_authorization_with_payment():
    pending = SimpleNamespace(concepto_pago="Reembolso de saldo a favor — I-167", estado="aprobado")
    paid = SimpleNamespace(concepto_pago=pending.concepto_pago, estado="pagado")
    cancelled = SimpleNamespace(concepto_pago=pending.concepto_pago, estado="cancelado")
    unrelated = SimpleNamespace(concepto_pago="Anticipo", estado="pagado")

    assert _informe_reembolso_payment_state(
        solicitudes=[pending, unrelated], reembolsos=[], saldo=-995.98
    ) == ("pendiente", pending, None)
    assert _informe_reembolso_payment_state(
        solicitudes=[cancelled], reembolsos=[], saldo=-995.98
    ) == ("sin_solicitud", None, None)
    assert _informe_reembolso_payment_state(
        solicitudes=[paid], reembolsos=[], saldo=0
    ) == ("pagado", paid, None)
    assert _informe_reembolso_payment_state(
        solicitudes=[unrelated], reembolsos=[], saldo=0
    ) == ("no_aplica", None, None)


def test_direct_reembolso_payment_and_proof_are_visible():
    cuenta_id, doc_id, settlement_id, proof_id, unrelated_id = (uuid4() for _ in range(5))
    document = SimpleNamespace(id=doc_id)
    settlement = SimpleNamespace(id=settlement_id, tipo="reembolso", estado="pagado")
    state, selected_doc, selected_settlement = _informe_reembolso_payment_state(
        solicitudes=[], reembolsos=[settlement], saldo=0
    )
    assert (state, selected_doc, selected_settlement) == ("pagado", None, settlement)

    links = _informe_reembolso_proof_links(
        cuenta_id=cuenta_id,
        documento=document,
        settlement=settlement,
        documento_metas={doc_id: [
            SimpleNamespace(id=unrelated_id, categoria="supporting"),
            SimpleNamespace(id=proof_id, categoria="comprobante_pago"),
        ]},
        settlement_metas={settlement_id: [SimpleNamespace(id=proof_id)]},
    )
    assert len(links) == 2
    assert f"/documentos/{doc_id}/adjuntos/{proof_id}" in links[0]
    assert str(unrelated_id) not in " ".join(links)
    assert f"/informes-de-gastos/{cuenta_id}/reembolsos/{settlement_id}/adjuntos/{proof_id}" in links[1]
