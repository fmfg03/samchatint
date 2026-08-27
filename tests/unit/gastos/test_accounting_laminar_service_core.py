from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.employee_debtor_accounting_service import (
    DEBTOR_ACCOUNT_PREFIXES,
    SANTANDER_BANK_ACCOUNT_CODE,
    _event_poliza_number,
    _preview_expense_lines,
)


ROOT = Path(__file__).resolve().parents[3]


def _account(code: str):
    return SimpleNamespace(id=uuid4(), codigo=code, nombre=code, activo=True)


def _preview_account(account):
    return {
        "codigo": account.codigo,
        "cuenta_contable_id": str(account.id),
    }


def test_laminar_event_identity_binds_event_and_complete_uuid():
    entity_id = uuid4()
    assert _event_poliza_number("PROV-APR", entity_id) == (
        f"LAM-PROV-APR-{entity_id}"
    )
    assert _event_poliza_number("PROV-PAY", entity_id) != _event_poliza_number(
        "PROV-APR", entity_id
    )


def test_laminar_accounts_are_exact_and_reject_1700_aliases():
    assert SANTANDER_BANK_ACCOUNT_CODE == "1120-001-001"
    assert DEBTOR_ACCOUNT_PREFIXES == ("1170-001-", "1170-002-")
    assert all(not prefix.startswith("1700-") for prefix in DEBTOR_ACCOUNT_PREFIXES)


def test_full_fiscal_preview_translates_to_balanced_bound_lines():
    expense_account = _account("5300-001-001")
    iva_account = _account("1200-001-001")
    local_account = _account("5300-010-002")
    nd_account = _account("5500-001-001")
    retention_account = _account("2130-001-001")
    counterpart = _account("2120-001-001")
    expense = SimpleNamespace(
        concepto="Hospedaje",
        cuenta_contable=expense_account,
    )
    preview = {
        "taxes": {
            "base_gasto": Decimal("100.00"),
            "iva_trasladado": Decimal("16.00"),
            "iva_account": _preview_account(iva_account),
            "impuestos_locales": [
                {
                    "label": "ISH",
                    "importe": Decimal("4.00"),
                    "account": _preview_account(local_account),
                }
            ],
            "gastos_no_deducibles": [
                {
                    "label": "Propina",
                    "importe": Decimal("5.00"),
                    "account": _preview_account(nd_account),
                }
            ],
            "retenciones": [
                {
                    "label": "ISR",
                    "importe": Decimal("10.00"),
                    "account": _preview_account(retention_account),
                }
            ],
            "neto_contrapartida": Decimal("115.00"),
        }
    }

    lines, error = _preview_expense_lines(
        preview=preview,
        expense=expense,
        counterpart=counterpart,
        meta={"source": "test"},
    )

    assert error is None
    assert {line["cuenta_codigo"] for line in lines} == {
        expense_account.codigo,
        iva_account.codigo,
        local_account.codigo,
        nd_account.codigo,
        retention_account.codigo,
        counterpart.codigo,
    }
    assert sum(Decimal(str(line["debe"])) for line in lines) == Decimal("125.00")
    assert sum(Decimal(str(line["haber"])) for line in lines) == Decimal("125.00")


def test_full_fiscal_preview_fails_closed_without_exact_tax_binding():
    expense = SimpleNamespace(
        concepto="Servicio",
        cuenta_contable=_account("5300-001-001"),
    )
    lines, error = _preview_expense_lines(
        preview={
            "taxes": {
                "base_gasto": Decimal("100.00"),
                "iva_trasladado": Decimal("16.00"),
                "iva_account": None,
                "neto_contrapartida": Decimal("116.00"),
            }
        },
        expense=expense,
        counterpart=_account("2120-001-001"),
        meta={},
    )
    assert lines == []
    assert error == "missing_iva_account"


def test_real_workflow_and_payment_services_wire_laminar_hooks():
    workflow = (ROOT / "src/devnous/gastos/services/documento_workflow_service.py").read_text(
        encoding="utf-8"
    )
    payment = (ROOT / "src/devnous/gastos/services/documento_payment_service.py").read_text(
        encoding="utf-8"
    )
    assert "ensure_provider_approval_posting" in workflow
    assert "ensure_debtor_comprobacion_posting_for_informe" in workflow
    assert "ensure_amex_report_approval_posting" in workflow
    assert 'posting.status == "pending"' in workflow
    assert "ensure_provider_payment_posting" in payment
    assert "ensure_debtor_payment_posting_for_document" in payment
    assert "ensure_amex_payment_posting" in payment
    assert payment.index("parse_amex_payment_card_id") < payment.index("has_proveedor")
    assert 'posting.status == "pending"' in payment


def test_amex_reconciliation_validation_wires_accounting_before_notification():
    routes = (ROOT / "src/devnous/gastos/routes/user_routes.py").read_text(encoding="utf-8")
    start = routes.index("async def amex_conciliacion_validate_notify")
    end = routes.index('@router.get("/admin/gastos/amex/conciliacion"', start)
    body = routes[start:end]

    assert "ensure_amex_reconciliation_posting" in body
    assert "notify_amex_reconciliation_validated" in body
    assert body.index("ensure_amex_reconciliation_posting") < body.index("notify_amex_reconciliation_validated")
    assert 'posting.status == "pending"' in body
