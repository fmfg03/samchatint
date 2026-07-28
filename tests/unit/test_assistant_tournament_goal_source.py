from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.copa_telmex.models import Team
from devnous.gastos.models import Tournament, TournamentOperationsLink
from samchat.assistant.tournament_goal_source import (
    TournamentSourceAmbiguousError,
    TournamentSourceNotFoundError,
    inspect_tournament_source,
)


class _ScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        return _ScalarRows(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def all(self):
        return list(self._rows)


class _ReadOnlySession:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected database read")
        return self._results.pop(0)

    def add(self, *_args, **_kwargs):
        raise AssertionError("source inspection must not add rows")

    async def flush(self):
        raise AssertionError("source inspection must not flush")

    async def commit(self):
        raise AssertionError("source inspection must not commit")

    async def delete(self, *_args, **_kwargs):
        raise AssertionError("source inspection must not delete rows")


def _project(**overrides):
    values = {
        "id": uuid4(),
        "name": "Copa Base 2026",
        "description": "Torneo fuente",
        "active": True,
        "display_order": 4,
        "cuenta_contable_relacionada": "5300-010",
        "etapas": ["Colectiva", "Nacional", "Colectiva", ""],
        "categorias": ["2011", "2012", "2011"],
        "form_visibility_areas": ["Operaciones", "Dirección"],
        "created_at": datetime(2026, 1, 2, 3, 4, 5),
        "updated_at": datetime(2026, 2, 3, 4, 5, 6),
    }
    values.update(overrides)
    return Tournament(**values)


@pytest.mark.asyncio
async def test_inspects_local_project_link_and_exact_slug_aggregates():
    project = _project()
    link = TournamentOperationsLink(
        tournament_id=project.id,
        operations_tournament_id="operations-id-1",
        operations_tournament_slug="copa-base-2026",
    )
    dimensions = [
        SimpleNamespace(
            category="2011", gender="Varonil", state="Morelos", municipality="Jiutepec"
        ),
        SimpleNamespace(
            category="2012",
            gender="Femenil",
            state="Morelos",
            municipality="Cuernavaca",
        ),
        SimpleNamespace(
            category="2011", gender="Varonil", state="Morelos", municipality="Jiutepec"
        ),
    ]
    session = _ReadOnlySession(
        [
            _Result(rows=[project]),
            _Result(scalar=link),
            _Result(scalar=3),
            _Result(scalar=42),
            _Result(rows=dimensions),
        ]
    )

    snapshot = await inspect_tournament_source(
        session,
        tournament_id=str(project.id),
    )

    assert snapshot.project.name == "Copa Base 2026"
    assert snapshot.project.etapas == ["Colectiva", "Nacional"]
    assert snapshot.project.categorias == ["2011", "2012"]
    assert snapshot.project.form_visibility_departments == [
        "Operaciones",
        "Dirección",
    ]
    assert snapshot.operations_link.operations_tournament_id == "operations-id-1"
    assert snapshot.observed_operations.model_dump() == {
        "available": True,
        "scope_slug": "copa-base-2026",
        "teams_count": 3,
        "players_count": 42,
        "categories": ["2011", "2012"],
        "branches": ["Femenil", "Varonil"],
        "states": ["Morelos"],
        "municipalities": ["Cuernavaca", "Jiutepec"],
    }
    assert snapshot.domain_write_performed is False
    assert snapshot.source_hash.startswith("sha256:")

    aggregate_statements = session.statements[2:]
    assert len(aggregate_statements) == 3
    for statement in aggregate_statements:
        compiled = statement.compile()
        assert "copa-base-2026" in compiled.params.values()
        assert "tournament_slug" in str(statement)
    assert any(
        Team.__tablename__ in str(statement) for statement in aggregate_statements
    )


@pytest.mark.asyncio
async def test_unlinked_project_stays_local_and_does_not_probe_operations_tables():
    project = _project(name="Proyecto sin liga")
    session = _ReadOnlySession([_Result(rows=[project]), _Result(scalar=None)])

    snapshot = await inspect_tournament_source(
        session,
        tournament_name="  Proyecto sin liga  ",
    )

    assert snapshot.operations_link is None
    assert snapshot.observed_operations.available is False
    assert snapshot.observed_operations.teams_count == 0
    assert len(session.statements) == 2
    assert "lower" in str(session.statements[0]).lower()


@pytest.mark.asyncio
async def test_snapshot_hash_is_deterministic_for_identical_source():
    project = _project()
    results = [_Result(rows=[project]), _Result(scalar=None)]
    first = await inspect_tournament_source(
        _ReadOnlySession(results), tournament_id=project.id
    )
    second = await inspect_tournament_source(
        _ReadOnlySession([_Result(rows=[project]), _Result(scalar=None)]),
        tournament_id=project.id,
    )

    assert first.source_hash == second.source_hash


@pytest.mark.asyncio
async def test_dimensions_strip_blanks_and_dedupe_case_insensitively():
    project = _project()
    link = TournamentOperationsLink(
        tournament_id=project.id,
        operations_tournament_id="operations-id-1",
        operations_tournament_slug="copa-base-2026",
    )
    dimensions = [
        SimpleNamespace(
            category=" Juvenil ",
            gender="VARONIL",
            state=" Morelos ",
            municipality=" Jiutepec ",
        ),
        SimpleNamespace(
            category="juvenil",
            gender="varonil",
            state="morelos",
            municipality="jiutepec",
        ),
        SimpleNamespace(category="   ", gender=" ", state=None, municipality=""),
    ]

    async def inspect(rows):
        return await inspect_tournament_source(
            _ReadOnlySession(
                [
                    _Result(rows=[project]),
                    _Result(scalar=link),
                    _Result(scalar=2),
                    _Result(scalar=20),
                    _Result(rows=rows),
                ]
            ),
            tournament_id=project.id,
        )

    first = await inspect(dimensions)
    reversed_rows = await inspect(list(reversed(dimensions)))

    assert first.observed_operations.categories == ["Juvenil"]
    assert first.observed_operations.branches == ["VARONIL"]
    assert first.observed_operations.states == ["Morelos"]
    assert first.observed_operations.municipalities == ["Jiutepec"]
    assert reversed_rows.observed_operations == first.observed_operations
    assert reversed_rows.source_hash == first.source_hash


@pytest.mark.asyncio
async def test_not_found_and_ambiguous_name_fail_closed():
    with pytest.raises(TournamentSourceNotFoundError):
        await inspect_tournament_source(
            _ReadOnlySession([_Result(rows=[])]),
            tournament_name="Inexistente",
        )

    with pytest.raises(TournamentSourceAmbiguousError):
        await inspect_tournament_source(
            _ReadOnlySession([_Result(rows=[_project(), _project()])]),
            tournament_name="Copa Base 2026",
        )


@pytest.mark.asyncio
async def test_selector_requires_exactly_one_identifier_before_any_read():
    no_reads = _ReadOnlySession([])
    with pytest.raises(ValueError, match="exactly one"):
        await inspect_tournament_source(no_reads)
    with pytest.raises(ValueError, match="exactly one"):
        await inspect_tournament_source(
            no_reads,
            tournament_id=uuid4(),
            tournament_name="Duplicado",
        )
    assert no_reads.statements == []


def test_source_module_has_no_supabase_dependency():
    import samchat.assistant.tournament_goal_source as source_module

    source_text = open(source_module.__file__, encoding="utf-8").read().lower()
    assert "supabase_client" not in source_text
    assert "_supabase" not in source_text
