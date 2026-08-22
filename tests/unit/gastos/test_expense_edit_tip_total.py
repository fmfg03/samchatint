def apply_tip_delta_to_total(current_total: float, old_tip: float, new_tip: float) -> float:
    # Mirrors the edit-route semantics: gasto_cantidad stores total paid,
    # propina_no_deducible is a component inside that total.
    return round(max(round(float(current_total or 0.0), 2) + round(float(new_tip or 0.0) - float(old_tip or 0.0), 2), 0.0), 2)


def test_adding_tip_after_initial_save_increases_paid_total_by_delta():
    assert apply_tip_delta_to_total(100.0, 0.0, 15.0) == 115.0


def test_editing_existing_tip_changes_total_only_by_delta():
    assert apply_tip_delta_to_total(115.0, 15.0, 20.0) == 120.0


def test_removing_tip_reduces_total_by_delta():
    assert apply_tip_delta_to_total(120.0, 20.0, 0.0) == 100.0
