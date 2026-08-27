from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services import amex_accounting_posting_service as service


def test_amex_liability_allowlist_is_exact() -> None:
    assert service.ALLOWED_AMEX_LIABILITY_CODES == {
        "2120-002-062",
        "2120-002-063",
        "2120-002-064",
        "2120-002-065",
        "2120-002-066",
        "2120-002-067",
        "2120-002-100",
    }
    assert service.AMEX_REPORT_DEBTOR_CODE == "1170-002-004"
    assert service.SANTANDER_BANK_CODE == "1120-001-001"


def test_payment_card_marker_is_structured_and_round_trips() -> None:
    card_id = uuid4()
    documento = SimpleNamespace(
        metodo_pago="AMEX",
        notas=f"texto operativo\n{service.amex_payment_card_marker(card_id)}\n",
    )

    assert service.parse_amex_payment_card_id(documento) == card_id


def test_payment_card_binding_fails_closed() -> None:
    card_id = uuid4()
    assert service.parse_amex_payment_card_id(
        SimpleNamespace(metodo_pago="TRANSFERENCIA", notas=service.amex_payment_card_marker(card_id))
    ) is None
    assert service.parse_amex_payment_card_id(
        SimpleNamespace(metodo_pago="AMEX", notas="Tarjeta terminacion 5007")
    ) is None
    assert service.parse_amex_payment_card_id(
        SimpleNamespace(
            metodo_pago="AMEX",
            notas=f"{service.AMEX_PAYMENT_CARD_MARKER}=not-a-uuid",
        )
    ) is None


def test_balance_check_requires_positive_equal_sides() -> None:
    assert service.posting_is_balanced(
        [
            {"debe": "80.00", "haber": 0},
            {"debe": "20.00", "haber": 0},
            {"debe": 0, "haber": "100.00"},
        ]
    )
    assert not service.posting_is_balanced(
        [{"debe": "100.00", "haber": 0}, {"debe": 0, "haber": "99.99"}]
    )
    assert not service.posting_is_balanced([])


def test_posting_functions_leave_transaction_control_to_caller() -> None:
    for function in (
        service.ensure_amex_report_approval_posting,
        service.ensure_amex_reconciliation_posting,
        service.ensure_amex_payment_posting,
    ):
        source = inspect.getsource(function)
        assert "session.commit" not in source
        assert "await session.flush" not in source


def test_shared_pase_cfdi_has_one_group_level_fiscal_path() -> None:
    source = inspect.getsource(service._fiscal_lines_for_expenses)
    group_source = inspect.getsource(service._pase_group_fiscal_lines)

    assert "len(group) > 1" in source
    assert "_pase_group_fiscal_lines" in source
    assert '"shared_pase_cfdi": True' in group_source
    assert "pase_cfdi_total_mismatch" in group_source
