import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check-registration-operational-surface.py"
)
SPEC = importlib.util.spec_from_file_location("registration_surface_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_only_operational_python_paths_are_in_scope():
    assert MODULE.is_operational_path("scripts/seed_roster.py")
    assert MODULE.is_operational_path("alternate_bot.py")
    assert MODULE.is_operational_path("tools/import_players.py")
    assert not MODULE.is_operational_path("src/devnous/copa_telmex/database.py")
    assert not MODULE.is_operational_path("tests/unit/test_database.py")
    assert not MODULE.is_operational_path("run_copa_telmex.py")


def test_registration_primitive_mutation_is_denied():
    assert MODULE.mutation_reasons("await db.create_player(team_id=team.id)")
    assert MODULE.mutation_reasons("await db.update_team(team_id, name='x')")


def test_raw_sql_and_direct_orm_mutation_are_denied():
    raw = 'await session.execute(text("DELETE FROM copa_telmex_players"))'
    direct = """
from devnous.copa_telmex.models import Team
session.add(Team(name="unsafe"))
"""
    assert "RAW_REGISTRATION_TABLE_MUTATION" in MODULE.mutation_reasons(raw)
    assert "DIRECT_ORM_SESSION_MUTATION" in MODULE.mutation_reasons(direct)


def test_read_only_operational_query_is_not_denied():
    source = """
from devnous.copa_telmex.database import CopaTelmexDB
teams = await db.get_teams_by_chat(chat_id)
"""
    assert MODULE.mutation_reasons(source) == []
