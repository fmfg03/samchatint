from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services.cuenta_contable_suggester import CuentaContableSuggester


class _ScalarListResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_amex_catalog_mapping_wins_for_card_last4(monkeypatch):
    cuenta = SimpleNamespace(id=uuid4(), codigo="2120-002-064", nombre="AMEX FGV")
    session = SimpleNamespace(execute=AsyncMock(return_value=_ScalarListResult([cuenta])))

    async def fake_find_by_last4(_session, last4):
        assert last4 == "4321"
        return SimpleNamespace(
            last4="4321",
            liability_cuenta_contable=cuenta,
        )

    monkeypatch.setattr(
        "devnous.gastos.services.amex_card_account_service.find_amex_card_account_by_last4",
        fake_find_by_last4,
    )

    suggestion = await CuentaContableSuggester(session).get_suggestion(
        expense_id=uuid4(),
        concepto="HOTEL",
        metodo_pago="TARJETA CREDITO AMEX",
        origen="amex_batch",
        ultimos_4_digitos="4321",
        use_llm=False,
    )

    assert suggestion is not None
    assert suggestion.tier == "amex_catalog"
    assert suggestion.cuenta_codigo == "2120-002-064"
    assert "****4321" in suggestion.reason
