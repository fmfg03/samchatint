from dataclasses import replace
from decimal import Decimal

import pytest

from samchat.agent_actions.contracts import (
    ActionInputSchema,
    ActionRequest,
    InputFieldSchema,
    ResolvedPrincipal,
    input_schema_is_valid,
)
from samchat.agent_actions.receipts import InMemoryActionReceiptStore
from samchat.agent_actions.registry import (
    ACTION_NOT_REGISTERED,
    CANONICAL_SCOPE_UNPROVEN,
)
from samchat.agent_actions.service import (
    CORRELATION_ID,
    IDEMPOTENCY_KEY,
    INPUT_SCHEMA_INVALID,
    PAYLOAD_IDENTITY_FREE,
    PRECONDITION_UNSATISFIED,
    TRUSTED_PRINCIPAL,
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


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls = []

    def dispatch(self, definition, request, principal) -> object:
        self.calls.append((definition, request, principal))
        raise AssertionError("disabled action reached the dispatcher")


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
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)
    result = service.evaluate(action_request, principal=_principal())

    assert result.receipt.evaluated_preconditions == (
        CANONICAL_SCOPE_UNPROVEN,
    )
    assert result.receipt.invoked_domain in {"gastos", "presupuestos"}
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "action_request",
    (
        ActionRequest("expense.get_status", "corr-incomplete", {}),
        ActionRequest(
            "budget.get_availability",
            "corr-incomplete",
            {"tournament_id": "t1"},
        ),
        ActionRequest("expense.diagnose_blocker", "corr-incomplete", {}),
        ActionRequest(
            "transfer.create_draft",
            "corr-incomplete",
            {"monto_solicitado": "1.00"},
            idempotency_key="incomplete-draft",
        ),
    ),
)
def test_incomplete_payload_for_each_action_is_rejected_before_dispatcher(
    action_request: ActionRequest,
) -> None:
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)

    result = service.evaluate(action_request, principal=_principal())

    assert result.receipt.error_code == INPUT_SCHEMA_INVALID
    assert result.receipt.evaluated_preconditions == (INPUT_SCHEMA_INVALID,)
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "action_request",
    (
        ActionRequest(
            "expense.get_status", "corr-invalid-string", {"expense_id": 1}
        ),
        ActionRequest(
            "budget.get_availability",
            "corr-invalid-integer",
            {"tournament_id": "t1", "edition_year": True},
        ),
        ActionRequest(
            "transfer.create_draft",
            "corr-invalid-decimal",
            {
                "monto_solicitado": "NaN",
                "proveedor_cliente_id": "supplier-1",
                "torneo_id": "tournament-1",
            },
            idempotency_key="invalid-decimal",
        ),
    ),
)
def test_invalid_declared_types_are_rejected_before_dispatcher(
    action_request: ActionRequest,
) -> None:
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)

    result = service.evaluate(action_request, principal=_principal())

    assert result.receipt.error_code == INPUT_SCHEMA_INVALID
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


@pytest.mark.parametrize("value", (Decimal("1.5"), 1, "1.5", 1.5))
def test_decimal_schema_accepts_only_supported_finite_values(value) -> None:
    schema = ActionInputSchema(
        fields=(InputFieldSchema("amount", "decimal"),)
    )

    assert input_schema_is_valid(schema, {"amount": value}) is True


@pytest.mark.parametrize(
    "value",
    (True, object(), float("inf"), "not-a-number"),
)
def test_decimal_schema_rejects_boolean_and_non_finite_or_non_numeric_values(
    value,
) -> None:
    schema = ActionInputSchema(
        fields=(InputFieldSchema("amount", "decimal"),)
    )

    assert input_schema_is_valid(schema, {"amount": value}) is False


@pytest.mark.parametrize("value", (None, "   "))
def test_required_string_schema_rejects_null_and_whitespace(value) -> None:
    schema = ActionInputSchema(
        fields=(InputFieldSchema("expense_id", "string"),)
    )

    assert input_schema_is_valid(schema, {"expense_id": value}) is False


@pytest.mark.parametrize("payload", (None, []))
def test_non_mapping_payload_is_rejected_before_dispatcher(payload) -> None:
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)

    result = service.evaluate(
        ActionRequest("expense.get_status", "corr-non-mapping", payload),
        principal=_principal(),
    )

    assert result.receipt.error_code == INPUT_SCHEMA_INVALID
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "action_request",
    (
        ActionRequest(
            "expense.get_status",
            "corr-extra-expense",
            {"expense_id": "e1", "unexpected": "value"},
        ),
        ActionRequest(
            "budget.get_availability",
            "corr-extra-budget",
            {
                "tournament_id": "t1",
                "edition_year": 2026,
                "unexpected": "value",
            },
        ),
        ActionRequest(
            "expense.diagnose_blocker",
            "corr-extra-diagnostic",
            {"expense_id": "e1", "unexpected": "value"},
        ),
        ActionRequest(
            "transfer.create_draft",
            "corr-extra-draft",
            {
                "monto_solicitado": "1.00",
                "proveedor_cliente_id": "supplier-1",
                "torneo_id": "tournament-1",
                "unexpected": "value",
            },
            idempotency_key="extra-draft",
        ),
    ),
)
def test_each_registered_schema_rejects_extra_input_before_dispatcher(
    action_request: ActionRequest,
) -> None:
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)

    result = service.evaluate(action_request, principal=_principal())

    assert result.receipt.error_code == INPUT_SCHEMA_INVALID
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "attribute, value", (("actor_id", 1), ("tenant_id", []))
)
def test_malformed_principal_is_rejected_before_dispatcher(
    attribute: str,
    value,
) -> None:
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)
    principal = replace(_principal(), **{attribute: value})

    result = service.evaluate(
        ActionRequest(
            "expense.get_status", "corr-malformed", {"expense_id": "e1"}
        ),
        principal=principal,
    )

    assert result.receipt.error_code == PRECONDITION_UNSATISFIED
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


def test_strict_schema_rejects_extra_input_before_dispatcher(
    monkeypatch,
) -> None:
    import samchat.agent_actions.service as service_module
    from samchat.agent_actions.registry import get_action

    definition = replace(
        get_action("expense.get_status"),
        input_schema=ActionInputSchema(
            fields=(InputFieldSchema("expense_id", "string"),),
            allow_additional_fields=False,
        ),
    )
    monkeypatch.setattr(
        service_module, "get_action", lambda _action_id: definition
    )
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)

    result = service.evaluate(
        ActionRequest(
            "strict.test",
            "corr-extra",
            {"expense_id": "e1", "unexpected": "value"},
        ),
        principal=_principal(),
    )

    assert result.receipt.error_code == INPUT_SCHEMA_INVALID
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    ("action_request", "principal", "precondition"),
    (
        (
            ActionRequest(
                "expense.get_status", "corr-principal", {"expense_id": "e1"}
            ),
            None,
            TRUSTED_PRINCIPAL,
        ),
        (
            ActionRequest("expense.get_status", "", {"expense_id": "e1"}),
            _principal(),
            CORRELATION_ID,
        ),
        (
            ActionRequest(
                "expense.get_status",
                "corr-identity",
                {"expense_id": "e1", "context": {"roles": ["admin"]}},
            ),
            _principal(),
            PAYLOAD_IDENTITY_FREE,
        ),
        (
            ActionRequest(
                "transfer.create_draft",
                "corr-idempotency",
                {
                    "monto_solicitado": "1.00",
                    "proveedor_cliente_id": "supplier-1",
                    "torneo_id": "tournament-1",
                },
            ),
            _principal(),
            IDEMPOTENCY_KEY,
        ),
    ),
)
def test_local_preconditions_are_rejected_before_dispatcher(
    action_request: ActionRequest,
    principal: ResolvedPrincipal,
    precondition: str,
) -> None:
    dispatcher = RecordingDispatcher()
    service, _ = _service(dispatcher=dispatcher)

    result = service.evaluate(action_request, principal=principal)

    assert result.receipt.error_code == PRECONDITION_UNSATISFIED
    assert result.receipt.evaluated_preconditions == (precondition,)
    assert result.receipt.result == "not_invoked"
    assert dispatcher.calls == []


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
    assert missing_key.receipt.error_code == PRECONDITION_UNSATISFIED
    assert missing_key.receipt.evaluated_preconditions == (IDEMPOTENCY_KEY,)

    keyed = ActionRequest(
        request.action_id,
        request.correlation_id,
        request.payload,
        idempotency_key="draft-1",
    )
    first = service.evaluate(keyed, principal=_principal())
    replay = service.evaluate(keyed, principal=_principal())

    assert first.receipt.evaluated_preconditions == (CANONICAL_SCOPE_UNPROVEN,)
    assert first.receipt.error_code is None
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
    assert receipt.error_code == INPUT_SCHEMA_INVALID
    assert receipt.result == "not_invoked"
    assert "submitted" not in receipt.result
