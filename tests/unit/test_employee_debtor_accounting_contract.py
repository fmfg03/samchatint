from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_expense_service
from devnous.gastos.services.amex_expense_service import set_company_amex_status
from devnous.gastos.services.employee_debtor_accounting_service import (
    _debtor_account_match_score,
    _debtor_name_match_score,
    _format_missing_expense_accounts,
)


ROOT = Path(__file__).resolve().parents[2]


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.added = []

    async def execute(self, _query):
        return _FakeExecuteResult(self._rows)

    def add(self, item):
        self.added.append(item)


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


def test_debtor_comprobacion_missing_account_reason_lists_expense_references() -> None:
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


def test_debtor_comprobacion_missing_account_reason_handles_empty_result() -> None:
    assert _format_missing_expense_accounts([]) == "expense_missing_accounts"
    assert (
        _format_missing_expense_accounts(
            [
                SimpleNamespace(
                    id="expense-1",
                    numero_referencia="O-26000355",
                    concepto="Alimentos",
                    cuenta_contable=object(),
                )
            ]
        )
        == "expense_missing_accounts"
    )


def test_debtor_comprobacion_missing_account_reason_is_bounded() -> None:
    reason = _format_missing_expense_accounts(
        [
            SimpleNamespace(
                id=f"expense-{idx}",
                numero_referencia=f"O-{idx:08d}",
                concepto="Concepto con descripcion extremadamente larga " * 4,
                cuenta_contable=None,
            )
            for idx in range(10)
        ]
    )

    assert "y 2 más" in reason
    assert "O-00000008" not in reason
    assert len(reason) < 850


def test_debtor_comprobacion_missing_account_reason_caps_max_reference_length() -> None:
    reason = _format_missing_expense_accounts(
        [
            SimpleNamespace(
                id=f"expense-{idx}",
                numero_referencia="O-" + ("9" * 100),
                concepto="Concepto con descripcion extremadamente larga " * 4,
                cuenta_contable=None,
            )
            for idx in range(10)
        ]
    )

    assert reason.startswith("expense_missing_accounts:")
    assert "y " in reason
    assert len(reason) < 850


def test_unchecking_company_amex_applies_budget_concept_account_mapping() -> None:
    route_source = read("src/devnous/gastos/routes/user_routes.py")
    service_source = read("src/devnous/gastos/services/amex_expense_service.py")

    assert (
        "from ..services.budget_concept_account_service import (\n"
        "    apply_budget_concept_cuenta_mapping,\n"
        ")"
    ) in route_source
    assert (
        "reclassified_from_company_amex = (\n"
        "        current_company_amex and not requested_company_amex\n"
        "    )"
    ) in route_source
    assert (
        "await apply_budget_concept_cuenta_mapping(\n"
        "            session, expense\n"
        "        )"
    ) in route_source
    assert "cuenta_contable asignada desde partida" in route_source
    assert (
        "from .budget_concept_account_service import "
        "apply_budget_concept_cuenta_mapping"
    ) in service_source
    assert "if not mark_as_amex and expense.cuenta_contable_id is None:" in service_source
    assert "Cuenta contable asignada automáticamente desde partida presupuestal" in service_source


@pytest.mark.asyncio
async def test_bulk_unchecking_company_amex_applies_budget_mapping(monkeypatch) -> None:
    cuenta_id = uuid4()
    mapped_account_id = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        cuenta_gastos_id=cuenta_id,
        estado_gasto="activo",
        pagado_con_amex_empresa=True,
        origen=None,
        cuenta_contable_id=None,
    )
    actor = SimpleNamespace(id=uuid4(), rol="finanzas")
    session = _FakeSession([expense])

    async def fake_apply_mapping(_session, mapped_expense):
        mapped_expense.cuenta_contable_id = mapped_account_id
        return True

    monkeypatch.setattr(
        amex_expense_service,
        "apply_budget_concept_cuenta_mapping",
        fake_apply_mapping,
    )

    changed = await set_company_amex_status(
        session,
        cuenta_id=cuenta_id,
        expense_ids=[expense.id],
        mark_as_amex=False,
        actor=actor,
    )

    assert changed == [expense]
    assert expense.pagado_con_amex_empresa is False
    assert expense.cuenta_contable_id == mapped_account_id
    assert session.added
    assert "Cuenta contable asignada automáticamente" in session.added[0].comentario


@pytest.mark.asyncio
async def test_bulk_company_amex_keeps_existing_account(monkeypatch) -> None:
    cuenta_id = uuid4()
    existing_account_id = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        cuenta_gastos_id=cuenta_id,
        estado_gasto="activo",
        pagado_con_amex_empresa=True,
        origen=None,
        cuenta_contable_id=existing_account_id,
    )
    actor = SimpleNamespace(id=uuid4(), rol="finanzas")
    session = _FakeSession([expense])
    calls = []

    async def fake_apply_mapping(_session, mapped_expense):
        calls.append(mapped_expense.id)
        return True

    monkeypatch.setattr(
        amex_expense_service,
        "apply_budget_concept_cuenta_mapping",
        fake_apply_mapping,
    )

    await set_company_amex_status(
        session,
        cuenta_id=cuenta_id,
        expense_ids=[expense.id],
        mark_as_amex=False,
        actor=actor,
    )

    assert calls == []
    assert expense.cuenta_contable_id == existing_account_id


@pytest.mark.asyncio
async def test_bulk_company_amex_noops_when_status_is_unchanged(monkeypatch) -> None:
    cuenta_id = uuid4()
    expense = SimpleNamespace(
        id=uuid4(),
        cuenta_gastos_id=cuenta_id,
        estado_gasto="activo",
        pagado_con_amex_empresa=True,
        origen=None,
        cuenta_contable_id=None,
    )
    actor = SimpleNamespace(id=uuid4(), rol="finanzas")
    session = _FakeSession([expense])
    calls = []

    async def fake_apply_mapping(_session, mapped_expense):
        calls.append(mapped_expense.id)
        return True

    monkeypatch.setattr(
        amex_expense_service,
        "apply_budget_concept_cuenta_mapping",
        fake_apply_mapping,
    )

    changed = await set_company_amex_status(
        session,
        cuenta_id=cuenta_id,
        expense_ids=[expense.id],
        mark_as_amex=True,
        actor=actor,
    )

    assert changed == []
    assert calls == []
    assert expense.pagado_con_amex_empresa is True
