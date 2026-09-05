from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.services.payment_run_service import (
    DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS,
    DEFAULT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS,
    PaymentRunValidationError,
    _document_amount,
    can_confirm_payment_run_payment,
    can_manage_payment_run,
    configured_payment_run_payment_confirmer_ids,
    configured_payment_run_manager_ids,
    parse_payment_run_date,
)


def test_payment_run_access_allows_superadmin_without_allowlist() -> None:
    empleado = SimpleNamespace(id=uuid4(), rol="superadmin")

    assert can_manage_payment_run(empleado)


def test_payment_run_access_uses_empleado_id_allowlist(monkeypatch) -> None:
    empleado_id = uuid4()
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
        str(empleado_id),
    )

    assert can_manage_payment_run(
        SimpleNamespace(id=empleado_id, rol="finanzas")
    )
    assert not can_manage_payment_run(
        SimpleNamespace(id=uuid4(), rol="finanzas")
    )


def test_payment_run_manager_ids_accept_both_env_keys(monkeypatch) -> None:
    first = uuid4()
    second = uuid4()
    monkeypatch.setenv("SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS", str(first))
    monkeypatch.setenv("PAYMENT_RUN_MANAGER_EMPLOYEE_IDS", str(second))

    configured = configured_payment_run_manager_ids()
    assert str(first) in configured
    assert str(second) in configured
    assert DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS.issubset(configured)


def test_payment_run_access_allows_default_manager_id() -> None:
    empleado_id = next(iter(DEFAULT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS))

    assert can_manage_payment_run(
        SimpleNamespace(id=empleado_id, rol="finanzas")
    )


def test_parse_payment_run_date_from_iso_string() -> None:
    parsed = parse_payment_run_date("2026-07-31")

    assert parsed.isoformat() == "2026-07-31"


def test_payment_run_payment_confirmation_is_accounting_only() -> None:
    payment_confirmer_id = next(
        iter(DEFAULT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS)
    )

    assert can_confirm_payment_run_payment(
        SimpleNamespace(
            id=payment_confirmer_id,
            rol="finanzas",
            departamento="Finanzas",
        )
    )
    assert can_confirm_payment_run_payment(
        SimpleNamespace(id=uuid4(), rol="contabilidad")
    )
    assert can_confirm_payment_run_payment(
        SimpleNamespace(id=uuid4(), rol="usuario", departamento="Contabilidad")
    )
    assert can_confirm_payment_run_payment(
        SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            permissions={"contabilidad.pagos.marcar_pagado"},
        )
    )
    assert can_confirm_payment_run_payment(
        SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            permissions={"admin.contabilidad.*"},
        )
    )
    assert not can_confirm_payment_run_payment(
        SimpleNamespace(id=uuid4(), rol="finanzas", departamento="finanzas")
    )
    assert not can_confirm_payment_run_payment(
        SimpleNamespace(id=uuid4(), rol="operaciones", departamento="operaciones")
    )


def test_payment_run_payment_confirmer_ids_accept_env_key(monkeypatch) -> None:
    empleado_id = uuid4()
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS",
        str(empleado_id),
    )

    assert str(empleado_id) in configured_payment_run_payment_confirmer_ids()


def test_payment_run_reimbursement_requires_final_total() -> None:
    document = SimpleNamespace(
        id=uuid4(),
        numero_referencia="S-260005",
        concepto_pago="Reembolso de saldo a favor - I-235650",
        monto_total=None,
        monto_solicitado=56020,
    )

    with pytest.raises(PaymentRunValidationError, match="sin monto_total"):
        _document_amount(document)


def test_payment_run_ordinary_request_prefers_final_total() -> None:
    document = SimpleNamespace(
        concepto_pago="Pago a tercero",
        monto_total=120,
        monto_solicitado=150,
    )

    assert _document_amount(document) == 120
