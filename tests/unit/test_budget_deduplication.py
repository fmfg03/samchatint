import asyncio

from samchat.budgets.deduplication import (
    budget_concept_semantic_identity,
    choose_canonical_budget_concept,
    duplicate_budget_concept_groups,
    is_valid_budget_concept_identifier,
    normalize_budget_identity_value,
)
from samchat.budgets.service import import_budget_concepts_upload


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


def test_phase_identity_uses_all_labels_without_order_sensitivity():
    state_and_national = _row(
        id="state-national",
        metadata={"applicable_phase_labels": ["Fase Estatal", "Fase Nacional"]},
    )
    same_labels_different_order = _row(
        id="same-scope",
        metadata={"applicable_phase_labels": ["Fase Nacional", "Fase Estatal"]},
    )
    different_scope = _row(
        id="state-regional",
        metadata={"applicable_phase_labels": ["Fase Estatal", "Fase Regional"]},
    )

    groups = duplicate_budget_concept_groups(
        [state_and_national, same_labels_different_order, different_scope]
    )

    assert len(groups) == 1
    assert [row["id"] for row in groups[0][1]] == [
        "state-national",
        "same-scope",
    ]


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


def test_idless_import_uses_stable_catalog_key_when_optional_fields_absent(
    monkeypatch,
):
    existing = {
        "id": "existing-concept",
        "tournament_id": "tournament-1",
        "concept_name": "Viáticos",
        "concept_key": "viaticos",
        "budget_direction": "expense",
        "cuenta_contable_id": "account-1",
        "metadata": {"applicable_phase_labels": ["Fase Nacional"]},
    }
    captured = {}

    async def no_op(*_args, **_kwargs):
        return None

    async def tournaments(*_args, **_kwargs):
        return [{"id": "tournament-1", "name": "TOR-1", "active": True}]

    async def concepts(*_args, **_kwargs):
        return [existing]

    async def save(_session, *, rows, **_kwargs):
        captured["rows"] = rows
        return {"saved": len(rows)}

    monkeypatch.setattr("samchat.budgets.service.ensure_budget_schema", no_op)
    monkeypatch.setattr(
        "samchat.budgets.service._load_tabular_upload_rows",
        lambda **_kwargs: [{"partida": "Viáticos", "proyecto": "TOR-1"}],
    )
    monkeypatch.setattr("samchat.budgets.service._load_tournament_rows", tournaments)
    monkeypatch.setattr("samchat.budgets.service.list_budget_concepts", concepts)
    monkeypatch.setattr("samchat.budgets.service.bulk_save_budget_concepts", save)

    result = asyncio.run(
        import_budget_concepts_upload(
            object(),
            actor_empleado_id="actor",
            file_bytes=b"unused",
            filename="catalogo.csv",
        )
    )

    assert result["saved"] == 1
    assert captured["rows"][0]["concept_id"] == "existing-concept"
