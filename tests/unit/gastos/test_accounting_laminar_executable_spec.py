"""Executable oracle for RQF-ACCOUNTING-LAMINAR-001.

These tests encode the approved accounting contract without importing or
changing production posting services. They are not evidence that the current
runtime is wired correctly. Implementation closure requires adapter tests that
feed the same cases through the real transactional services.
"""

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STORY = ROOT / "docs/sprints/rqf-accounting-laminar-001-story.md"
SPEC = ROOT / "docs/sprints/rqf-accounting-laminar-001-spec.md"

EMPLOYEE_DEBTOR = "1170-001-042"
PARTNER_DEBTOR = "1170-002-007"
ODILON_AMEX_DEBTOR = "1170-002-004"
SANTANDER = "1120-001-001"
BUDGET_EXPENSE = "5300-010-001"
NON_DEDUCTIBLE = "5500-001-001"
TAX = "1200-001-001"
BUDGET_LIABILITY = "2120-001-001"
AMEX_LIABILITY = "2120-002-062"

ALLOWED_AMEX_LIABILITIES = {
    "2120-002-062",
    "2120-002-063",
    "2120-002-064",
    "2120-002-065",
    "2120-002-066",
    "2120-002-067",
    "2120-002-100",
}


@dataclass(frozen=True)
class Line:
    account: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")


def _expense_debits() -> tuple[Line, ...]:
    return (
        Line(BUDGET_EXPENSE, debit=Decimal("100.00")),
        Line(NON_DEDUCTIBLE, debit=Decimal("4.00")),
        Line(TAX, debit=Decimal("16.00")),
    )


POSTING_ORACLE = {
    "LAM-001": _expense_debits() + (Line(BUDGET_LIABILITY, credit=Decimal("120.00")),),
    "LAM-002": (
        Line(BUDGET_LIABILITY, debit=Decimal("120.00")),
        Line(SANTANDER, credit=Decimal("120.00")),
    ),
    "LAM-003": (),
    "LAM-004": (
        Line(EMPLOYEE_DEBTOR, debit=Decimal("120.00")),
        Line(SANTANDER, credit=Decimal("120.00")),
    ),
    "LAM-005": (),
    "LAM-006": (
        Line(PARTNER_DEBTOR, debit=Decimal("120.00")),
        Line(SANTANDER, credit=Decimal("120.00")),
    ),
    "LAM-007": _expense_debits() + (Line(EMPLOYEE_DEBTOR, credit=Decimal("120.00")),),
    "LAM-008": _expense_debits() + (Line(PARTNER_DEBTOR, credit=Decimal("120.00")),),
    "LAM-009": _expense_debits()
    + (Line(ODILON_AMEX_DEBTOR, credit=Decimal("120.00")),),
    "LAM-010": _expense_debits() + (Line(AMEX_LIABILITY, credit=Decimal("120.00")),),
    "LAM-011": (
        Line(AMEX_LIABILITY, debit=Decimal("120.00")),
        Line(SANTANDER, credit=Decimal("120.00")),
    ),
}


REQUIRED_CONTEXT = {
    "LAM-001": {"budget_expense", "budget_liability", "fiscal_breakdown"},
    "LAM-002": {"approval_receipt", "santander"},
    "LAM-003": set(),
    "LAM-004": {"beneficiary_kind", "debtor_account", "santander"},
    "LAM-005": set(),
    "LAM-006": {"beneficiary_kind", "debtor_account", "santander"},
    "LAM-007": {"budget_expense", "fiscal_breakdown", "debtor_account"},
    "LAM-008": {"budget_expense", "fiscal_breakdown", "debtor_account"},
    "LAM-009": {"budget_expense", "fiscal_breakdown", "odilon_amex_debtor"},
    "LAM-010": {"budget_expense", "fiscal_breakdown", "amex_card_mapping"},
    "LAM-011": {"amex_payment_receipt", "santander"},
}


def _idempotency_key(
    rule_id: str,
    source_type: str,
    source_id: str,
    state_version: int,
    owner: str,
) -> str:
    raw = "|".join(
        (
            "ACCOUNTING-LAMINAR-001",
            rule_id,
            source_type,
            source_id,
            str(state_version),
            owner,
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def test_contract_defines_exactly_the_eleven_rules():
    expected_rules = {f"LAM-{number:03d}" for number in range(1, 12)}
    assert set(POSTING_ORACLE) == expected_rules
    assert set(REQUIRED_CONTEXT) == set(POSTING_ORACLE)


def test_every_posting_rule_is_balanced_to_cents():
    for rule_id, lines in POSTING_ORACLE.items():
        debit = sum((line.debit for line in lines), Decimal("0.00"))
        credit = sum((line.credit for line in lines), Decimal("0.00"))
        assert debit == credit, rule_id
        for line in lines:
            assert line.debit == line.debit.quantize(Decimal("0.01"))
            assert line.credit == line.credit.quantize(Decimal("0.01"))


def test_approval_events_with_no_posting_are_explicit():
    assert POSTING_ORACLE["LAM-003"] == ()
    assert POSTING_ORACLE["LAM-005"] == ()


def test_canonical_accounts_exclude_transcription_alias_1700():
    persisted_accounts = {
        line.account for lines in POSTING_ORACLE.values() for line in lines
    }
    assert all(not account.startswith("1700-") for account in persisted_accounts)
    assert EMPLOYEE_DEBTOR.startswith("1170-001-")
    assert PARTNER_DEBTOR.startswith("1170-002-")
    assert ODILON_AMEX_DEBTOR == "1170-002-004"
    assert SANTANDER == "1120-001-001"


def test_amex_rules_use_only_governed_counterparties():
    rule_9_credits = {line.account for line in POSTING_ORACLE["LAM-009"] if line.credit}
    rule_10_credits = {
        line.account for line in POSTING_ORACLE["LAM-010"] if line.credit
    }
    rule_11_debits = {line.account for line in POSTING_ORACLE["LAM-011"] if line.debit}
    assert rule_9_credits == {ODILON_AMEX_DEBTOR}
    assert rule_10_credits <= ALLOWED_AMEX_LIABILITIES
    assert rule_11_debits == rule_10_credits


def test_payment_rules_credit_only_exact_santander_account():
    for rule_id in ("LAM-002", "LAM-004", "LAM-006", "LAM-011"):
        bank_credits = {
            line.account
            for line in POSTING_ORACLE[rule_id]
            if line.credit and line.account.startswith("1120-")
        }
        assert bank_credits == {SANTANDER}, rule_id


def test_missing_required_context_is_a_blocking_contract():
    for rule_id, required in REQUIRED_CONTEXT.items():
        if not required:
            continue
        available = required - {next(iter(required))}
        missing = required - available
        assert missing, rule_id
        assert not required.issubset(available), rule_id


def test_idempotency_key_is_stable_and_transition_sensitive():
    first = _idempotency_key(
        "LAM-001", "documento", "source-123", 7, "transfer_approval"
    )
    retry = _idempotency_key(
        "LAM-001", "documento", "source-123", 7, "transfer_approval"
    )
    next_version = _idempotency_key(
        "LAM-001", "documento", "source-123", 8, "transfer_approval"
    )
    other_owner = _idempotency_key(
        "LAM-001", "documento", "source-123", 7, "transfer_payment"
    )
    assert first == retry
    assert first != next_version
    assert first != other_owner


def test_story_and_spec_preserve_non_backfill_and_double_post_guards():
    story = STORY.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    combined = f"{story}\n{spec}"

    assert "Sin backfill histórico" in story
    assert "economic_spend_already_owned" in spec
    assert "No se genera automáticamente una reversa" in spec
    assert "no autoriza rellenar recibos o pólizas antiguas" in spec
    assert "1170-002-004" in combined
    assert "1120-001-001" in combined
