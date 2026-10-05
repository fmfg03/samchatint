"""Fiscal acceptance tests; preview account resolution is an explicit seam."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_accounting_posting_service as service


def _account(code):
    return SimpleNamespace(id=uuid4(), codigo=code, activo=True)


def _expense(account, amount=116, cfdi=None):
    return SimpleNamespace(
        id=uuid4(),
        cuenta_contable=account,
        cuenta_contable_id=account.id,
        concepto="PASE TAG",
        gasto_cantidad=amount,
        cfdi_report=cfdi,
        cfdi_report_id=cfdi.id if cfdi else None,
        propina_no_deducible=0,
        hospedaje_impuesto_monto=0,
    )


def _mapped(account):
    return {"codigo": account.codigo, "cuenta_contable_id": account.id}


@pytest.mark.asyncio
async def test_fiscal_components_survive_single_consumption(monkeypatch):
    expense_account = _account("5300-010-001")
    iva, local, nd, retention = [
        _account(code)
        for code in ("1180-001-001", "5300-010-002", "5300-010-030", "2130-001-001")
    ]
    preview = {
        "taxes": {
            "base_gasto": 100,
            "neto_contrapartida": 129,
            "iva_trasladado": 16,
            "iva_account": _mapped(iva),
            "impuestos_locales": [{"importe": 3, "account": _mapped(local)}],
            "gastos_no_deducibles": [{"importe": 20, "account": _mapped(nd)}],
            "retenciones": [{"importe": 10, "account": _mapped(retention)}],
        }
    }
    monkeypatch.setattr(
        service, "build_expense_accounting_preview", AsyncMock(return_value=preview)
    )
    rows, net, reason = await service._fiscal_lines_for_expenses(
        AsyncMock(), [_expense(expense_account, 129)], meta={}
    )
    assert reason is None
    assert net == Decimal("129.00")
    assert {row["cuenta_codigo"] for row in rows} == {
        account.codigo for account in (expense_account, iva, local, nd, retention)
    }
    assert (
        sum(Decimal(str(row["debe"])) - Decimal(str(row["haber"])) for row in rows)
        == net
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component", ["iva_account", "local", "non_deductible", "retention"]
)
async def test_missing_fiscal_mapping_returns_no_partial_lines(monkeypatch, component):
    taxes = {"base_gasto": 100, "neto_contrapartida": 116}
    if component == "iva_account":
        taxes["iva_trasladado"] = 16
    else:
        key = {
            "local": "impuestos_locales",
            "non_deductible": "gastos_no_deducibles",
            "retention": "retenciones",
        }[component]
        taxes[key] = [{"importe": 16}]
    monkeypatch.setattr(
        service,
        "build_expense_accounting_preview",
        AsyncMock(return_value={"taxes": taxes}),
    )
    rows, net, reason = await service._fiscal_lines_for_expenses(
        AsyncMock(), [_expense(_account("5300-010-001"))], meta={}
    )
    assert rows is None
    assert net == 0
    assert reason.startswith("missing_")


@pytest.mark.asyncio
async def test_shared_pase_cfdi_is_not_multiplied_by_number_of_charges(monkeypatch):
    account, iva = _account("5300-010-001"), _account("1180-001-001")
    cfdi = SimpleNamespace(id=uuid4(), total=116)
    expenses = [_expense(account, 58, cfdi), _expense(account, 58, cfdi)]
    preview = AsyncMock(
        return_value={"taxes": {"iva_trasladado": 16, "iva_account": _mapped(iva)}}
    )
    monkeypatch.setattr(service, "build_expense_accounting_preview", preview)
    monkeypatch.setattr(service, "is_pase_expense", lambda expense: True)
    rows, net, reason = await service._fiscal_lines_for_expenses(
        AsyncMock(get=AsyncMock(return_value=account)), expenses, meta={}
    )
    assert reason is None
    assert net == Decimal("116.00")
    assert sum(row["debe"] for row in rows) == net
    assert [row["debe"] for row in rows] == [Decimal("100.00"), Decimal("16.00")]
    assert preview.await_count == 1


@pytest.mark.asyncio
async def test_shared_pase_cfdi_incomplete_amount_coverage_fails_closed(monkeypatch):
    account = _account("5300-010-001")
    cfdi = SimpleNamespace(id=uuid4(), total=116)
    monkeypatch.setattr(service, "is_pase_expense", lambda expense: True)
    rows, net, reason = await service._fiscal_lines_for_expenses(
        AsyncMock(), [_expense(account, 58, cfdi), _expense(account, 57, cfdi)], meta={}
    )
    assert rows is None and net == 0
    assert reason.startswith("pase_cfdi_total_mismatch:")
