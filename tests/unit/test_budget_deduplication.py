from samchat.budgets.deduplication import (
    budget_concept_semantic_identity,
    choose_canonical_budget_concept,
    duplicate_budget_concept_groups,
    is_valid_budget_concept_identifier,
    normalize_budget_identity_value,
)


def _row(**overrides):
    row = {
        "id": "legacy",
        "tournament_id": "tournament-1",
        "concept_name": "Envío de material",
        "budget_direction": "expense",
        "cuenta_contable_codigo": "5300-010-025",
        "metadata": {"applicable_phase_labels": ["Fase Estatal"]},
        "active": True,
        "source": "admin_ui",
        "reference_count": 0,
    }
    row.update(overrides)
    return row


def test_identity_normalizes_historical_key_formats_without_boolean_ids():
    assert normalize_budget_identity_value(False) == ""
    assert normalize_budget_identity_value("false") == ""
    assert is_valid_budget_concept_identifier(None)
    assert is_valid_budget_concept_identifier("")
    assert is_valid_budget_concept_identifier(
        "66da0e23-f5f8-48f0-9911-304a9a67c75a"
    )
    assert not is_valid_budget_concept_identifier(False)
    assert not is_valid_budget_concept_identifier("false")
    assert not is_valid_budget_concept_identifier("not-a-uuid")
    assert budget_concept_semantic_identity(_row()) == (
        "tournament_1",
        "expense",
        "envio_de_material",
        "fase_estatal",
        "5300_010_025",
    )


def test_duplicate_groups_keep_distinct_phase_or_account_separate():
    duplicate = _row(id="truth", source="excel_truth_rebuild")
    different_phase = _row(
        id="national",
        metadata={"applicable_phase_labels": ["Fase Nacional"]},
    )
    different_account = _row(
        id="other-account",
        cuenta_contable_codigo="5300-010-001",
    )

    groups = duplicate_budget_concept_groups(
        [_row(), duplicate, different_phase, different_account]
    )

    assert len(groups) == 1
    assert [row["id"] for row in groups[0][1]] == ["legacy", "truth"]


def test_canonical_prefers_ssot_then_preserves_most_linked_record():
    assert choose_canonical_budget_concept(
        [
            _row(id="legacy", reference_count=20),
            _row(id="truth", source="excel_truth_rebuild"),
        ]
    )["id"] == "truth"
    assert choose_canonical_budget_concept(
        [
            _row(id="old", reference_count=1),
            _row(id="linked", reference_count=3),
        ]
    )["id"] == "linked"
