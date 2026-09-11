from samchat.assistant.router import _derive_empleado_role


def test_supabase_customer_maps_to_client_not_finance():
    assert _derive_empleado_role(user_payload={}, supabase_roles=["customer"]) == "cliente"


def test_supabase_finance_role_remains_finance():
    assert _derive_empleado_role(user_payload={}, supabase_roles=["finanzas"]) == "finanzas"
