"""Real scope/configuration guards with request-local synthetic persistence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

import devnous.gastos  # noqa: F401
from devnous.gastos.routes import admin_routes
from devnous.gastos.services.tournament_phase_service import (
    tournament_scope_config_changed,
)
from samchat.budgets import service

TID = UUID("c3b247f1-e3fc-4f4a-8995-56a5880f86a6")


class Result:
    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, rows

    def scalar_one_or_none(self):
        return self.row

    def mappings(self):
        return self

    def all(self):
        return list(self.rows)


class Session:
    def __init__(self, tournament, concepts=()):
        self.tournament, self.concepts = tournament, concepts
        self.statements = []
        self.commits = self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        if "SELECT id, concept_name, metadata" in str(statement):
            return Result(rows=self.concepts)
        return Result(row=self.tournament)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class Request:
    def __init__(self, form):
        self.form_values = form

    async def form(self):
        return self.form_values


def project():
    return SimpleNamespace(
        id=TID,
        name="HWC",
        etapas=["Montaje", "Operación"],
        categorias=["Nacional"],
    )


@pytest.mark.parametrize(
    "next_etapas,next_categorias,expected",
    [
        (["Operación", "Montaje"], ["Nacional"], False),
        (["Montaje", "Operación", "Transportes"], ["Nacional"], True),
        (["Montaje"], ["Nacional"], True),
        (["Montaje", "Operación"], [], True),
    ],
)
def test_scope_configuration_compares_membership_not_order(
    next_etapas, next_categorias, expected
):
    assert (
        tournament_scope_config_changed(
            previous_etapas=["Montaje", "Operación"],
            previous_categorias=["Nacional"],
            next_etapas=next_etapas,
            next_categorias=next_categorias,
        )
        is expected
    )


@pytest.fixture
def owner_guards(monkeypatch):
    monkeypatch.setattr(admin_routes, "_ensure_tournaments_admin_schema", AsyncMock())
    monkeypatch.setattr(
        admin_routes, "_require_legacy_gastos_project_mutable", AsyncMock()
    )
    monkeypatch.setattr(
        admin_routes, "_find_local_tournament_by_name", AsyncMock(return_value=None)
    )


async def update(session, form, *, etapas=None, categorias=None):
    return await admin_routes.update_tournament(
        tournament_id=TID,
        request=Request({"active": "on", **form}),
        name="HWC renamed",
        description="same budget",
        display_order=3,
        cuenta_contable_relacionada=None,
        etapas=etapas,
        categorias=categorias,
        current_empleado=SimpleNamespace(id="actor", rol="superadmin"),
        _csrf=None,
        session=session,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["omitted", "reordered", "added"])
async def test_project_edit_preserves_catalogue_scope(owner_guards, change):
    tournament = project()
    metadata = service.build_budget_concept_scope_metadata(["Operación"])
    original = dict(metadata)
    session = Session(
        tournament, [{"id": "concept", "concept_name": "Staff", "metadata": metadata}]
    )
    form, etapas = {}, None
    if change == "reordered":
        form, etapas = {"etapas": "Operación\nMontaje"}, "Operación\nMontaje"
    elif change == "added":
        form, etapas = {
            "etapas": "Montaje\nOperación\nTransportes"
        }, "Montaje\nOperación\nTransportes"
    result = await update(session, form, etapas=etapas)
    assert "success_msg" in parse_qs(urlsplit(result.headers["location"]).query)
    assert session.commits == 1 and session.rollbacks == 0
    assert metadata == original
    assert tournament.categorias == ["Nacional"]
    assert "Operación" in tournament.etapas
    assert not any("UPDATE budget_concepts" in query for query in session.statements)
    assert "FOR UPDATE" in session.statements[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_removing_referenced_label_rejects_active_and_retired_partidas(
    owner_guards, active
):
    tournament = project()
    concept = {
        "id": "concept",
        "concept_name": "Staff",
        "active": active,
        "metadata": service.build_budget_concept_scope_metadata(["Operación"]),
    }
    session = Session(tournament, [concept])
    result = await update(session, {"etapas": "Montaje"}, etapas="Montaje")
    error = parse_qs(urlsplit(result.headers["location"]).query)["error_msg"][0]
    assert "Reasigne primero su alcance" in error
    assert session.commits == 0 and session.rollbacks == 1
    assert tournament.name == "HWC"
    assert tournament.etapas == ["Montaje", "Operación"]


@pytest.mark.asyncio
async def test_intentional_global_scope_does_not_block_label_removal(owner_guards):
    tournament = project()
    metadata = {
        **service.build_budget_concept_scope_metadata([]),
        "ssot_subproyecto": "Operación",
    }
    session = Session(
        tournament, [{"id": "global", "concept_name": "General", "metadata": metadata}]
    )
    result = await update(session, {"etapas": "Montaje"}, etapas="Montaje")
    assert "success_msg" in parse_qs(urlsplit(result.headers["location"]).query)
    assert session.commits == 1
    assert tournament.etapas == ["Montaje"]
    assert metadata["scope_mode"] == "global"


@pytest.mark.parametrize("labels", [["Operación"], ["operación"], ["Nacional"], []])
def test_explicit_labels_use_configured_phase_or_category(labels):
    service._validate_budget_scope_labels(labels, vars(project()))


def test_unknown_explicit_label_fails_closed():
    with pytest.raises(ValueError, match="ya no está configurado"):
        service._validate_budget_scope_labels(["Retirada"], vars(project()))


@pytest.mark.asyncio
async def test_scope_update_locks_and_rereads_current_metadata(monkeypatch):
    before = {"id": "concept", "tournament_id": str(TID), "metadata": {"note": "old"}}
    latest = {
        **before,
        "metadata": {"note": "new", **service.build_budget_concept_scope_metadata([])},
    }
    result = {
        **latest,
        "metadata": {
            "note": "new",
            **service.build_budget_concept_scope_metadata(["Operación"]),
        },
    }
    getter = AsyncMock(side_effect=[before, latest, result])
    resolver = AsyncMock(
        return_value={
            "tournament_id": str(TID),
            "etapas": ["Operación"],
            "categorias": [],
        }
    )
    monkeypatch.setattr(service, "get_budget_concept", getter)
    monkeypatch.setattr(service, "_resolve_tournament_for_budget_concept", resolver)
    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    updated = await service.update_budget_concept(
        session, concept_id="concept", scope_labels=["Operación"], ensure_schema=False
    )
    assert updated == result
    resolver.assert_awaited_once_with(session, tournament_id=str(TID), lock_scope=True)
    params = session.execute.await_args.args[1]
    import json

    assert json.loads(params["metadata"])["note"] == "new"


@pytest.mark.asyncio
async def test_non_scope_edit_preserves_legacy_scope_without_config_read(monkeypatch):
    metadata = {"applicable_phase_labels": ["Legacy no longer configured"]}
    current = {"id": "concept", "tournament_id": str(TID), "metadata": metadata}
    monkeypatch.setattr(service, "get_budget_concept", AsyncMock(return_value=current))
    resolver = AsyncMock()
    monkeypatch.setattr(service, "_resolve_tournament_for_budget_concept", resolver)
    session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
    await service.update_budget_concept(
        session, concept_id="concept", active=False, ensure_schema=False
    )
    resolver.assert_not_awaited()
    params = session.execute.await_args.args[1]
    assert "metadata" not in params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "injected",
    [
        {"scope_mode": "phase_scoped", "applicable_phase_keys": ["removed"]},
        {"applicable_phase_keys": ["removed"]},
        {"applicable_phase_labels": ["Retirada"]},
        {"applicable_subproject_labels": ["Retirada"]},
        {"applicable_subproject_keys": ["removed"]},
    ],
)
async def test_extra_metadata_cannot_reintroduce_removed_scope(monkeypatch, injected):
    monkeypatch.setattr(
        service,
        "_resolve_tournament_for_budget_concept",
        AsyncMock(
            return_value={
                "tournament_id": str(TID),
                "tournament_code": "HWC",
                "tournament_name": "HWC",
                "etapas": ["Montaje"],
                "categorias": [],
            }
        ),
    )

    class Empty:
        def mappings(self):
            return self

        def first(self):
            return None

    session = SimpleNamespace(
        execute=AsyncMock(return_value=Empty()), commit=AsyncMock()
    )
    with pytest.raises(ValueError, match="metadata no puede sustituir"):
        await service.create_budget_concept(
            session,
            tournament_id=str(TID),
            concept_name="Rejected",
            scope_labels=[],
            extra_metadata=injected,
            ensure_schema=False,
        )
    assert not any(
        "INSERT" in str(call.args[0]) for call in session.execute.await_args_list
    )
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_extra_provenance_and_matching_scope_are_preserved(monkeypatch):
    monkeypatch.setattr(
        service,
        "_resolve_tournament_for_budget_concept",
        AsyncMock(
            return_value={
                "tournament_id": str(TID),
                "tournament_code": "HWC",
                "tournament_name": "HWC",
                "etapas": ["Montaje"],
                "categorias": [],
            }
        ),
    )
    monkeypatch.setattr(
        service, "get_budget_concept", AsyncMock(return_value={"id": "new"})
    )

    class Empty:
        def mappings(self):
            return self

        def first(self):
            return None

    session = SimpleNamespace(
        execute=AsyncMock(return_value=Empty()), commit=AsyncMock()
    )
    await service.create_budget_concept(
        session,
        tournament_id=str(TID),
        concept_name="Allowed",
        scope_labels=["Montaje"],
        extra_metadata={
            "source_row": 17,
            **service.build_budget_concept_scope_metadata(["Montaje"]),
        },
        ensure_schema=False,
    )
    import json

    params = session.execute.await_args.args[1]
    metadata = json.loads(params["metadata"])
    assert metadata["source_row"] == 17
    assert metadata["applicable_phase_labels"] == ["Montaje"]


@pytest.mark.parametrize("etapas,categorias", [(None, None), ([], []), ([" "], [])])
def test_unconfigured_budget_accepts_only_the_existing_form_defaults(
    etapas, categorias
):
    from devnous.gastos.services.tournament_phase_service import (
        get_tournament_budget_scope_labels,
        get_tournament_etapas,
        get_tournament_scope_labels,
    )

    row = {"etapas": etapas, "categorias": categorias}
    offered = get_tournament_scope_labels(row) or get_tournament_etapas(row)
    assert get_tournament_budget_scope_labels(row) == offered
    for label in offered:
        service._validate_budget_scope_labels([label], row)
    with pytest.raises(ValueError, match="ya no está configurado"):
        service._validate_budget_scope_labels(["Unknown"], row)


def test_category_configuration_does_not_enable_unoffered_defaults():
    with pytest.raises(ValueError, match="ya no está configurado"):
        service._validate_budget_scope_labels(
            ["Colectiva"], {"etapas": [], "categorias": ["Juvenil"]}
        )
    service._validate_budget_scope_labels(
        ["Juvenil"], {"etapas": [], "categorias": ["Juvenil"]}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_configuring_phases_cannot_orphan_a_used_default(owner_guards, active):
    tournament = project()
    tournament.etapas = None
    tournament.categorias = None
    session = Session(
        tournament,
        [
            {
                "id": "default-scoped",
                "concept_name": "Staff",
                "active": active,
                "metadata": service.build_budget_concept_scope_metadata(["Colectiva"]),
            }
        ],
    )
    result = await update(session, {"etapas": "Custom"}, etapas="Custom")
    error = parse_qs(urlsplit(result.headers["location"]).query)["error_msg"][0]
    assert "Reasigne primero su alcance" in error
    assert session.commits == 0 and session.rollbacks == 1
    assert tournament.etapas is None and tournament.categorias is None


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_default_reference_survives_an_effective_option_preserving_transition(
    owner_guards, configured
):
    tournament = project()
    tournament.etapas = ["Colectiva"] if configured else None
    tournament.categorias = []
    session = Session(
        tournament,
        [
            {
                "id": "default-scoped",
                "concept_name": "Staff",
                "metadata": service.build_budget_concept_scope_metadata(["Colectiva"]),
            }
        ],
    )
    selected = "" if configured else "Colectiva\nCustom"
    result = await update(session, {"etapas": selected}, etapas=selected)
    assert "success_msg" in parse_qs(urlsplit(result.headers["location"]).query)
    assert session.commits == 1 and session.rollbacks == 0
