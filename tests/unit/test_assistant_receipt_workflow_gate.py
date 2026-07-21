import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from devnous.gastos.services.receipt_workflow_service import (
    _parse_expense_date,
    _unique_reference,
)
from samchat.assistant.adapters import (
    _verified_receipt_bytes,
    create_personal_receipt_workflow_adapter,
)
from samchat.assistant.capability_negotiation import receipt_workflow_writes_enabled
from samchat.assistant.context import AssistantContext


def test_receipt_workflow_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("ASSISTANT_RECEIPT_WORKFLOW_EMPLOYEE_IDS", raising=False)

    assert receipt_workflow_writes_enabled("employee-1") is False


def test_receipt_workflow_requires_actor_allowlist_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_RECEIPT_WORKFLOW_EMPLOYEE_IDS", "employee-1")

    assert receipt_workflow_writes_enabled("employee-1") is True
    assert receipt_workflow_writes_enabled("employee-2") is False


def test_receipt_workflow_allowlist_is_case_insensitive(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_RECEIPT_WORKFLOW_EMPLOYEE_IDS", "EMPLOYEE-A")

    assert receipt_workflow_writes_enabled("employee-a") is True


@pytest.mark.asyncio
async def test_unique_reference_locks_employee_allocation_before_lookup() -> None:
    lock_result = MagicMock()
    lookup_result = MagicMock()
    lookup_result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[lock_result, lookup_result])

    reference = await _unique_reference(
        session,
        UUID("22222222-2222-2222-2222-222222222222"),
    )

    assert reference.isdigit()
    assert len(reference) == 6
    lock_statement = str(session.execute.await_args_list[0].args[0])
    assert "pg_advisory_xact_lock" in lock_statement


def test_receipt_bytes_are_bound_to_original_evidence_hash() -> None:
    raw = b"receipt bytes"
    payload = {
        "file_b64": base64.b64encode(raw).decode("ascii"),
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
    }

    assert _verified_receipt_bytes(payload) == raw

    payload["evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="evidence hash mismatch"):
        _verified_receipt_bytes(payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-07-20", "2026-07-20"),
        ("2026/07/20", "2026-07-20"),
        ("20/07/2026", "2026-07-20"),
        ("20-07-2026", "2026-07-20"),
    ],
)
def test_receipt_date_parser_accepts_closed_supported_formats(
    raw: str, expected: str
) -> None:
    assert _parse_expense_date(raw).date().isoformat() == expected


def test_receipt_date_parser_rejects_ambiguous_or_unknown_formats() -> None:
    with pytest.raises(ValueError, match="formato valido"):
        _parse_expense_date("July 20, 2026")


@pytest.mark.asyncio
async def test_receipt_adapter_defers_commit_to_confirmation_transaction(
    monkeypatch,
) -> None:
    actor_id = "22222222-2222-2222-2222-222222222222"
    raw = b"receipt bytes"
    account = SimpleNamespace(
        id="account-1",
        referencia_base="123456",
        to_dict=lambda: {"id": "account-1", "referencia_base": "123456"},
    )
    workflow = AsyncMock(
        return_value=SimpleNamespace(
            account=account,
            expense=SimpleNamespace(id="expense-1"),
            payment_request=SimpleNamespace(id="request-1"),
        )
    )
    monkeypatch.setenv("ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED", "true")
    monkeypatch.setattr(
        "samchat.assistant.adapters.create_personal_receipt_workflow", workflow
    )
    monkeypatch.setattr(
        "samchat.assistant.adapters._expense_snapshot", lambda value: {"id": value.id}
    )
    monkeypatch.setattr(
        "samchat.assistant.adapters._documento_snapshot",
        lambda value: {"id": value.id},
    )

    await create_personal_receipt_workflow_adapter(
        SimpleNamespace(),
        context=AssistantContext(responsible_user_id=actor_id),
        payload={
            "file_b64": base64.b64encode(raw).decode("ascii"),
            "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        },
    )

    assert workflow.await_args.kwargs["commit"] is False
