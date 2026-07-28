from __future__ import annotations

import subprocess
import sys
import inspect
from pathlib import Path

from devnous.gastos.services import finance_training_seed_service
from scripts.check_tournament_write_boundaries import audit_source

ROOT = Path(__file__).resolve().parents[2]


def test_gate_rejects_unknown_tournament_constructor() -> None:
    findings = audit_source(
        """
from devnous.gastos.models import Tournament
def surprise_writer():
    return Tournament(name='bypass')
""",
        path="src/example.py",
    )
    assert [(item.rule, item.function) for item in findings] == [
        ("orm-constructor", "surprise_writer")
    ]


def test_gate_rejects_raw_sql_and_model_mutation_alias() -> None:
    findings = audit_source(
        """
from devnous.gastos.models import Tournament as LocalProject
from sqlalchemy import delete
def surprise_writer(session):
    session.execute(delete(LocalProject))
    session.execute('UPDATE tournaments SET active = false')
""",
        path="src/example.py",
    )
    assert {item.rule for item in findings} == {
        "orm-mutation",
        "raw-sql-mutation",
    }


def test_gate_rejects_module_qualified_constructor() -> None:
    findings = audit_source(
        """
import devnous.gastos.models as models
def surprise_writer():
    return models.Tournament(name='bypass')
""",
        path="src/example.py",
    )
    assert {item.rule for item in findings} == {"orm-constructor"}


def test_gate_rejects_module_qualified_update_and_delete() -> None:
    findings = audit_source(
        """
import devnous.gastos.models as models
from devnous.gastos import models as m
from sqlalchemy import delete, update
def surprise_writer(session):
    session.execute(update(models.Tournament).values(active=False))
    session.execute(delete(m.Tournament))
""",
        path="src/example.py",
    )
    assert [item.rule for item in findings] == ["orm-mutation", "orm-mutation"]


def test_gate_rejects_fully_qualified_constructor_without_alias() -> None:
    findings = audit_source(
        """
import devnous.gastos.models
def surprise_writer():
    return devnous.gastos.models.Tournament(name='bypass')
""",
        path="src/example.py",
    )
    assert {item.rule for item in findings} == {"orm-constructor"}


def test_gate_tracks_arbitrary_selected_row_name_for_write_and_delete() -> None:
    findings = audit_source(
        """
from devnous.gastos.models import Tournament
from sqlalchemy import select
def surprise_writer(session):
    result = session.execute(select(Tournament))
    project = result.scalar_one()
    project.active = False
    session.delete(project)
""",
        path="src/example.py",
    )
    assert {item.rule for item in findings} == {
        "model-attribute-write",
        "session-delete",
    }


def test_gate_tracks_row_loaded_with_async_session_get() -> None:
    findings = audit_source(
        """
from devnous.gastos.models import Tournament
async def surprise_writer(session, tournament_id):
    row = await session.get(Tournament, tournament_id)
    row.active = False
    await session.delete(row)
""",
        path="src/example.py",
    )
    assert {item.rule for item in findings} == {
        "model-attribute-write",
        "session-delete",
    }


def test_gate_does_not_taint_objects_built_from_protected_row_scalars() -> None:
    findings = audit_source(
        """
from devnous.gastos.models import Tournament
from sqlalchemy import select
def legitimate_reader(session):
    result = session.execute(select(Tournament))
    tournament = result.scalar_one()
    project_name = tournament.name
    expense = create_expense(project=project_name)
    expense.documento_id = 'document-id'
""",
        path="src/example.py",
    )
    assert findings == []


def test_gate_rejects_table_update_and_dynamic_sql() -> None:
    findings = audit_source(
        """
from devnous.gastos.models import Tournament
def surprise_writer(session, value):
    session.execute(Tournament.__table__.update())
    session.execute(f'UPDATE public.tournaments SET name = {value}')
""",
        path="src/example.py",
    )
    assert {item.rule for item in findings} == {
        "orm-table-mutation",
        "dynamic-sql-mutation",
    }


def test_repository_local_gastos_project_writer_inventory_is_closed() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_tournament_write_boundaries.py", "."],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_finance_training_cleanup_guards_targets_before_delete() -> None:
    cleanup = inspect.getsource(
        finance_training_seed_service.cleanup_finance_training_dataset
    )
    bulk_cleanup = inspect.getsource(
        finance_training_seed_service._delete_finance_training_id_sets
    )
    assert cleanup.index("require_ungoverned_gastos_project") < cleanup.index(
        "delete(Tournament)"
    )
    assert bulk_cleanup.index("require_ungoverned_gastos_project") < bulk_cleanup.index(
        "delete(Tournament)"
    )
