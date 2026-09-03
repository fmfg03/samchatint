from devnous.gastos.services.employee_debtor_accounting_service import (
    _debtor_account_match_score,
    _debtor_name_match_score,
    _format_missing_expense_accounts,
)

from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def test_debtor_accounting_uses_existing_employee_subaccounts_only():
    source = read("src/devnous/gastos/services/employee_debtor_accounting_service.py")

    assert 'DEBTOR_ACCOUNT_ROOT_CODE = "1170-001-000"' in source
    assert 'DEBTOR_ACCOUNT_PREFIX = "1170-001-"' in source
    assert "CuentaContable(" not in source
    assert "missing_employee_debtor_account" in source


def test_debtor_accounting_hooks_approved_business_events():
    payment_source = read("src/devnous/gastos/services/documento_payment_service.py")
    workflow_source = read("src/devnous/gastos/services/documento_workflow_service.py")
    settlement_source = read("src/devnous/gastos/services/cuenta_settlement_service.py")

    assert "ensure_debtor_payment_posting_for_document" in payment_source
    assert "ensure_debtor_comprobacion_posting_for_informe" in workflow_source
    assert 'documento.tipo == "INFORME"' in workflow_source
    assert "ensure_debtor_settlement_posting" in settlement_source


def test_debtor_accounting_is_visible_in_both_approved_views():
    user_routes = read("src/devnous/gastos/routes/user_routes.py")
    admin_routes = read("src/devnous/gastos/routes/admin_routes.py")

    assert "Auxiliar de deudores" in user_routes
    assert "build_cuenta_debtor_auxiliary" in user_routes
    assert '"/admin/contabilidad/deudores"' in admin_routes
    assert "Empleados sin subcuenta de deudores" in admin_routes


def test_debtor_accounting_uses_cuenta_beneficiary_not_requester_for_reports():
    source = read("src/devnous/gastos/services/employee_debtor_accounting_service.py")

    assert "resolve_cuenta_debtor_empleado" in source
    assert 'beneficiario_empleado_id' in source
    assert "empleado = await resolve_cuenta_debtor_empleado(session, cuenta)" in source
    assert "CuentaDeGastos.empleado_id`` is the authenticated requester/capturer" in source


def test_debtor_account_match_allows_omitted_middle_names_but_fails_weak_matches():
    assert _debtor_name_match_score(
        "carlos felipe lozano pardinas",
        "carlos lozano pardinas",
    ) > 0
    assert _debtor_name_match_score(
        "jose odilon trujillo macedo",
        "odilon trujillo macedo",
    ) > 0
    assert _debtor_name_match_score(
        "carlos felipe lozano pardinas",
        "carlos lozano",
    ) == 0
    assert (
        _debtor_account_match_score(
            "CARLOS FELIPE LOZANO PARDINAS",
            "CARLOS LOZANO PARDINAS",
        )
        > 0
    )


def test_debtor_account_match_rejects_weak_name_overlap():
    assert _debtor_account_match_score("CARLOS FELIPE LOZANO PARDINAS", "CARLOS GARCIA") == 0


def test_debtor_accounting_supports_petty_cash_alternate_accounts_without_changing_normal_debtors():
    source = read("src/devnous/gastos/services/employee_debtor_accounting_service.py")

    assert '"caja_chica_usd": "1110-001-001"' in source
    assert '"caja_chica_pesos": "1110-001-002"' in source
    assert "resolve_cuenta_debtor_account" in source
    assert "beneficiario_alterno_tipo" in source
    assert "return await resolve_employee_debtor_account(session, empleado)" in source


def test_debtor_comprobacion_missing_account_reason_lists_expense_references():
    reason = _format_missing_expense_accounts(
        [
            SimpleNamespace(
                id="expense-1",
                numero_referencia="O-26000355",
                concepto="Alimentos",
                cuenta_contable=None,
            ),
            SimpleNamespace(
                id="expense-2",
                numero_referencia="O-26000351",
                concepto="Gasolina",
                cuenta_contable=None,
            ),
            SimpleNamespace(
                id="expense-3",
                numero_referencia="O-26000350",
                concepto="Alimentos",
                cuenta_contable=object(),
            ),
        ]
    )

    assert reason == (
        "expense_missing_accounts:"
        "O-26000355 Alimentos; O-26000351 Gasolina"
    )
    assert "expense-1" not in reason


def test_edit_expense_unchecking_company_amex_applies_budget_concept_account_mapping():
    source = read("src/devnous/gastos/routes/user_routes.py")

    assert (
        "from ..services.budget_concept_account_service import (\n"
        "    apply_budget_concept_cuenta_mapping,\n"
        ")"
    ) in source
    assert (
        "reclassified_from_company_amex = (\n"
        "        current_company_amex and not requested_company_amex\n"
        "    )"
    ) in source
    assert (
        "await apply_budget_concept_cuenta_mapping(\n"
        "            session, expense\n"
        "        )"
    ) in source
    assert "cuenta_contable asignada desde partida" in source
