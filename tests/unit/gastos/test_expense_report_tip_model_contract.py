from devnous.gastos.models import ExpenseReport


def test_expense_report_accepts_tip_no_deducible_keyword():
    expense = ExpenseReport(
        proyecto="Gastos Administrativos - Operaciones",
        concepto="Consumo",
        gasto_cantidad=5800.0,
        propina_no_deducible=250.0,
    )

    assert expense.propina_no_deducible == 250.0
    assert expense.to_dict()["propina_no_deducible"] == 250.0
