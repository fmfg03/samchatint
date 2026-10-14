"""Account suggestions must not resolve missing evidence by catalog row order."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services.cuenta_contable_suggester import (
    CuentaContableSuggestion,
    CuentaContableSuggester,
)


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


def account(suffix, name):
    # Synthetic codes and UUIDs: never a proposed production catalog mapping.
    return SimpleNamespace(
        id=uuid4(), codigo=f"SYNTH-{suffix}", nombre=name, activo=True
    )


def mapping(concept, suffix, parent="SYNTH"):
    return SimpleNamespace(concepto=concept, sub_cuenta=suffix, parent_account=parent)


def suggester(mappings, accounts, base="SYNTH"):
    tournament = SimpleNamespace(
        id=uuid4(), name="Torneo sintético", cuenta_contable_relacionada=base
    )
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[Result(tournament), Result(mappings)])
    )
    service = CuentaContableSuggester(session)
    service._cuentas_cache = accounts
    return service, tournament.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "concept,target,suffix",
    [
        ("Apoyo de asociación", "Cuotas Asociaciones", "A"),
        ("Pago de arbitrajes", "Arbitrajes", "R"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
async def test_exact_mapping_wins_independently_of_order(
    concept, target, suffix, reverse
):
    national = account("N", "Fase Nacional")
    expected = account(suffix, target)
    rows = [mapping("Gastos Fase Nacional", "N"), mapping(concept, suffix)]
    if reverse:
        rows.reverse()
    service, tournament_id = suggester(rows, [national, expected])
    result = await service._apply_torneo_proyecto_rule(None, tournament_id, concept)
    assert result.cuenta_contable_id == expected.id
    assert result.can_preselect


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "concept", ["Apoyo de asociación", "Pago de arbitrajes", "", "   ", "!!!"]
)
async def test_unknown_or_empty_concept_never_uses_first_mapping(concept):
    service, tournament_id = suggester(
        [mapping("Gastos Fase Nacional", "N")], [account("N", "Fase Nacional")]
    )
    assert (
        await service._apply_torneo_proyecto_rule(None, tournament_id, concept) is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "concept,suffix", [("Apoyo de asociación", "A"), ("Pago de arbitrajes", "R")]
)
async def test_missing_exact_account_never_uses_prefix_account(concept, suffix):
    service, tournament_id = suggester(
        [mapping(concept, suffix)], [account("N", "Fase Nacional")]
    )
    assert (
        await service._apply_torneo_proyecto_rule(None, tournament_id, concept) is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_exact_match_precedes_partial_and_normalizes_both_sides(reverse):
    expected = account("A", "Cuotas Asociaciones")
    rows = [mapping("APOYO", "N"), mapping("  APOYO,   ASOCIACIÓN! ", "A")]
    if reverse:
        rows.reverse()
    service, tournament_id = suggester(rows, [account("N", "Fase Nacional"), expected])
    result = await service._apply_torneo_proyecto_rule(
        None, tournament_id, "apoyo asociación"
    )
    assert result.cuenta_contable_id == expected.id
    assert result.can_preselect


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,concept",
    [
        (
            [mapping("Apoyo asociación", "A"), mapping("APOYO ASOCIACIÓN", "N")],
            "Apoyo asociación",
        ),
        ([mapping("Apoyo", "A"), mapping("Asociación", "N")], "Apoyo asociación"),
        (
            [mapping("Apoyo asociación", "A"), mapping("Apoyo asociación", "A")],
            "Apoyo asociación",
        ),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
async def test_multiple_matches_are_not_resolved_by_order(rows, concept, reverse):
    service, tournament_id = suggester(
        list(reversed(rows)) if reverse else rows,
        [account("A", "Cuotas Asociaciones"), account("N", "Fase Nacional")],
    )
    assert (
        await service._apply_torneo_proyecto_rule(None, tournament_id, concept) is None
    )


@pytest.mark.asyncio
async def test_unique_partial_match_remains_advisory():
    expected = account("A", "Cuotas Asociaciones")
    service, tournament_id = suggester([mapping("Apoyo asociación", "A")], [expected])
    result = await service._apply_torneo_proyecto_rule(
        None, tournament_id, "Pago apoyo asociación"
    )
    assert result.cuenta_contable_id == expected.id
    assert not result.can_preselect
    assert result.requires_explicit_selection
    assert result.confidence_score < 0.7


@pytest.mark.asyncio
async def test_missing_tournament_base_uses_only_matched_mapping_parent():
    expected = account("A", "Cuotas Asociaciones")
    service, tournament_id = suggester(
        [mapping("Otro", "N", "UNRELATED"), mapping("Apoyo asociación", "A")],
        [expected],
        base=None,
    )
    result = await service._apply_torneo_proyecto_rule(
        None, tournament_id, "Apoyo asociación"
    )
    assert result.cuenta_contable_id == expected.id
    assert service.session.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base,suffix,parent", [(None, "A", None), ("SYNTH", None, "SYNTH")]
)
async def test_incomplete_mapping_has_no_account_fallback(base, suffix, parent):
    service, tournament_id = suggester(
        [mapping("Apoyo asociación", suffix, parent)],
        [account("A", "Cuotas Asociaciones")],
        base=base,
    )
    assert (
        await service._apply_torneo_proyecto_rule(
            None, tournament_id, "Apoyo asociación"
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "concept_active,account_active", [(True, True), (False, True), (True, False)]
)
async def test_partida_catalog_requires_active_concept_and_account(
    concept_active, account_active
):
    expected = account("R", "Arbitrajes")
    expected.activo = account_active
    concept = SimpleNamespace(
        active=concept_active,
        cuenta_contable=expected,
        concept_name="Partida sintética",
    )
    service = CuentaContableSuggester(
        SimpleNamespace(execute=AsyncMock(return_value=Result(concept)))
    )
    result = await service._apply_partida_catalog_rule(uuid4())
    if concept_active and account_active:
        assert result.cuenta_contable_id == expected.id
        assert result.can_preselect
    else:
        assert result is None


@pytest.mark.asyncio
async def test_valid_partida_keeps_priority_over_tournament_and_history(monkeypatch):
    expected = account("R", "Arbitrajes")
    concept = SimpleNamespace(
        active=True, cuenta_contable=expected, concept_name="Partida sintética"
    )
    service = CuentaContableSuggester(
        SimpleNamespace(execute=AsyncMock(return_value=Result(concept)))
    )
    service._cuentas_cache = [expected]
    tournament_rule = AsyncMock(
        side_effect=AssertionError("Do not override canonical partida")
    )
    history_rule = AsyncMock(
        side_effect=AssertionError("Do not consult contaminated history")
    )
    monkeypatch.setattr(service, "_apply_torneo_proyecto_rule", tournament_rule)
    monkeypatch.setattr(service, "_apply_learned_mappings", history_rule)
    result = await service.get_suggestion(
        uuid4(), "Pago de arbitrajes", budget_concept_id=uuid4(), use_llm=False
    )
    assert result.cuenta_contable_id == expected.id
    tournament_rule.assert_not_awaited()
    history_rule.assert_not_awaited()


@pytest.mark.parametrize(
    "tier,allowed",
    [
        ("partida", True),
        ("torneo", True),
        ("amex_catalog", True),
        ("no_deducible_project", True),
        ("rules", False),
        ("learned", False),
        ("llm", False),
        ("amex_batch", False),
        ("unknown", False),
    ],
)
def test_high_confidence_alone_does_not_authorize_preselection(tier, allowed):
    expected = account("N", "Fase Nacional")
    suggestion = CuentaContableSuggestion(
        expected.id, expected.codigo, expected.nombre, 0.95, "Synthetic", tier
    )
    assert suggestion.can_preselect is allowed
    assert suggestion.to_dict()["can_preselect"] is allowed
    suggestion.requires_explicit_selection = True
    assert not suggestion.can_preselect
    suggestion.requires_explicit_selection = False
    suggestion.confidence_score = 0.7
    assert not suggestion.can_preselect


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_has_account", [False, True])
async def test_selected_budget_id_never_borrows_same_name_other_phase_account(
    selected_has_account,
):
    selected_id, other_id = uuid4(), uuid4()
    selected_account = account("A", "Cuotas Asociaciones")
    other_account = account("N", "Gastos Fase Nacional")
    selected = SimpleNamespace(
        active=True,
        concept_name="Cuotas de Asociaciones",
        cuenta_contable=selected_account if selected_has_account else None,
    )
    other = SimpleNamespace(
        active=True,
        concept_name="Cuotas de Asociaciones",
        cuenta_contable=other_account,
    )

    async def execute(statement):
        params = statement.compile().params
        assert selected_id in params.values()
        assert other_id not in params.values()
        return Result(selected if selected_id in params.values() else other)

    service = CuentaContableSuggester(
        SimpleNamespace(execute=AsyncMock(side_effect=execute))
    )
    result = await service._apply_partida_catalog_rule(selected_id)
    if selected_has_account:
        assert result.cuenta_contable_id == selected_account.id
    else:
        assert result is None


@pytest.mark.parametrize("reverse", [False, True])
def test_workbook_mapping_does_not_choose_between_same_scope_rows(reverse):
    from samchat.budgets.service import (
        _match_budget_concept_for_account_mapping,
        _scoped_budget_concept_key,
        build_budget_concept_scope_metadata,
    )

    partida, phase = "Cuotas de Asociaciones", "Generales del torneo"
    key = _scoped_budget_concept_key(partida, phase)
    rows = [
        {
            "id": str(uuid4()),
            "concept_name": partida,
            "concept_key": key,
            "tournament_name": "Torneo sintético",
            "cuenta_contable_id": account_id,
            "metadata": build_budget_concept_scope_metadata([phase]),
        }
        for account_id in [None, str(uuid4())]
    ]
    if reverse:
        rows.reverse()
    result = _match_budget_concept_for_account_mapping(
        rows,
        row={
            "concept_key": _scoped_budget_concept_key(partida),
            "subproyecto": phase,
            "proyecto": "Torneo sintético",
            "sheet_row_index": 99,
        },
    )
    assert result is None


def test_budget_semantic_identity_distinguishes_phase_and_absent_account():
    from samchat.budgets.deduplication import budget_concept_semantic_identity
    from samchat.budgets.service import build_budget_concept_scope_metadata

    mapped = {
        "tournament_id": str(uuid4()),
        "concept_name": "Cuotas de Asociaciones",
        "cuenta_contable_id": str(uuid4()),
        "budget_direction": "expense",
        "metadata": build_budget_concept_scope_metadata(["Generales del torneo"]),
    }
    unmapped = {**mapped, "cuenta_contable_id": None}
    national = {
        **mapped,
        "metadata": build_budget_concept_scope_metadata(["Fase Nacional"]),
    }
    assert budget_concept_semantic_identity(mapped) != budget_concept_semantic_identity(
        unmapped
    )
    assert budget_concept_semantic_identity(mapped) != budget_concept_semantic_identity(
        national
    )
