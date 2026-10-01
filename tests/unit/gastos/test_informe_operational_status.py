from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.routes.user_routes import (
    _derive_informe_operational_status,
    _informe_reembolso_payment_state,
    _informe_reembolso_proof_links,
    _render_informe_reembolso_payment_html,
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


def test_reembolso_detail_panel_distinguishes_pending_paid_and_missing_proof():
    cuenta_id, doc_id, settlement_id, proof_id = (uuid4() for _ in range(4))
    doc = SimpleNamespace(id=doc_id, numero_referencia="S-167")
    settlement = SimpleNamespace(id=settlement_id)
    kwargs = dict(
        cuenta_id=cuenta_id,
        documento_metas={doc_id: [SimpleNamespace(id=proof_id, categoria="comprobante_pago")]},
        settlement_metas={settlement_id: [SimpleNamespace(id=proof_id)]},
    )
    assert _render_informe_reembolso_payment_html(
        state="no_aplica", documento=None, settlement=None, **kwargs
    ) == ""
    pending = _render_informe_reembolso_payment_html(
        state="pendiente", documento=doc, settlement=None, **kwargs
    )
    assert "Pendiente de pago" in pending
    assert "S-167" in pending
    assert str(proof_id) not in pending
    paid = _render_informe_reembolso_payment_html(
        state="pagado", documento=doc, settlement=None, **kwargs
    )
    assert f"/documentos/{doc_id}/adjuntos/{proof_id}" in paid
    direct = _render_informe_reembolso_payment_html(
        state="pagado", documento=None, settlement=settlement, **kwargs
    )
    assert f"/reembolsos/{settlement_id}/adjuntos/{proof_id}" in direct
    without_proof = _render_informe_reembolso_payment_html(
        state="pagado", documento=doc, settlement=None,
        cuenta_id=cuenta_id, documento_metas={}, settlement_metas={},
    )
    assert "Comprobante pendiente de adjuntar" in without_proof
    without_request = _render_informe_reembolso_payment_html(
        state="sin_solicitud", documento=None, settlement=None, **kwargs
    )
    assert "sin solicitud registrada" in without_request


def _rows(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("blocking_count", [0, 1])
async def test_empty_informe_list_cancellation_with_historical_requests(
    monkeypatch, blocking_count
):
    """Render the real list: terminal history must not hide safe draft cleanup."""
    cuenta_id, informe_id, solicitud_id = (uuid4() for _ in range(3))
    actor = SimpleNamespace(
        id=uuid4(), rol="finanzas", nombre="Finanzas", departamento="Finanzas"
    )
    empleado = SimpleNamespace(id=uuid4(), nombre="Paulina", aprobador=None)
    cuenta = SimpleNamespace(
        id=cuenta_id, empleado_id=empleado.id, empleado=empleado,
        beneficiario_empleado=None, beneficiario_proveedor_cliente=None,
        nombre="Informe vacío", referencia_base="376361", estado="abierta",
        created_at=datetime(2026, 9, 30), currency="MXN", torneo=None,
        torneo_id=None, fase=None, tipo_cuenta=None, proyecto=None,
        motivo_gasto=None, descripcion=None, tipo_gasto=None,
    )
    informe = SimpleNamespace(
        id=informe_id, cuenta_gastos_id=cuenta_id, tipo="INFORME",
        estado="borrador", aprobado_en=None,
        numero_referencia="I-376361", referencia_operaciones=None,
    )
    solicitud = SimpleNamespace(
        id=solicitud_id, concepto_pago="Reembolso de saldo a favor — I-376361",
        estado="cancelado", pagado_en=None,
        numero_referencia="S-371", monto_solicitado=2058.25,
        currency="MXN", creado_en=datetime(2026, 9, 30),
    )
    count_result = MagicMock()
    count_result.scalar_one.return_value = blocking_count
    session = AsyncMock()
    session.execute.side_effect = [
        _rows([cuenta]), _rows([informe]), _rows([]),
        _rows([]), _rows([solicitud]), count_result,
    ]
    monkeypatch.setattr(user_routes, "_can_view_all_cuentas_de_gastos", lambda *_: True)
    monkeypatch.setattr(user_routes, "empleado_list_view_department_scope", lambda *_: None)
    monkeypatch.setattr(
        user_routes, "compute_cuenta_saldo_adjustments", AsyncMock(return_value=(0, 0))
    )
    monkeypatch.setattr(user_routes, "calculate_informe_expense_totals", lambda *_: SimpleNamespace(
        total_reported=0, company_amex=0, employee_paid=0
    ))
    monkeypatch.setattr(user_routes, "fetch_documento_adjuntos_meta_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(user_routes, "fetch_reembolso_adjuntos_meta_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_: "")
    monkeypatch.setattr(user_routes, "_gastos_workspace_nav_html", lambda *_: "")

    html = await user_routes.cuentas_de_gastos_list(
        request=SimpleNamespace(query_params={}), session=session,
        current_empleado=actor, q=None, estado=None, reembolso=None,
        empleado_nombre=None, torneo_nombre=None,
    )
    assert "I-376361" in html
    assert ('>Cancelar borrador</button>' in html) == (blocking_count == 0)
    assert (f'/informes-de-gastos/{cuenta_id}/cancelar-borrador' in html) == (blocking_count == 0)
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("paid", [False, True])
async def test_informes_list_shows_reimbursement_payment_and_proof(monkeypatch, paid):
    cuenta_id, informe_id, solicitud_id, proof_id = (uuid4() for _ in range(4))
    actor = SimpleNamespace(id=uuid4(), rol="finanzas", nombre="Finanzas", departamento="Finanzas")
    empleado = SimpleNamespace(id=uuid4(), nombre="Alicia", aprobador=None)
    cuenta = SimpleNamespace(
        id=cuenta_id, empleado_id=empleado.id, empleado=empleado,
        beneficiario_empleado=None, beneficiario_proveedor_cliente=None,
        nombre="Reembolso varios", referencia_base="700715", estado="cerrada",
        created_at=datetime(2026, 9, 15), currency="MXN", torneo=None,
        torneo_id=None, fase=None, tipo_cuenta=None, proyecto=None,
        motivo_gasto=None, descripcion=None, tipo_gasto=None,
    )
    informe = SimpleNamespace(
        id=informe_id, cuenta_gastos_id=cuenta_id, tipo="INFORME",
        estado="aprobado", aprobado_en=datetime(2026, 9, 16),
        numero_referencia="I-700715", referencia_operaciones="167",
    )
    solicitud = SimpleNamespace(
        id=solicitud_id, concepto_pago="Reembolso de saldo a favor — I-700715",
        estado="pagado" if paid else "aprobado", pagado_en=None,
        numero_referencia="S-700715", monto_solicitado=100,
        currency="MXN", creado_en=datetime(2026, 9, 16),
    )
    session = AsyncMock()
    monkeypatch.setattr(user_routes, "_can_view_all_cuentas_de_gastos", lambda *_: True)
    monkeypatch.setattr(user_routes, "empleado_list_view_department_scope", lambda *_: None)
    monkeypatch.setattr(user_routes, "compute_cuenta_saldo_adjustments", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(user_routes, "calculate_informe_expense_totals", lambda *_: SimpleNamespace(
        total_reported=100, company_amex=0, employee_paid=100
    ))
    monkeypatch.setattr(user_routes, "fetch_documento_adjuntos_meta_batch", AsyncMock(return_value={
        solicitud_id: [SimpleNamespace(id=proof_id, categoria="comprobante_pago")]
    }))
    monkeypatch.setattr(user_routes, "fetch_reembolso_adjuntos_meta_batch", AsyncMock(return_value={}))
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_: "")
    monkeypatch.setattr(user_routes, "_gastos_workspace_nav_html", lambda *_: "")

    def reset_results():
        session.execute.side_effect = [
            _rows([cuenta]), _rows([informe]), _rows([]),
            _rows([SimpleNamespace(id=uuid4())]), _rows([solicitud]),
        ]

    reset_results()
    html = await user_routes.cuentas_de_gastos_list(
        request=SimpleNamespace(query_params={}), session=session,
        current_empleado=actor, q=None, estado=None, reembolso=None,
        empleado_nombre=None, torneo_nombre=None,
    )
    assert "Pago de reembolso / comprobante" in html
    assert ('Pagado' if paid else 'Pendiente de pago') in html
    if paid:
        assert f"/documentos/{solicitud_id}/adjuntos/{proof_id}" in html
    else:
        assert f"/documentos/{solicitud_id}/adjuntos/{proof_id}" not in html

    reset_results()
    filtered = await user_routes.cuentas_de_gastos_list(
        request=SimpleNamespace(query_params={}), session=session,
        current_empleado=actor, q=None, estado=None,
        reembolso="pendiente" if paid else "pagado",
        empleado_nombre=None, torneo_nombre=None,
    )
    assert "I-700715" not in filtered

    # A direct settlement paid by Finance exposes its own proof.
    settlement_id = uuid4()
    settlement = SimpleNamespace(
        id=settlement_id, cuenta_gastos_id=cuenta_id, documento_id=informe_id,
        tipo="reembolso", estado="pagado",
    )
    solicitud.estado = "aprobado"
    monkeypatch.setattr(user_routes, "fetch_reembolso_adjuntos_meta_batch", AsyncMock(return_value={
        settlement_id: [SimpleNamespace(id=proof_id)]
    }))
    session.execute.side_effect = [
        _rows([cuenta]), _rows([informe]), _rows([settlement]),
        _rows([SimpleNamespace(id=uuid4())]), _rows([solicitud]),
    ]
    direct = await user_routes.cuentas_de_gastos_list(
        request=SimpleNamespace(query_params={}), session=session,
        current_empleado=actor, q=None, estado=None, reembolso="pagado",
        empleado_nombre=None, torneo_nombre=None,
    )
    assert f"/informes-de-gastos/{cuenta_id}/reembolsos/{settlement_id}/adjuntos/{proof_id}" in direct
