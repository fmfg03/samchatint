import pytest

from samchat.agent_actions.contracts import ActionRequest, ResolvedPrincipal
from samchat.agent_actions.receipts import InMemoryActionReceiptStore
from samchat.agent_actions.registry import (
    ACTION_NOT_REGISTERED,
    CANONICAL_SCOPE_UNPROVEN,
)
from samchat.agent_actions.service import (
    CORRELATION_ID_REQUIRED,
    IDEMPOTENCY_KEY_REQUIRED,
    IDENTITY_CONTEXT_MISSING,
    UNTRUSTED_IDENTITY_FIELD,
    AgentActionService,
)


def _principal() -> ResolvedPrincipal:
    return ResolvedPrincipal(
        actor_id="employee-1",
        tenant_id="samchat-prod",
        roles=("finanzas",),
        effective_capabilities=("finance.ops.read",),
    )


def _service(
    dispatcher=None,
) -> tuple[AgentActionService, InMemoryActionReceiptStore]:
    store = InMemoryActionReceiptStore()
    return AgentActionService(store, dispatcher=dispatcher), store


def test_unregistered_action_fails_closed() -> None:
    service, store = _service()

    result = service.evaluate(
        ActionRequest("payment.execute", "corr-1", {}), principal=_principal()
    )

    assert result.receipt.evaluated_preconditions == (ACTION_NOT_REGISTERED,)
    assert result.receipt.result == "not_invoked"
    assert len(store.receipts) == 1


@pytest.mark.parametrize(
    "action_request",
    (
        ActionRequest(
            "expense.get_status", "corr-status", {"expense_id": "e1"}
        ),
        ActionRequest(
            "budget.get_availability",
            "corr-budget",
            {"tournament_id": "t1", "edition_year": 2026},
        ),
        ActionRequest(
            "expense.diagnose_blocker",
            "corr-blocker",
            {"expense_id": "e1"},
        ),
        ActionRequest(
            "transfer.create_draft",
            "corr-draft",
            {
                "monto_solicitado": "100.00",
                "proveedor_cliente_id": "supplier-1",
                "torneo_id": "tournament-1",
            },
            idempotency_key="draft-dispatch-guard",
        ),
    ),
)
def test_disabled_actions_return_before_the_injected_dispatcher(
    action_request: ActionRequest,
) -> None:
    class RecordingDispatcher:
        def __init__(self) -> None:
            self.calls = []

        def dispatch(self, definition, request, principal) -> object:
            self.calls.append((definition, request, principal))
            raise AssertionError("disabled action reached the dispatcher")

    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)
    result = service.evaluate(action_request, principal=_principal())

    assert result.receipt.evaluated_preconditions == (
        CANONICAL_SCOPE_UNPROVEN,
    )
    assert result.receipt.invoked_domain in {"gastos", "presupuestos"}
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


def test_identity_correlation_and_untrusted_payload_are_denied() -> None:
    service, _ = _service()
    request = ActionRequest(
        "expense.get_status", "corr-3", {"expense_id": "e1"}
    )

    missing_principal = service.evaluate(request, principal=None)
    assert missing_principal.receipt.evaluated_preconditions == (
        IDENTITY_CONTEXT_MISSING,
    )

    missing_correlation = service.evaluate(
        ActionRequest("expense.get_status", "", {"expense_id": "e1"}),
        principal=_principal(),
    )
    assert missing_correlation.receipt.evaluated_preconditions == (
        CORRELATION_ID_REQUIRED,
    )

    impersonation = service.evaluate(
        ActionRequest(
            "expense.get_status",
            "corr-4",
            {"expense_id": "e1", "empleado_id": "someone-else"},
        ),
        principal=_principal(),
    )
    assert impersonation.receipt.evaluated_preconditions == (
        UNTRUSTED_IDENTITY_FIELD,
    )


def test_nested_identity_or_authority_fields_are_denied() -> None:
    service, _ = _service()

    result = service.evaluate(
        ActionRequest(
            "expense.get_status",
            "corr-nested",
            {
                "expense_id": "e1",
                "context": {
                    "empleado_id": "someone-else",
                    "nested": [{"facultades": ["finance.execute"]}],
                },
            },
        ),
        principal=_principal(),
    )

    assert result.receipt.evaluated_preconditions == (
        UNTRUSTED_IDENTITY_FIELD,
    )


def test_disabled_draft_requires_idempotency_and_replays_receipt() -> None:
    service, store = _service()
    request = ActionRequest(
        "transfer.create_draft",
        "corr-5",
        {
            "monto_solicitado": "100.00",
            "proveedor_cliente_id": "supplier-1",
            "torneo_id": "tournament-1",
        },
    )

    missing_key = service.evaluate(request, principal=_principal())
    assert missing_key.receipt.evaluated_preconditions == (
        IDEMPOTENCY_KEY_REQUIRED,
    )

    keyed = ActionRequest(
        request.action_id,
        request.correlation_id,
        request.payload,
        idempotency_key="draft-1",
    )
    first = service.evaluate(keyed, principal=_principal())
    replay = service.evaluate(keyed, principal=_principal())

    assert first.receipt.evaluated_preconditions == (CANONICAL_SCOPE_UNPROVEN,)
    assert replay.replayed is True
    assert replay.receipt.receipt_id == first.receipt.receipt_id
    assert len(store.receipts) == 2


def test_receipt_redacts_sensitive_input_and_never_claims_submission() -> None:
    service, _ = _service()

    result = service.evaluate(
        ActionRequest(
            "transfer.create_draft",
            "corr-6",
            {
                "monto_solicitado": "100.00",
                "proveedor_cliente_id": "supplier-1",
                "torneo_id": "tournament-1",
                "archivo_data": "raw document contents",
                "api_token": "do-not-record",
            },
            idempotency_key="draft-2",
        ),
        principal=_principal(),
    )

    receipt = result.receipt
    assert receipt.normalized_redacted_inputs["archivo_data"] == "[REDACTED]"
    assert receipt.normalized_redacted_inputs["api_token"] == "[REDACTED]"
    assert receipt.policy_envelope == {
        "policy_id": "samchat.transfer.draft",
        "policy_version": "v0.1",
        "actor_id": "employee-1",
        "tenant_id": "samchat-prod",
        "roles": ("finanzas",),
        "effective_capabilities": ("finance.ops.read",),
        "decision": "deny",
    }
    assert receipt.decision == "deny"
    assert receipt.result == "not_invoked"
    assert "submitted" not in receipt.result
