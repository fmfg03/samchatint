from samchat.agent_actions.registry import (
    CANONICAL_SCOPE_UNPROVEN,
    registered_actions,
)


def test_v01_registers_only_the_concrete_disabled_actions() -> None:
    actions = registered_actions()

    assert [item.action_id for item in actions] == [
        "budget.get_availability",
        "expense.diagnose_blocker",
        "expense.get_status",
        "transfer.create_draft",
    ]
    assert all(item.enabled is False for item in actions)
    assert {item.disabled_reason for item in actions} == {
        CANONICAL_SCOPE_UNPROVEN
    }


def test_registered_actions_have_typed_contract_policy_and_preconditions(
) -> None:
    for action in registered_actions():
        assert action.input_schema.fields
        assert all(field.value_type for field in action.input_schema.fields)
        assert action.policy.policy_id.startswith("samchat.")
        assert action.policy.policy_version == "v0.1"
        assert all(
            item.code and item.enforced_by for item in action.preconditions
        )
        assert "canonical_scope_bound" in {
            item.code for item in action.preconditions
        }


def test_only_draft_declares_the_local_idempotency_precondition() -> None:
    actions = {item.action_id: item for item in registered_actions()}

    assert "idempotency_key" in {
        item.code for item in actions["transfer.create_draft"].preconditions
    }
    assert "idempotency_key" not in {
        item.code for item in actions["expense.get_status"].preconditions
    }


def test_only_mutating_action_requires_idempotency() -> None:
    actions = {item.action_id: item for item in registered_actions()}

    assert actions["transfer.create_draft"].requires_idempotency is True
    assert actions["expense.get_status"].requires_idempotency is False
    assert actions["budget.get_availability"].requires_idempotency is False
    assert actions["expense.diagnose_blocker"].requires_idempotency is False
