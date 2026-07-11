from samchat.assistant.document_confirmation import (
    build_proposed_action,
    build_safety_status,
    requires_confirmation,
    stable_action_id,
)


def test_stable_action_id_is_deterministic() -> None:
    first = stable_action_id(
        intake_id="docint_1",
        canonical_action="receipts.register_document_payment",
        payload_preview={"amount": "45000", "bank_reference": "ABC"},
    )
    second = stable_action_id(
        intake_id="docint_1",
        canonical_action="receipts.register_document_payment",
        payload_preview={"bank_reference": "ABC", "amount": "45000"},
    )

    assert first == second
    assert first.startswith("docact_")


def test_write_actions_require_confirmation_and_are_blocked_when_writes_disabled() -> None:
    action = build_proposed_action(
        intake_id="docint_1",
        canonical_action="receipts.register_document_payment",
        title="Registrar pago",
        payload_preview={"amount": "45000"},
        risk_level="high",
        writes_enabled=False,
    )

    assert requires_confirmation(action.canonical_action) is True
    assert action.requires_confirmation is True
    assert action.write_blocked is True
    assert "Confirma" in action.confirmation_prompt


def test_read_actions_do_not_require_confirmation() -> None:
    action = build_proposed_action(
        intake_id="docint_1",
        canonical_action="receipts.pending_payment_overview",
        title="Buscar candidatos",
        payload_preview={"amount": "45000"},
        risk_level="read",
    )

    assert action.requires_confirmation is False
    assert action.write_blocked is False


def test_safety_status_fails_closed_for_write_or_missing_fields() -> None:
    action = build_proposed_action(
        intake_id="docint_1",
        canonical_action="operations.verify_player_document",
        title="Crear revision",
        payload_preview={"team_name": "Tigres"},
        risk_level="medium",
    )

    safety = build_safety_status(
        proposed_actions=[action],
        missing_fields=["tournament"],
    )

    assert safety["can_execute_without_confirmation"] is False
    assert safety["requires_human_review"] is True
    assert safety["blocked_reason"] == "write_requires_explicit_confirmation"
