from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.services import loan_accounting_service
from devnous.gastos.services.loan_accounting_service import (
    assign_prestamo_debtor_account,
    ensure_prestamo_abono_posting,
    ensure_prestamo_payment_posting,
    is_valid_prestamo_debtor_account,
)
from devnous.gastos.services.loan_request_service import (
    PrestamoWorkflowPermissionError,
    PrestamoWorkflowValidationError,
)


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    async def execute(self, _query):
        return _ScalarResult()

    async def get(self, _model, _entity_id):
        return None


def _account(code: str):
    return SimpleNamespace(id=uuid4(), codigo=code, nombre=code, activo=True)


async def _capture_poliza(captured, **kwargs):
    captured.append(kwargs)
    return SimpleNamespace(id=uuid4(), **kwargs)


@pytest.mark.asyncio
async def test_loan_payment_posting_debits_debtor_and_credits_santander(
    monkeypatch,
) -> None:
    debtor = _account("1170-001-016")
    bank = _account("1120-001-001")
    captured = []
    prestamo = SimpleNamespace(
        id=uuid4(),
        estado="pagada",
        numero_referencia="PRE-26000001",
        monto_solicitado=Decimal("1500.00"),
        pagado_en=datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
        beneficiario_tipo="propio",
        beneficiario_nombre_snapshot="Juan Pablo Lopez",
        cuenta_deudor_contable_id=None,
        banco_cuenta_contable_id=None,
    )

    monkeypatch.setattr(
        loan_accounting_service,
        "resolve_prestamo_debtor_account",
        lambda session, prestamo: _async_value(debtor),
    )
    monkeypatch.setattr(
        loan_accounting_service,
        "resolve_default_bank_account",
        lambda session: _async_value(bank),
    )
    monkeypatch.setattr(
        loan_accounting_service,
        "_create_poliza",
        lambda *args, **kwargs: _capture_poliza(captured, **kwargs),
    )

    result = await ensure_prestamo_payment_posting(
        _FakeSession(),
        prestamo=prestamo,
    )

    assert result.status == "created"
    assert captured[0]["origen"] == "prestamo_pago"
    assert captured[0]["lines"][0]["cuenta_codigo"] == debtor.codigo
    assert captured[0]["lines"][0]["debe"] == Decimal("1500.00")
    assert captured[0]["lines"][1]["cuenta_codigo"] == bank.codigo
    assert captured[0]["lines"][1]["haber"] == Decimal("1500.00")
    assert prestamo.cuenta_deudor_contable_id == debtor.id
    assert prestamo.banco_cuenta_contable_id == bank.id


@pytest.mark.asyncio
async def test_loan_repayment_posting_debits_santander_and_credits_debtor(
    monkeypatch,
) -> None:
    debtor = _account("1170-001-016")
    bank = _account("1120-001-001")
    captured = []
    prestamo = SimpleNamespace(
        id=uuid4(),
        estado="pagada",
        numero_referencia="PRE-26000002",
        beneficiario_tipo="propio",
        beneficiario_nombre_snapshot="Juan Pablo Lopez",
        cuenta_deudor_contable_id=None,
        banco_cuenta_contable_id=None,
    )
    abono = SimpleNamespace(
        id=uuid4(),
        estado="aprobado",
        prestamo=prestamo,
        monto_aplicado=Decimal("500.00"),
        aprobado_en=datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
    )

    monkeypatch.setattr(
        loan_accounting_service,
        "resolve_prestamo_debtor_account",
        lambda session, prestamo: _async_value(debtor),
    )
    monkeypatch.setattr(
        loan_accounting_service,
        "resolve_default_bank_account",
        lambda session: _async_value(bank),
    )
    monkeypatch.setattr(
        loan_accounting_service,
        "_create_poliza",
        lambda *args, **kwargs: _capture_poliza(captured, **kwargs),
    )

    result = await ensure_prestamo_abono_posting(
        _FakeSession(),
        abono=abono,
    )

    assert result.status == "created"
    assert captured[0]["origen"] == "prestamo_abono"
    assert captured[0]["lines"][0]["cuenta_codigo"] == bank.codigo
    assert captured[0]["lines"][0]["debe"] == Decimal("500.00")
    assert captured[0]["lines"][1]["cuenta_codigo"] == debtor.codigo
    assert captured[0]["lines"][1]["haber"] == Decimal("500.00")


@pytest.mark.asyncio
async def test_loan_posting_fails_closed_when_debtor_account_missing(
    monkeypatch,
) -> None:
    prestamo = SimpleNamespace(
        id=uuid4(),
        estado="pagada",
        numero_referencia="PRE-26000003",
        monto_solicitado=Decimal("1500.00"),
    )

    monkeypatch.setattr(
        loan_accounting_service,
        "resolve_prestamo_debtor_account",
        lambda session, prestamo: _async_value(None),
    )

    result = await ensure_prestamo_payment_posting(
        _FakeSession(),
        prestamo=prestamo,
    )

    assert result.status == "pending"
    assert result.reason == "missing_loan_debtor_account"


def test_accounting_assigns_only_valid_loan_debtor_accounts() -> None:
    prestamo = SimpleNamespace(
        beneficiario_tipo="proveedor",
        cuenta_deudor_contable_id=None,
        cuenta_deudor_contable=None,
    )
    provider_debtor = _account("1170-003-042")
    employee_debtor = _account("1170-001-016")
    accountant = SimpleNamespace(
        id=uuid4(),
        rol="contabilidad",
        departamento="Contabilidad",
    )

    assert is_valid_prestamo_debtor_account(prestamo, provider_debtor)
    assert not is_valid_prestamo_debtor_account(prestamo, employee_debtor)

    assign_prestamo_debtor_account(prestamo, accountant, provider_debtor)

    assert prestamo.cuenta_deudor_contable_id == provider_debtor.id
    assert prestamo.cuenta_deudor_contable == provider_debtor

    with pytest.raises(PrestamoWorkflowValidationError):
        assign_prestamo_debtor_account(prestamo, accountant, employee_debtor)
    with pytest.raises(PrestamoWorkflowPermissionError):
        assign_prestamo_debtor_account(
            prestamo,
            SimpleNamespace(id=uuid4(), rol="empleado"),
            provider_debtor,
        )


async def _async_value(value):
    return value
