from __future__ import annotations

from samchat.finance_platform.service import build_finance_platform_snapshot


def _expense(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "expense-id",
        "numero_referencia": "O-26000136",
        "estado_reembolso": "pendiente",
        "informe_estado": "enviado",
        "informe_numero_referencia": "I-933635",
        "cuenta_contable_id": "debit-account",
        "contra_cuenta_contable_id": "credit-account",
        "budget_concept_id": "concept-id",
        "budget_concept_name": "Transporte",
        "budget_concept_active": True,
    }
    row.update(overrides)
    return row


def _actions_for(expense: dict[str, object]) -> list[dict[str, object]]:
    return build_finance_platform_snapshot(
        {"documents": [], "expenses": [expense], "polizas": []}
    )["action_queue"]["actions"]


def _actions_for_snapshot(snapshot: dict[str, object]) -> list[dict[str, object]]:
    return build_finance_platform_snapshot(snapshot)["action_queue"]["actions"]


def test_sent_informe_missing_debit_account_is_an_early_warning() -> None:
    actions = _actions_for(_expense(cuenta_contable_id=""))

    assert len(actions) == 1
    assert actions[0]["title"] == "Preparar I-933635: gasto O-26000136"
    assert "Falta cuenta contable." in actions[0]["detail"]
    assert actions[0]["href"] == "/admin/finanzas#coi-pendiente"


def test_sent_informe_missing_credit_account_reports_the_exact_field() -> None:
    actions = _actions_for(_expense(contra_cuenta_contable_id=""))

    assert len(actions) == 1
    assert "Falta contracuenta." in actions[0]["detail"]


def test_sent_informe_missing_both_accounts_reports_inactive_concept() -> None:
    actions = _actions_for(
        _expense(
            cuenta_contable_id="",
            contra_cuenta_contable_id="",
            budget_concept_active=False,
        )
    )

    assert len(actions) == 1
    assert "cuenta contable y contracuenta" in actions[0]["detail"]
    assert "Concepto presupuestal inactivo: Transporte." in actions[0]["detail"]


def test_sent_informe_without_concept_explains_the_missing_catalog_link() -> None:
    actions = _actions_for(
        _expense(cuenta_contable_id="", budget_concept_id="", budget_concept_name=None)
    )

    assert len(actions) == 1
    assert "Sin concepto presupuestal efectivo" in actions[0]["detail"]


def test_non_sent_expense_keeps_the_existing_generic_coi_action() -> None:
    actions = _actions_for(_expense(informe_estado="aprobado", cuenta_contable_id=""))

    assert len(actions) == 1
    assert actions[0]["title"] == "Clasificar cuentas de gasto O-26000136"


def test_complete_sent_informe_has_no_accounting_readiness_action() -> None:
    assert _actions_for(_expense()) == []


def test_readiness_projection_does_not_mutate_expense_snapshot() -> None:
    expense = _expense(cuenta_contable_id="")
    original = dict(expense)

    _actions_for(expense)

    assert expense == original


def test_truncated_expense_scan_is_disclosed_without_blocking_workflow() -> None:
    actions = _actions_for_snapshot(
        {
            "documents": [],
            "expenses": [],
            "polizas": [],
            "source_status": {
                "expense_scan_truncated": True,
                "expense_scan_limit": 300,
            },
        }
    )

    assert len(actions) == 1
    assert actions[0]["severity"] == "low"
    assert actions[0]["title"] == "Verificación contable parcial"
