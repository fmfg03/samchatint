from samchat.assistant.action_router import supported_actions, supported_read_actions, supported_write_actions


def test_action_router_exposes_read_and_write_contracts():
    actions = set(supported_actions())

    assert "executive.realtime_report" in actions
    assert "receipts.request_cfdi" in actions
    assert set(supported_read_actions()).issubset(actions)
    assert set(supported_write_actions()).issubset(actions)


def test_action_router_keeps_read_write_sets_disjoint():
    assert set(supported_read_actions()).isdisjoint(set(supported_write_actions()))
