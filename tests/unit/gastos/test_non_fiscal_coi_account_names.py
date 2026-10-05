"""All cataloged non-deductible accounts share the no-CFDI COI exception."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from devnous.gastos.routes.admin_routes import (
    _allows_coi_without_cfdi,
    _allows_coi_without_cfdi_name,
)
from devnous.gastos.services import (
    expense_accounting_cleanup_service as cleanup,
)
from devnous.gastos.services.expense_accounting_cleanup_service import (
    _allows_cleanup_without_cfdi,
)
from devnous.gastos.services.expense_coi_export_service import (
    allows_coi_without_cfdi,
)


@pytest.mark.parametrize(
    "name",
    [
        "SIN REQUISITOS FISCALES",
        "NO DEDUCIBLE",
        "GASTOS NO DEDUCIBLES",
        "GASTOS NO DEDUCIBLES CTT",
        "GASTOS NO DEDUCIBLES DCC",
        "GASTOS NO DEDUCIBLES DEL CUTT",
        "GASTOS NO DEDUCIBLES DE MD",
        "GASTOS NO DEDUCIBLES LTB",
        "GASTOS NO DEDUCIBLES HWC",
    ],
)
def test_cataloged_non_fiscal_names_are_allowed_across_coi_paths(name):
    account = SimpleNamespace(nombre=name)
    expense = SimpleNamespace(cuenta_contable=account)

    assert _allows_cleanup_without_cfdi(expense)
    assert allows_coi_without_cfdi(account)
    assert _allows_coi_without_cfdi(account)
    assert _allows_coi_without_cfdi_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "GASTOS DE VIAJE ALIMENTOS",
        "GASTOS NO DEDUCIBLES SIN CATALOGAR",
        "GASTOS NO DEDUCIBLES CTT EXTRA",
        "",
    ],
)
def test_other_names_keep_the_cfdi_requirement(name):
    account = SimpleNamespace(nombre=name)
    expense = SimpleNamespace(cuenta_contable=account)

    assert not _allows_cleanup_without_cfdi(expense)
    assert not allows_coi_without_cfdi(account)
    assert not _allows_coi_without_cfdi(account)
    assert not _allows_coi_without_cfdi_name(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "expected_status"),
    [
        ("GASTOS NO DEDUCIBLES CTT", "Listo COI"),
        ("GASTOS DE VIAJE ALIMENTOS", "Pendiente"),
    ],
)
async def test_cleanup_readiness_uses_cataloged_non_fiscal_account(
    monkeypatch, name, expected_status
):
    expense = SimpleNamespace(
        cuenta_contable=SimpleNamespace(nombre=name),
        cuenta_contable_id="account-id",
        contra_cuenta_contable_id="counterpart-id",
        cfdi_report_id=None,
        cfdi_uuid_manual="manual-uuid-is-not-a-linked-cfdi",
    )
    monkeypatch.setattr(
        cleanup, "resolve_effective_budget_concept", lambda _: None
    )
    monkeypatch.setattr(
        cleanup,
        "build_expense_accounting_preview",
        AsyncMock(
            return_value={"taxes": {}, "contra_account": {"codigo": "2120"}}
        ),
    )

    state = await cleanup.build_cleanup_preview(None, expense)

    assert state["status"] == expected_status
    assert ("Falta CFDI vinculado" in state["issues"]) == (
        expected_status == "Pendiente"
    )
