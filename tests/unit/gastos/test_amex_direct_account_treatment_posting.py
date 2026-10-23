"""Partner receivables preserve bank economics without budget/tax expense lines."""

from decimal import Decimal
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from devnous.gastos.services.amex_direct_accounting_service import (
    partner_receivable_lines,
)
from devnous.gastos.services.amex_direct_common import DirectConflict


def facts():
    debtor = NS(id=uuid4(), codigo="1170-002-123", activo=True)
    liability = NS(id=uuid4(), codigo="2120-002-062", activo=True)
    item = NS(
        id=uuid4(),
        account_treatment_decision_id=uuid4(),
        cfdi_id=uuid4(),
        tournament_id=None,
        project_code=None,
        budget_concept_id=None,
        budget_line_id=None,
        budget_version_id=None,
        budget_assignment_id=None,
        edition_year=None,
        budget_status="not_applicable",
    )
    expense = NS(
        id=uuid4(),
        gasto_cantidad=Decimal("939.00"),
        concepto="Subscription",
        cuenta_contable_id=debtor.id,
        contra_cuenta_contable_id=liability.id,
        proyecto=None,
        budget_concept_id=None,
    )
    source = NS(
        imported_expense_id=expense.id,
        movement_kind="debit",
        raw_signed_amount=Decimal("939.00"),
    )
    decision = NS(
        id=item.account_treatment_decision_id,
        action="set",
        treatment="partner_receivable",
        debtor_account_id=debtor.id,
    )
    return item, expense, decision, debtor, liability, source


def test_partner_full_amount_is_asset_not_an_expense_or_tax():
    item, expense, decision, debtor, liability, source = facts()
    rows = partner_receivable_lines(item, expense, decision, debtor, liability, source)
    assert [(r["cuenta_codigo"], r["debe"], r["haber"]) for r in rows] == [
        (debtor.codigo, Decimal("939.00"), 0),
        (liability.codigo, 0, Decimal("939.00")),
    ]
    assert rows[0]["raw_row_json"]["movement"] == "debe_deudor_socio_amex"
    for row in rows:
        meta = row["raw_row_json"]
        assert meta["coverage_kind"] == "partner_receivable"
        assert meta["account_treatment_decision_id"] == str(decision.id)
        assert meta["debtor_account_id"] == str(debtor.id)
        assert "budget_assignment_id" not in meta
        assert "tax" not in meta


def test_partner_without_invoice_requires_its_explicit_typed_declaration():
    item, expense, decision, debtor, liability, source = facts()
    item.cfdi_id = None
    with pytest.raises(DirectConflict, match="partner_fiscal_resolution_required"):
        partner_receivable_lines(item, expense, decision, debtor, liability, source)
    declaration = NS(
        id=uuid4(), action="declare", treatment="full_amount_partner_receivable_no_tax"
    )
    rows = partner_receivable_lines(
        item, expense, decision, debtor, liability, source, no_cfdi_decision=declaration
    )
    assert all(
        r["raw_row_json"]["no_cfdi_decision_id"] == str(declaration.id) for r in rows
    )
    declaration.treatment = "full_amount_expense_no_tax"
    with pytest.raises(DirectConflict, match="partner_fiscal_resolution_required"):
        partner_receivable_lines(
            item,
            expense,
            decision,
            debtor,
            liability,
            source,
            no_cfdi_decision=declaration,
        )


@pytest.mark.parametrize(
    "target,key,value,reason",
    [
        ("debtor", "activo", False, "invalid_amex_partner_decision"),
        ("debtor", "codigo", "1170-001-123", "invalid_amex_partner_decision"),
        ("item", "budget_assignment_id", uuid4(), "partner_budget_conflict"),
        ("item", "project_code", "FAKE", "partner_budget_conflict"),
        ("expense", "proyecto", "FAKE", "partner_budget_conflict"),
        (
            "source",
            "raw_signed_amount",
            Decimal("940"),
            "partner_economic_source_changed",
        ),
        ("source", "movement_kind", "credit", "partner_economic_source_changed"),
        ("expense", "cuenta_contable_id", uuid4(), "partner_account_mapping_changed"),
    ],
)
def test_partner_rejects_incompatible_source_or_account(target, key, value, reason):
    item, expense, decision, debtor, liability, source = facts()
    setattr(locals()[target], key, value)
    with pytest.raises(DirectConflict, match=reason):
        partner_receivable_lines(item, expense, decision, debtor, liability, source)


def test_frozen_partner_no_cfdi_export_keeps_typed_audit_and_two_accounts():
    from datetime import date

    from devnous.gastos.models import AmexDirectAccountingCut
    from devnous.gastos.services.amex_accounting_cut_service import _freeze_lines
    from devnous.gastos.services.amex_cut_export_service import cut_expense_cfdis
    from devnous.gastos.services.coi_poliza_exporter import generate_coi_poliza_csv

    item, expense, decision, debtor, liability, source = facts()
    item.cfdi_id = None
    fiscal = NS(
        id=uuid4(), action="declare", treatment="full_amount_partner_receivable_no_tax"
    )
    rows = _freeze_lines(
        partner_receivable_lines(
            item, expense, decision, debtor, liability, source, no_cfdi_decision=fiscal
        )
    )
    member = {
        "expense_id": str(expense.id),
        "amount": "939.00",
        "concepto": expense.concepto,
        "coverage_kind": "partner_receivable",
        "cfdi": None,
        "no_cfdi_decision": {
            "id": str(fiscal.id),
            "reason": "Personal subscription without invoice",
        },
        "journal_lines": rows,
    }
    cut = AmexDirectAccountingCut(
        id=uuid4(),
        import_id=uuid4(),
        accounting_date=date(2026, 10, 23),
        snapshot_json={"partidas": [member], "lines": rows},
    )
    frozen = cut_expense_cfdis(cut)
    assert len(frozen) == 1
    assert frozen[0].cfdi_uuid is None
    assert frozen[0].iva_amount == 0
    assert "Sin CFDI" in frozen[0].missing_cfdi_warning
    assert str(fiscal.id) in frozen[0].missing_cfdi_warning
    assert [r["cuenta"] for r in frozen[0].posting_movements] == [
        debtor.codigo,
        liability.codigo,
    ]
    blob = generate_coi_poliza_csv(frozen).decode("cp1252")
    assert debtor.codigo in blob and liability.codigo in blob
    assert "1180-" not in blob and "5300-" not in blob
    assert blob.count("FIN_PARTIDAS") == 1
