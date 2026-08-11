from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.models import AmexCardAccount
from devnous.gastos.services import amex_card_account_service as svc


class _ScalarResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = values if values is not None else ([] if value is None else [value])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values


def test_normalize_amex_last4_requires_four_digits():
    assert svc.normalize_amex_last4("**** 1234") == "1234"
    with pytest.raises(svc.AmexCardAccountError) as exc:
        svc.normalize_amex_last4("123")
    assert exc.value.code == "invalid_last4"


@pytest.mark.asyncio
async def test_upsert_amex_card_account_creates_mapping_with_active_liability_account():
    cuenta_id = uuid4()
    cuenta = SimpleNamespace(id=cuenta_id, activo=True, codigo="2120-002-062")
    added = []

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[_ScalarResult(cuenta), _ScalarResult(None)]),
        add=lambda obj: added.append(obj),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await svc.upsert_amex_card_account(
        session,
        svc.AmexCardAccountInput(
            card_label="AMEX FGV",
            cardholder_key="fgv",
            cardholder_name="Federico González",
            last4="1234",
            liability_cuenta_contable_id=cuenta_id,
            notes="principal",
        ),
    )

    assert isinstance(result, AmexCardAccount)
    assert result.last4 == "1234"
    assert result.cardholder_key == "FGV"
    assert result.liability_cuenta_contable_id == cuenta_id
    assert result.active is True
    assert added == [result]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_amex_card_account_rejects_inactive_missing_liability_account():
    session = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))

    with pytest.raises(svc.AmexCardAccountError) as exc:
        await svc.upsert_amex_card_account(
            session,
            svc.AmexCardAccountInput(
                card_label="AMEX LAO",
                cardholder_key="LAO",
                last4="9999",
                liability_cuenta_contable_id=uuid4(),
            ),
        )

    assert exc.value.code == "invalid_liability_account"
