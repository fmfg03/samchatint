from devnous.gastos.routes.user_routes import _expense_tip_group_should_show


def test_tip_group_visible_for_food_budget_concept_even_with_generic_cfdi_text():
    assert _expense_tip_group_should_show(
        concepto="Tarifa",
        budget_concept_label="Cafeteria",
        propina_no_deducible=0,
    ) is True


def test_tip_group_visible_for_food_concept_text():
    assert _expense_tip_group_should_show(
        concepto="Consumo de alimentos",
        budget_concept_label="",
        propina_no_deducible=0,
    ) is True


def test_tip_group_visible_when_tip_already_exists():
    assert _expense_tip_group_should_show(
        concepto="Tarifa",
        budget_concept_label="",
        propina_no_deducible=25.50,
    ) is True


def test_tip_group_hidden_for_non_food_without_tip():
    assert _expense_tip_group_should_show(
        concepto="Tarifa aerea",
        budget_concept_label="Transportes",
        propina_no_deducible=0,
    ) is False
