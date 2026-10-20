"""Decision economics and canonical full-amount posting without fiscal inventions."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from devnous.gastos.services.amex_direct_accounting_service import no_cfdi_lines
from devnous.gastos.services.amex_direct_common import DirectConflict, item_hash
from devnous.gastos.services.amex_direct_no_cfdi_service import (
    TREATMENT,
    active_decision,
    decision_snapshot,
    economic_source_hash,
    validate_decision,
)


def facts(signed="-116.00"):
    statement = NS(id=uuid4(), file_sha256="a" * 64, card_account_id=uuid4())
    context = NS(id=uuid4(), version=1, currency="MXN")
    item = NS(
        id=uuid4(),
        import_id=statement.id,
        import_item_id=uuid4(),
        context_id=context.id,
        context_version=1,
        no_cfdi_decision_id=uuid4(),
        source_version=1,
        source_hash="b" * 64,
        budget_concept_id=uuid4(),
        budget_assignment_id=uuid4(),
        edition_year=2026,
    )
    expense = NS(
        id=uuid4(),
        gasto_cantidad=Decimal("116.00"),
        currency="MXN",
        estado_gasto="activo",
        fecha=datetime(2026, 10, 2),
        concepto="Bank charge",
        proyecto="Project",
    )
    source = NS(
        import_id=statement.id,
        imported_expense_id=expense.id,
        movement_kind="debit",
        raw_signed_amount=Decimal(signed),
        source_json={
            "currency": "MXN",
            "date": "2026-10-02",
            "description": "Merchant",
        },
        recovery_id=None,
        recovery_revision_id=None,
        recovery_proof_hash=None,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    )
    decision = NS(
        id=item.no_cfdi_decision_id,
        item_id=item.id,
        import_id=statement.id,
        imported_expense_id=expense.id,
        context_id=context.id,
        context_version=1,
        revision=1,
        action="declare",
        treatment=TREATMENT,
        reason="No invoice available",
        actor_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        economic_source_hash=economic_source_hash(statement, item, source),
    )
    return statement, context, item, source, expense, decision


@pytest.mark.parametrize("signed", ["116.00", "-116.00"])
def test_full_bank_amount_two_lines_no_fiscal_identity(signed):
    statement, context, item, source, expense, decision = facts(signed)
    proof = validate_decision(statement, context, item, source, expense, decision)
    assert proof["reason"] == decision.reason
    account, liability = NS(id=uuid4(), codigo="5300-001-001"), NS(
        id=uuid4(), codigo="2120-002-062"
    )
    lines = no_cfdi_lines(item, expense, decision, account, liability, source)
    assert len(lines) == 2
    assert [(r["debe"], r["haber"]) for r in lines] == [
        (Decimal("116.00"), 0),
        (0, Decimal("116.00")),
    ]
    assert all(
        r["raw_row_json"]["no_cfdi_decision_id"] == str(decision.id) for r in lines
    )
    assert all("cfdi_uuid" not in r["raw_row_json"] for r in lines)


def test_classification_and_budget_do_not_expire_economic_decision():
    statement, context, item, source, expense, decision = facts()
    before = economic_source_hash(statement, item, source)
    item.source_hash = "changed"
    item.source_version = 99
    item.budget_assignment_id = uuid4()
    item.budget_concept_id = uuid4()
    item.edition_year = 2027
    expense.proyecto = "New classification"
    assert economic_source_hash(statement, item, source) == before
    assert validate_decision(statement, context, item, source, expense, decision)


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("item", "context_id", uuid4()),
        ("source", "raw_signed_amount", Decimal("-117")),
        ("source", "recovery_revision_id", uuid4()),
        ("source", "movement_kind", "credit"),
        ("expense", "cfdi_uuid_manual", "11111111-1111-1111-1111-111111111111"),
        ("expense", "cfdi_report_id", uuid4()),
        ("expense", "cfdi_compartido_confirmado", True),
        ("expense", "iva", 16),
        ("expense", "propina_no_deducible", 1),
        ("expense", "cuenta_iva_id", uuid4()),
        ("expense", "retencion_cuentas_json", {"001": "account"}),
        ("item", "declared_uuid", "declared"),
        ("item", "match_proof", {"stale": "proof"}),
        ("expense", "estado_gasto", "inactivo"),
        ("expense", "currency", "USD"),
        ("decision", "action", "revoke"),
        ("decision", "treatment", "exempt"),
    ],
)
def test_contradictions_rejected_without_clearing_evidence(target, key, value):
    statement, context, item, source, expense, decision = facts()
    row = {"item": item, "source": source, "expense": expense, "decision": decision}[
        target
    ]
    setattr(row, key, value)
    with pytest.raises(DirectConflict):
        validate_decision(statement, context, item, source, expense, decision)
    assert getattr(row, key) == value


def test_economic_fingerprint_preserves_sign_and_evidence():
    statement, context, item, source, expense, decision = facts()
    before = decision.economic_source_hash
    source.raw_signed_amount = abs(source.raw_signed_amount)
    assert economic_source_hash(statement, item, source) != before
    source.raw_signed_amount = -source.raw_signed_amount
    source.source_json = {**source.source_json, "description": "Different merchant"}
    assert economic_source_hash(statement, item, source) != before


def test_missing_pointer_fails_closed_and_revoked_history_allows_new_context():
    statement, context, item, source, expense, decision = facts()
    with pytest.raises(DirectConflict, match="head_conflict"):
        active_decision(item, {})
    decision.action = "revoke"
    item.context_id = uuid4()
    assert active_decision(item, {decision.id: decision}) is None
    assert decision_snapshot(decision)["context_id"] == str(context.id)
    decision.item_id = uuid4()
    with pytest.raises(DirectConflict, match="head_conflict"):
        active_decision(item, {decision.id: decision})


def test_legacy_item_hash_unchanged_for_absent_pointer():
    statement, context, item, source, expense, decision = facts()
    del item.no_cfdi_decision_id
    old = item_hash(item)
    item.no_cfdi_decision_id = None
    assert item_hash(item) == old
    item.no_cfdi_decision_id = decision.id
    assert item_hash(item) != old


def test_merchant_only_evidence_preserved_without_manufacturing_invoice():
    statement, context, item, source, expense, decision = facts()
    item.issuer_rfc = "AAA010101AAA"
    item.merchant_alias_id = uuid4()
    assert validate_decision(statement, context, item, source, expense, decision)
    assert item.issuer_rfc == "AAA010101AAA"
    assert item.merchant_alias_id is not None
    source.source_json["declared_uuid"] = str(uuid4())
    with pytest.raises(DirectConflict, match="fiscal_identity_conflict"):
        validate_decision(statement, context, item, source, expense, decision)
