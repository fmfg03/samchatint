from samchat.assistant.action_router import supported_actions
from samchat.assistant.document_action_planner import plan_document_actions
from samchat.assistant.document_classifier import (
    ACCOUNTING_BALANCE,
    CFDI_INVOICE,
    PAYMENT_PROOF,
    ROSTER,
    UNKNOWN_OR_GENERIC,
)


def test_accounting_balance_plans_read_only_previews() -> None:
    actions = plan_document_actions(
        intake_id="docint_test",
        document_type=ACCOUNTING_BALANCE,
        entities={"period": "2026-05", "account_count": 126, "imbalance": "0.00"},
        missing_fields=["company", "project"],
        supported_actions=supported_actions(),
    )

    assert [action.canonical_action for action in actions] == ["executive.accounting_report"]
    assert actions[0].requires_confirmation is False
    assert actions[0].risk_level == "read"


def test_roster_plans_review_write_as_confirmation_gated() -> None:
    actions = plan_document_actions(
        intake_id="docint_roster",
        document_type=ROSTER,
        entities={"team_name": "Tigres", "category": "Sub-17", "player_count": 18},
        missing_fields=["tournament"],
        supported_actions=supported_actions(),
    )

    canonical = [action.canonical_action for action in actions]
    assert "operations.tournament_soul_snapshot" in canonical
    assert "operations.verify_player_document" in canonical
    write_action = next(action for action in actions if action.canonical_action == "operations.verify_player_document")
    assert write_action.requires_confirmation is True
    assert write_action.write_blocked is True


def test_cfdi_maps_to_existing_receipt_actions() -> None:
    actions = plan_document_actions(
        intake_id="docint_cfdi",
        document_type=CFDI_INVOICE,
        entities={"uuid": "ABC", "amount": "100.00", "issuer_rfc": "AAA010101AAA"},
        missing_fields=["expense_or_document_candidate"],
        supported_actions=supported_actions(),
    )

    assert {action.canonical_action for action in actions} >= {
        "receipts.cfdi_matching_overview",
        "receipts.link_expense_to_cfdi",
    }
    assert next(action for action in actions if action.canonical_action == "receipts.link_expense_to_cfdi").requires_confirmation


def test_payment_proof_write_is_confirmation_gated() -> None:
    actions = plan_document_actions(
        intake_id="docint_pay",
        document_type=PAYMENT_PROOF,
        entities={"amount": "45000", "bank_reference": "SPEI123"},
        missing_fields=["document_or_expense_candidate"],
        supported_actions=supported_actions(),
    )

    assert [action.canonical_action for action in actions] == [
        "receipts.pending_payment_overview",
        "receipts.register_document_payment",
    ]
    assert actions[1].requires_confirmation is True


def test_unknown_document_produces_no_executable_writes() -> None:
    actions = plan_document_actions(
        intake_id="docint_unknown",
        document_type=UNKNOWN_OR_GENERIC,
        entities={},
        missing_fields=["target_workflow"],
        supported_actions=supported_actions(),
    )

    assert actions == []


def test_unsupported_action_surface_fails_closed() -> None:
    actions = plan_document_actions(
        intake_id="docint_cfdi",
        document_type=CFDI_INVOICE,
        entities={"uuid": "ABC", "amount": "100.00"},
        missing_fields=[],
        supported_actions=["receipts.cfdi_matching_overview"],
    )

    assert [action.canonical_action for action in actions] == ["receipts.cfdi_matching_overview"]
