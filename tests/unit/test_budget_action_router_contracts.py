from samchat.assistant.action_router import supported_read_actions, supported_write_actions


def test_budget_actions_are_exposed_in_router_contracts():
    read_actions = supported_read_actions()
    write_actions = supported_write_actions()

    assert "budgets.snapshot" in read_actions
    assert "budgets.update_line" in write_actions
    assert "budgets.update_version" in write_actions
    assert "budgets.submit_for_approval" in write_actions
    assert "budgets.approve_version" in write_actions
    assert "budgets.freeze_version" in write_actions
    assert "budgets.reforecast" in write_actions
