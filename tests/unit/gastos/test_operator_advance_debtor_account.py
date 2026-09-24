from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.models import ProveedorCliente
from devnous.gastos.services import employee_debtor_accounting_service as accounting


class OperatorSession:
    def __init__(self, operator, accounts):
        self.operator = operator
        self.accounts = accounts

    async def get(self, model, identifier):
        assert model is ProveedorCliente
        assert identifier == self.operator.id
        return self.operator

    async def execute(self, query):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.accounts)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_accounts", [0, 1, 2])
async def test_operator_debtor_account_requires_unique_named_auxiliary(
    matching_accounts,
):
    operator = SimpleNamespace(
        id=uuid4(),
        nombre="YERALDIN STHEFANIA BUENDIA ROSAS",
        tipo="operadores_regionales",
        activo=True,
    )
    matching = [
        SimpleNamespace(
            codigo=f"1170-001-{index:03}", nombre="Yeraldin Sthefania Buendia Rosas"
        )
        for index in range(1, matching_accounts + 1)
    ]
    requester = SimpleNamespace(
        codigo="1170-001-005", nombre="Bibiana Raquel Roman Arguelles"
    )
    session = OperatorSession(operator, [requester, *matching])
    cuenta = SimpleNamespace(beneficiario_proveedor_cliente_id=operator.id)

    resolved = await accounting.resolve_cuenta_debtor_account(
        session, cuenta, requester
    )

    assert resolved is (matching[0] if matching_accounts == 1 else None)


@pytest.mark.asyncio
async def test_operator_advance_rejects_different_linked_beneficiary(monkeypatch):
    cuenta = SimpleNamespace(beneficiario_proveedor_cliente_id=uuid4())

    class Session:
        async def get(self, model, identifier):
            return cuenta

    monkeypatch.setattr(
        accounting,
        "_existing_event_poliza",
        lambda *args, **kwargs: pytest.fail(
            "beneficiary checked before existing posting"
        ),
    )
    result = await accounting.ensure_debtor_payment_posting_for_document(
        Session(),
        documento=SimpleNamespace(
            id=uuid4(),
            cuenta_gastos_id=uuid4(),
            proveedor_cliente_id=uuid4(),
            beneficiario_proveedor_cliente_id=uuid4(),
        ),
        empleado=SimpleNamespace(id=uuid4()),
        fecha_pago=accounting.date(2026, 9, 24),
        require_employee_beneficiary=False,
    )
    assert result.status == "pending"
    assert result.reason == "operator_beneficiary_mismatch"


@pytest.mark.asyncio
async def test_operator_advance_blocks_without_own_debtor_account(monkeypatch):
    operator_id = uuid4()
    cuenta = SimpleNamespace(beneficiario_proveedor_cliente_id=operator_id)

    class Session:
        async def get(self, model, identifier):
            return cuenta

    monkeypatch.setattr(
        accounting, "_existing_event_poliza", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        accounting, "resolve_cuenta_debtor_account", AsyncMock(return_value=None)
    )
    bank = AsyncMock(
        side_effect=AssertionError("no bank posting without debtor account")
    )
    monkeypatch.setattr(accounting, "resolve_default_bank_account", bank)
    result = await accounting.ensure_debtor_payment_posting_for_document(
        Session(),
        documento=SimpleNamespace(
            id=uuid4(),
            cuenta_gastos_id=uuid4(),
            proveedor_cliente_id=operator_id,
            beneficiario_proveedor_cliente_id=operator_id,
        ),
        empleado=SimpleNamespace(id=uuid4()),
        fecha_pago=accounting.date(2026, 9, 24),
        require_employee_beneficiary=False,
    )

    assert result.status == "pending"
    assert result.reason == "missing_operator_debtor_account"
    bank.assert_not_awaited()


@pytest.mark.asyncio
async def test_employee_bank_beneficiary_stays_on_employee_debtor_account(monkeypatch):
    employee_id = uuid4()
    cuenta = SimpleNamespace(
        beneficiario_empleado_id=employee_id,
        beneficiario_proveedor_cliente_id=uuid4(),
    )
    employee = SimpleNamespace(id=employee_id, nombre="Empleado beneficiario")
    employee_account = SimpleNamespace(codigo="1170-001-012", nombre=employee.nombre)

    class Session:
        async def get(self, model, identifier):
            if model is ProveedorCliente:
                pytest.fail("the linked employee bank record is not an operator")
            return cuenta

    resolve_employee = AsyncMock(return_value=employee_account)
    monkeypatch.setattr(accounting, "resolve_employee_debtor_account", resolve_employee)

    resolved = await accounting.resolve_cuenta_debtor_account(
        Session(), cuenta, employee
    )
    assert resolved is employee_account
    resolve_employee.assert_awaited_once()

    existing_poliza = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        accounting, "_existing_event_poliza", AsyncMock(return_value=existing_poliza)
    )
    result = await accounting.ensure_debtor_payment_posting_for_document(
        Session(),
        documento=SimpleNamespace(
            id=uuid4(),
            cuenta_gastos_id=uuid4(),
            beneficiario_empleado_id=employee_id,
            proveedor_cliente_id=None,
            beneficiario_proveedor_cliente_id=None,
        ),
        empleado=employee,
        fecha_pago=accounting.date(2026, 9, 24),
    )
    assert result.status == "exists"
    assert result.poliza is existing_poliza
