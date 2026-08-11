from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services.amex_fee_interest_service import (
    AMEX_FEE_INTEREST_CONCEPT,
    AmexFeeInterestError,
    apply_amex_fee_interest_p1218,
)


class _ScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = (
            values if values is not None else ([] if value is None else [value])
        )

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_apply_amex_fee_interest_p1218_classifies_selected_expenses():
    cuenta_id = uuid4()
    expense_id = uuid4()
    budget_id = uuid4()
    cuenta_contable_id = uuid4()
    added = []
    cuenta = SimpleNamespace(id=cuenta_id, torneo_id=uuid4())
    budget = SimpleNamespace(
        id=budget_id,
        cuenta_contable_id=cuenta_contable_id,
        concept_key="P1218",
        concept_name="P1218 Comisiones e intereses",
    )
    cuenta_contable = SimpleNamespace(
        id=cuenta_contable_id, codigo="5300-121-800", activo=True
    )
    expense = SimpleNamespace(
        id=expense_id,
        cuenta_gastos_id=cuenta_id,
        estado_gasto="activo",
        concepto="Cargo banco",
        budget_concept_id=None,
        cuenta_contable_id=None,
        pagado_con_amex_empresa=False,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(cuenta),
                _ScalarResult(budget),
                _ScalarResult(cuenta_contable),
                _ScalarResult(values=[expense]),
            ]
        ),
        add=lambda obj: added.append(obj),
    )
    actor = SimpleNamespace(id=uuid4(), rol="finanzas")

    result = await apply_amex_fee_interest_p1218(
        session,
        cuenta_id=cuenta_id,
        expense_ids=[expense_id],
        actor=actor,
    )

    assert result.expenses == [expense]
    assert expense.pagado_con_amex_empresa is True
    assert expense.concepto == AMEX_FEE_INTEREST_CONCEPT
    assert expense.budget_concept_id == budget_id
    assert expense.cuenta_contable_id == cuenta_contable_id
    assert len(added) == 1
    assert "P1218" in added[0].comentario


@pytest.mark.asyncio
async def test_apply_amex_fee_interest_p1218_requires_finance_role():
    session = SimpleNamespace(execute=AsyncMock())
    actor = SimpleNamespace(id=uuid4(), rol="empleado")

    with pytest.raises(AmexFeeInterestError):
        await apply_amex_fee_interest_p1218(
            session,
            cuenta_id=uuid4(),
            expense_ids=[uuid4()],
            actor=actor,
        )

    session.execute.assert_not_called()
