"""Project-specific no-deductible accounting rules."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.services.cuenta_contable_suggester import CuentaContableSuggester
from devnous.gastos.services.expense_accounting_service import (
    _no_deducible_account_code_for_project,
    _resolve_project_no_deducible_account,
)


def _account(codigo: str, nombre: str = "No deducible", activo: bool = True):
    return SimpleNamespace(id=uuid4(), codigo=codigo, nombre=nombre, activo=activo)


@pytest.mark.parametrize(
    ("project_name", "expected_code"),
    [
        ("Copa Telmex Telcel de F\u00fatbol", "5300-010-030"),
        ("Gastos Administrativos - Administraci\u00f3n y Finanzas", "5200-015-001"),
        ("Homeless World Cup M\u00e9xico", "5300-016-033"),
        ("Promoci\u00f3n de Negocios", "5100-015-001"),
    ],
)
def test_no_deducible_project_rule_normalizes_names(project_name, expected_code):
    assert _no_deducible_account_code_for_project(project_name) == expected_code


def test_no_deducible_project_rule_allows_descriptive_suffixes():
    assert (
        _no_deducible_account_code_for_project("Copa Telmex Telcel de F\u00fatbol 2026")
        == "5300-010-030"
    )


@pytest.mark.asyncio
async def test_suggester_prioritizes_project_no_deducible_when_cfdi_is_missing():
    cuenta = _account("5300-020-005", "NO DEDUCIBLE")
    suggester = CuentaContableSuggester(SimpleNamespace())
    suggester._cuentas_cache = [cuenta]

    suggestion = await suggester.get_suggestion(
        expense_id=uuid4(),
        concepto="ticket sin requisitos fiscales",
        proyecto="La Merced",
        has_cfdi=False,
        use_llm=False,
    )

    assert suggestion is not None
    assert suggestion.tier == "no_deducible_project"
    assert suggestion.cuenta_codigo == "5300-020-005"
    assert "Regla No Deducibles" in suggestion.reason


@pytest.mark.asyncio
async def test_suggester_does_not_apply_no_deducible_rule_when_cfdi_exists():
    cuenta = _account("5300-020-005", "NO DEDUCIBLE")
    suggester = CuentaContableSuggester(SimpleNamespace())
    suggester._cuentas_cache = [cuenta]

    suggestion = await suggester._apply_no_deducible_project_rule(
        proyecto="La Merced",
        budget_concept_id=None,
        has_cfdi=True,
    )

    assert suggestion is None


@pytest.mark.asyncio
async def test_accounting_service_resolves_project_specific_no_deductible_account():
    cuenta = _account("5300-016-033", "GASTOS NO DEDUCIBLES HWC")
    session = SimpleNamespace()
    expense = SimpleNamespace(proyecto="Homeless World Cup M\u00e9xico")

    resolved = await _resolve_project_no_deducible_account(session, [cuenta], expense)

    assert resolved is cuenta
