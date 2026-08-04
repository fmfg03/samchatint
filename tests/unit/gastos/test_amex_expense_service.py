from types import SimpleNamespace

from devnous.gastos.services.amex_expense_service import (
    calculate_informe_expense_totals,
    is_company_amex_account,
)


def test_is_company_amex_account_matches_bank_label():
    account = SimpleNamespace(nombre="JOSE ODILON", banco="TARJETA AMERICAN EXPRESS", cuenta_bancaria="65303006")

    assert is_company_amex_account(account) is True


def test_is_company_amex_account_matches_amex_abbreviation():
    account = SimpleNamespace(nombre="AMEX Corporativa", banco="", cuenta_bancaria="")

    assert is_company_amex_account(account) is True


def test_is_company_amex_account_rejects_normal_bank_account():
    account = SimpleNamespace(nombre="JOSE ODILON", banco="BBVA", cuenta_bancaria="1234")

    assert is_company_amex_account(account) is False


def test_company_amex_expenses_do_not_create_employee_paid_balance():
    expenses = [
        SimpleNamespace(estado_gasto="activo", gasto_cantidad=3067.43, pagado_con_amex_empresa=True, origen="informe_quick_entry"),
    ]

    totals = calculate_informe_expense_totals(expenses)

    assert totals.total_reported == 3067.43
    assert totals.company_amex == 3067.43
    assert totals.employee_paid == 0
