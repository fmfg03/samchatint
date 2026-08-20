from types import SimpleNamespace

from devnous.gastos.routes.user_routes import _derive_informe_operational_status


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
