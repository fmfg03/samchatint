from samchat.agent_actions.contracts import ActionRequest, ResolvedPrincipal
from samchat.agent_actions.receipts import InMemoryActionReceiptStore
from samchat.agent_actions.registry import (
    ACTION_NOT_REGISTERED,
    CANONICAL_SCOPE_UNPROVEN,
)
from samchat.agent_actions.service import (
    CORRELATION_ID_REQUIRED,
    IDEMPOTENCY_KEY_REQUIRED,
    IDEMPOTENCY_KEY_REUSED,
    IDENTITY_CONTEXT_MISSING,
    UNTRUSTED_IDENTITY_FIELD,
    AgentActionService,
)


def _principal(actor_id: str = "employee-1") -> ResolvedPrincipal:
    return ResolvedPrincipal(
        actor_id=actor_id,
        tenant_id="samchat-prod",
        roles=("finanzas",),
        effective_capabilities=("finance.ops.read",),
    )


def _service() -> tuple[AgentActionService, InMemoryActionReceiptStore]:
    store = InMemoryActionReceiptStore()
    return AgentActionService(store), store


def test_unregistered_action_fails_closed() -> None:
    service, store = _service()

    result = service.evaluate(
        ActionRequest("payment.execute", "corr-1", {}), principal=_principal()
    )

    assert result.receipt.evaluated_preconditions == (ACTION_NOT_REGISTERED,)
    assert result.receipt.result == "not_invoked"
    assert len(store.receipts) == 1


def test_disabled_actions_never_reach_a_domain_handler(monkeypatch) -> None:
    service, _ = _service()

    async def forbidden_handler(*_args, **_kwargs):
        raise AssertionError("disabled action called a canonical handler")

    monkeypatch.setattr(
        "samchat.assistant.action_router.execute_canonical_action",
        forbidden_handler,
    )
    result = service.evaluate(
        ActionRequest(
            "expense.get_status", "corr-2", {"expense_id": "expense-1"}
        ),
        principal=_principal(),
    )

    assert result.receipt.evaluated_preconditions == (
        CANONICAL_SCOPE_UNPROVEN,
    )
    assert result.receipt.invoked_domain == "gastos"
    assert result.receipt.result == "not_invoked"


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


def test_idempotency_replay_is_bound_to_actor_and_redacted_payload() -> None:
    service, store = _service()
    request = ActionRequest(
        "transfer.create_draft",
        "corr-actor-1",
        {
            "monto_solicitado": "100.00",
            "proveedor_cliente_id": "supplier-1",
            "torneo_id": "tournament-1",
        },
        idempotency_key="shared-key",
    )
    first = service.evaluate(request, principal=_principal())

    other_actor = service.evaluate(
        request, principal=_principal("employee-2")
    )
    assert other_actor.replayed is False
    assert other_actor.receipt.receipt_id != first.receipt.receipt_id
    assert other_actor.receipt.actor_id == "employee-2"

    altered_payload = service.evaluate(
        ActionRequest(
            request.action_id,
            "corr-actor-1-altered",
            {**request.payload, "monto_solicitado": "200.00"},
            idempotency_key=request.idempotency_key,
        ),
        principal=_principal(),
    )
    assert altered_payload.replayed is False
    assert altered_payload.receipt.evaluated_preconditions == (
        IDEMPOTENCY_KEY_REUSED,
    )
    assert altered_payload.receipt.idempotency_key is None
    assert len(store.receipts) == 3


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
                "api_key": "do-not-record",
                "Authorization": "Bearer do-not-record",
                "cookie": "session=do-not-record",
                "private-key": "do-not-record",
            },
            idempotency_key="draft-2",
        ),
        principal=_principal(),
    )

    receipt = result.receipt
    assert receipt.normalized_redacted_inputs["archivo_data"] == "[REDACTED]"
    assert receipt.normalized_redacted_inputs["api_token"] == "[REDACTED]"
    assert receipt.normalized_redacted_inputs["api_key"] == "[REDACTED]"
    assert receipt.normalized_redacted_inputs["Authorization"] == "[REDACTED]"
    assert receipt.normalized_redacted_inputs["cookie"] == "[REDACTED]"
    assert receipt.normalized_redacted_inputs["private-key"] == "[REDACTED]"
    assert receipt.policy_envelope == {
        "policy_version": "agent-action-policy-v0.1",
        "actor_id": "employee-1",
        "tenant_id": "samchat-prod",
        "roles": ("finanzas",),
        "effective_capabilities": ("finance.ops.read",),
        "decision": "deny",
    }
    assert receipt.decision == "deny"
    assert receipt.result == "not_invoked"
    assert "submitted" not in receipt.result
