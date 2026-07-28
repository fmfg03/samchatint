from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from devnous.gastos.models import Tournament
from samchat.assistant.tournament_application_domain import (
    PROJECTION_FIELDS,
    TournamentApplicationContractError,
    TournamentApplicationDuplicateNameError,
    TournamentApplicationVerificationError,
    create_local_tournament_from_projection,
)


def _projection(**changes):
    payload = {
        "name": " Copa Nacional 2027 ",
        "description": "  Nueva edición  ",
        "active": True,
        "display_order": 4,
        "accounting_account": "  410-20  ",
        "stages": ["Estatal", "Nacional"],
        "categories": ["2012", "2013"],
        "visibility_areas": ["operaciones", "Finanzas"],
    }
    payload.update(changes)
    return payload


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *, existing=None, readback_changes=None, flush_error=None):
        self.existing = existing
        self.readback_changes = readback_changes or {}
        self.flush_error = flush_error
        self.statements = []
        self.added = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _Result(self.existing)
        tournament = self.added[0]
        for field_name, value in self.readback_changes.items():
            setattr(tournament, field_name, value)
        return _Result(tournament)

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        if self.flush_error is not None:
            raise self.flush_error
        self.added[0].id = uuid4()

    async def commit(self):
        raise AssertionError("domain writer must not commit")


@pytest.mark.asyncio
async def test_inserts_exact_projection_and_verifies_without_commit():
    session = _Session()

    result = await create_local_tournament_from_projection(
        session,
        projection=_projection(),
    )

    assert isinstance(result.tournament_id, UUID)
    assert result.domain_write_count == 1
    assert result.committed is False
    assert result.projection.to_dict() == {
        "name": "Copa Nacional 2027",
        "description": "Nueva edición",
        "active": True,
        "display_order": 4,
        "accounting_account": "410-20",
        "stages": ["Estatal", "Nacional"],
        "categories": ["2012", "2013"],
        "visibility_areas": ["Operaciones", "Finanzas"],
    }
    assert len(session.added) == 1
    assert isinstance(session.added[0], Tournament)
    assert session.flush_count == 1
    assert len(session.statements) == 2
    assert session.statements[1].get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
async def test_precheck_rejects_normalized_duplicate_without_insert():
    session = _Session(existing=uuid4())

    with pytest.raises(TournamentApplicationDuplicateNameError):
        await create_local_tournament_from_projection(
            session,
            projection=_projection(),
        )

    assert session.added == []
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_unique_index_race_is_mapped_to_duplicate_conflict():
    class NameConflict(RuntimeError):
        sqlstate = "23505"
        constraint_name = "ux_tournaments_name_normalized"

    failure = IntegrityError("insert", {}, NameConflict("unique violation"))
    session = _Session(flush_error=failure)

    with pytest.raises(TournamentApplicationDuplicateNameError):
        await create_local_tournament_from_projection(
            session,
            projection=_projection(),
        )

    assert len(session.added) == 1
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_asyncpg_wrapped_unique_race_is_mapped_to_duplicate_conflict():
    class AsyncpgUniqueViolation(RuntimeError):
        constraint_name = "ux_tournaments_name_normalized"

    class AdapterWrapper(RuntimeError):
        sqlstate = "23505"

    underlying = AsyncpgUniqueViolation("unique violation")
    wrapper = AdapterWrapper("adapter wrapper")
    wrapper.__cause__ = underlying
    failure = IntegrityError("insert", {}, wrapper)
    session = _Session(flush_error=failure)

    with pytest.raises(TournamentApplicationDuplicateNameError):
        await create_local_tournament_from_projection(
            session,
            projection=_projection(),
        )


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_mislabeled_as_duplicate():
    class CheckViolation(RuntimeError):
        sqlstate = "23514"
        constraint_name = "check_tournament_display_order"

    failure = IntegrityError("insert", {}, CheckViolation("check violation"))
    session = _Session(flush_error=failure)

    with pytest.raises(IntegrityError):
        await create_local_tournament_from_projection(
            session,
            projection=_projection(),
        )


@pytest.mark.asyncio
async def test_readback_mismatch_fails_closed():
    session = _Session(readback_changes={"active": False})

    with pytest.raises(TournamentApplicationVerificationError, match="differs"):
        await create_local_tournament_from_projection(
            session,
            projection=_projection(),
        )


@pytest.mark.asyncio
async def test_missing_readback_fails_closed():
    session = _Session()

    async def execute_without_readback(statement):
        session.statements.append(statement)
        return _Result(None)

    session.execute = execute_without_readback
    with pytest.raises(TournamentApplicationVerificationError, match="read back"):
        await create_local_tournament_from_projection(
            session,
            projection=_projection(),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _projection().items() if key != "name"},
        {**_projection(), "source_tournament_id": "forbidden"},
        {**_projection(), 1: "non-text key"},
        _projection(name="   "),
        _projection(active=1),
        _projection(display_order=True),
        _projection(display_order=-1),
        _projection(stages="Estatal"),
        _projection(categories=["2012", " 2012 "]),
        _projection(visibility_areas=["Legal"]),
        _projection(accounting_account="x" * 201),
    ],
)
def test_projection_contract_rejects_missing_extra_or_invalid_values(payload):
    assert set(PROJECTION_FIELDS) == {
        "name",
        "description",
        "active",
        "display_order",
        "accounting_account",
        "stages",
        "categories",
        "visibility_areas",
    }
    with pytest.raises(TournamentApplicationContractError):
        from samchat.assistant.tournament_application_domain import (
            TournamentApplicationProjection,
        )

        TournamentApplicationProjection.from_mapping(payload)


def test_domain_writer_has_no_external_or_linked_write_dependency():
    import samchat.assistant.tournament_application_domain as module

    source = open(module.__file__, encoding="utf-8").read().casefold()
    assert "supabase" not in source
    assert "tournamentoperationslink" not in source
    assert ".commit(" not in source


def test_normalized_name_migration_preflights_then_adds_expression_index():
    migration = (
        __import__("pathlib")
        .Path(__file__)
        .parents[2]
        .joinpath(
            "database",
            "migrations",
            "20260728_rqf054_tournament_name_normalized_unique.sql",
        )
        .read_text(encoding="utf-8")
        .casefold()
    )
    assert "group by lower(btrim(name))" in migration
    assert "having count(*) > 1" in migration
    assert "raise exception" in migration
    assert "create unique index" in migration
    assert "lower(btrim(name))" in migration
    assert "create unique index if not exists" not in migration
    assert "pg_get_expr(indexprs, indrelid)" in migration
    assert "incompatible definition" in migration
