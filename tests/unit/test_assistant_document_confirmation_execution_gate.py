from samchat.assistant.action_router import supported_actions
from samchat.assistant.document_classifier import UNKNOWN_OR_GENERIC
from samchat.assistant.document_confirmation import (
    build_proposed_action,
    confirm_document_action,
    stable_payload_hash,
)
from samchat.assistant.document_intake import build_document_intake_result


def _action(intake_id: str, canonical_action: str, payload: dict, risk: str = "medium") -> dict:
    return build_proposed_action(
        intake_id=intake_id,
        canonical_action=canonical_action,
        title=f"Test {canonical_action}",
        payload_preview=payload,
        risk_level=risk,
    ).to_dict()


def test_cfdi_link_confirmation_blocks_when_writes_disabled_without_executor_call() -> None:
    action = _action(
        "docint_cfdi",
        "receipts.link_expense_to_cfdi",
        {"uuid": "123", "expense_id": "exp-1"},
    )
    calls = []

    def executor(canonical_action, payload):  # pragma: no cover - should not be called
        calls.append((canonical_action, payload))
        return {"summary": "unexpected"}

    result = confirm_document_action(
        intake_id="docint_cfdi",
        proposed_action=action,
        confirmation_text="confirmo vincular",
        expected_payload_hash=stable_payload_hash(action["payload_preview"]),
        supported_actions=supported_actions(),
        writes_enabled=False,
        action_router_executor=executor,
    )

    assert result.confirmed is True
    assert result.executed is False
    assert result.status == "blocked"
    assert result.blocked_reason == "writes_disabled"
    assert calls == []


def test_payment_registration_confirmation_blocks_when_writes_disabled() -> None:
    action = _action(
        "docint_pay",
        "receipts.register_document_payment",
        {"amount": "45000", "document_id": "doc-1"},
        risk="high",
    )

    result = confirm_document_action(
        intake_id="docint_pay",
        proposed_action=action,
        confirmation_text="si confirma",
        expected_payload_hash=stable_payload_hash(action["payload_preview"]),
        supported_actions=supported_actions(),
        writes_enabled=False,
    )

    assert result.executed is False
    assert result.blocked_reason == "writes_disabled"
    assert result.safety["direct_write_attempted"] is False


def test_read_only_preview_confirmation_uses_action_router_executor() -> None:
    action = _action(
        "docint_balance",
        "executive.accounting_report",
        {"period": "2026-05", "account_count": 126},
        risk="read",
    )
    calls = []

    def executor(canonical_action, payload):
        calls.append((canonical_action, payload))
        return {"summary": "preview generado"}

    result = confirm_document_action(
        intake_id="docint_balance",
        proposed_action=action,
        confirmation_text="generalo",
        expected_payload_hash=stable_payload_hash(action["payload_preview"]),
        supported_actions=supported_actions(),
        writes_enabled=False,
        action_router_executor=executor,
    )

    assert result.executed is True
    assert result.status == "executed"
    assert result.execution_result_summary == "preview generado"
    assert result.safety["used_action_router"] is True
    assert calls == [("executive.accounting_report", {"period": "2026-05", "account_count": 126})]


def test_payload_tampering_fails_closed() -> None:
    action = _action(
        "docint_cfdi",
        "receipts.link_expense_to_cfdi",
        {"uuid": "123", "expense_id": "exp-1"},
    )
    original_hash = stable_payload_hash(action["payload_preview"])
    action["payload_preview"] = {"uuid": "123", "expense_id": "exp-2"}

    result = confirm_document_action(
        intake_id="docint_cfdi",
        proposed_action=action,
        confirmation_text="confirmo",
        expected_payload_hash=original_hash,
        supported_actions=supported_actions(),
        writes_enabled=True,
        action_router_executor=lambda *_: {"summary": "unexpected"},
    )

    assert result.executed is False
    assert result.status == "rejected"
    assert result.blocked_reason in {
        "proposed_action_id_mismatch",
        "payload_hash_mismatch",
    }


def test_unsupported_action_rejected_fail_closed() -> None:
    action = _action(
        "docint_x",
        "unsupported.write_action",
        {"value": "1"},
    )

    result = confirm_document_action(
        intake_id="docint_x",
        proposed_action=action,
        confirmation_text="confirmo",
        expected_payload_hash=stable_payload_hash(action["payload_preview"]),
        supported_actions=supported_actions(),
        writes_enabled=True,
        action_router_executor=lambda *_: {"summary": "unexpected"},
    )

    assert result.executed is False
    assert result.status == "rejected"
    assert result.blocked_reason == "unsupported_canonical_action"
    assert result.safety["used_action_router"] is False


def test_unknown_document_has_no_executable_action_generated() -> None:
    intake = build_document_intake_result(
        conversation_id="conv",
        file_name="generic.txt",
        file_kind="text",
        text="Documento generico sin marcadores.",
        supported_actions=supported_actions(),
    )

    assert intake.detected_document_type == UNKNOWN_OR_GENERIC
    assert intake.proposed_actions == []
