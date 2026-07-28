from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from samchat.assistant.tournament_draft_authority import (
    TournamentDraftAuthorityError,
    TournamentDraftOwnerInactiveError,
    TournamentDraftOwnerNotFoundError,
    TournamentDraftSourceStaleError,
    inspect_active_tournament_owner,
    inspect_tournament_draft_authority,
)
from samchat.assistant.tournament_goal_source import (
    LocalTournamentOperationsAggregate,
    LocalTournamentProject,
    TournamentSourceSnapshot,
)


SOURCE_ID = UUID("00000000-0000-0000-0000-000000000053")
SOURCE_HASH = "sha256:" + ("a" * 64)


class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _ReadOnlySession:
    def __init__(self, results=()):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected database read")
        return self._results.pop(0)

    def add(self, *_args, **_kwargs):
        raise AssertionError("draft authority must not add rows")

    async def flush(self):
        raise AssertionError("draft authority must not flush")

    async def commit(self):
        raise AssertionError("draft authority must not commit")

    async def delete(self, *_args, **_kwargs):
        raise AssertionError("draft authority must not delete rows")


def _source(source_hash=SOURCE_HASH):
    return TournamentSourceSnapshot(
        project=LocalTournamentProject(
            id=SOURCE_ID,
            name="Torneo fuente",
            active=True,
            display_order=1,
        ),
        observed_operations=LocalTournamentOperationsAggregate(),
        source_hash=source_hash,
    )


def _owner(*, active=True):
    return SimpleNamespace(
        id=uuid4(),
        nombre="Alicia Operaciones",
        departamento="Operaciones",
        rol="admin",
        activo=active,
    )


@pytest.mark.asyncio
async def test_resolves_active_owner_without_source_inspection():
    owner = _owner()
    session = _ReadOnlySession([_Result(owner)])

    snapshot = await inspect_active_tournament_owner(session, str(owner.id))

    assert snapshot.model_dump(mode="json") == {
        "id": str(owner.id),
        "nombre": "Alicia Operaciones",
        "departamento": "Operaciones",
        "rol": "admin",
        "activo": True,
    }
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_reinspects_source_and_resolves_active_local_owner(monkeypatch):
    owner = _owner()
    received = {}

    async def inspect_source(session, **kwargs):
        received["session"] = session
        received["kwargs"] = kwargs
        return _source()

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_authority.inspect_tournament_source",
        inspect_source,
    )
    session = _ReadOnlySession([_Result(owner)])

    authority = await inspect_tournament_draft_authority(
        session,
        owner_employee_id=str(owner.id),
        expected_source_hash=SOURCE_HASH.upper(),
        source_tournament_id=str(SOURCE_ID),
    )

    assert received == {
        "session": session,
        "kwargs": {
            "tournament_id": SOURCE_ID,
            "tournament_name": None,
        },
    }
    assert authority.owner.model_dump(mode="json") == {
        "id": str(owner.id),
        "nombre": "Alicia Operaciones",
        "departamento": "Operaciones",
        "rol": "admin",
        "activo": True,
    }
    assert authority.source.source_hash == SOURCE_HASH
    assert authority.expected_source_hash == SOURCE_HASH
    assert authority.source_hash_verified is True
    assert authority.domain_write_performed is False
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_stale_source_fails_before_owner_lookup(monkeypatch):
    async def inspect_source(*_args, **_kwargs):
        return _source("sha256:" + ("b" * 64))

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_authority.inspect_tournament_source",
        inspect_source,
    )
    session = _ReadOnlySession()

    with pytest.raises(TournamentDraftSourceStaleError, match="source changed"):
        await inspect_tournament_draft_authority(
            session,
            owner_employee_id=uuid4(),
            expected_source_hash=SOURCE_HASH,
            source_tournament_name="Torneo fuente",
        )
    assert session.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "expected_error"),
    [
        (None, TournamentDraftOwnerNotFoundError),
        (_owner(active=False), TournamentDraftOwnerInactiveError),
    ],
)
async def test_missing_or_inactive_owner_fails_closed(
    monkeypatch, owner, expected_error
):
    async def inspect_source(*_args, **_kwargs):
        return _source()

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_authority.inspect_tournament_source",
        inspect_source,
    )
    session = _ReadOnlySession([_Result(owner)])

    with pytest.raises(expected_error):
        await inspect_tournament_draft_authority(
            session,
            owner_employee_id=uuid4(),
            expected_source_hash=SOURCE_HASH,
            source_tournament_id=SOURCE_ID,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {
            "owner_employee_id": "not-a-uuid",
            "expected_source_hash": SOURCE_HASH,
            "source_tournament_id": SOURCE_ID,
        },
        {
            "owner_employee_id": uuid4(),
            "expected_source_hash": "bad-hash",
            "source_tournament_id": SOURCE_ID,
        },
        {
            "owner_employee_id": uuid4(),
            "expected_source_hash": SOURCE_HASH,
            "source_tournament_id": "not-a-uuid",
        },
    ],
)
async def test_invalid_uuid_or_hash_fails_before_reads(monkeypatch, arguments):
    inspected = False

    async def inspect_source(*_args, **_kwargs):
        nonlocal inspected
        inspected = True
        return _source()

    monkeypatch.setattr(
        "samchat.assistant.tournament_draft_authority.inspect_tournament_source",
        inspect_source,
    )
    session = _ReadOnlySession()

    with pytest.raises(TournamentDraftAuthorityError):
        await inspect_tournament_draft_authority(session, **arguments)
    assert inspected is False
    assert session.statements == []


def test_authority_module_has_no_supabase_dependency():
    import samchat.assistant.tournament_draft_authority as authority_module

    source_text = open(authority_module.__file__, encoding="utf-8").read().lower()
    assert "supabase" not in source_text
    assert "_supabase" not in source_text
