from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from devnous.tournaments.core.operations_module import (
    REGS11_MANUAL_PLAYER_CREATION_RETIRED,
    OperationsModule,
)


CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check-registration-operational-surface.py"
)
CHECKER_SPEC = importlib.util.spec_from_file_location("regs11_surface_guard", CHECKER_PATH)
CHECKER = importlib.util.module_from_spec(CHECKER_SPEC)
assert CHECKER_SPEC.loader is not None
CHECKER_SPEC.loader.exec_module(CHECKER)


def test_manual_player_writer_parser_and_onboarding_are_removed() -> None:
    source = inspect.getsource(OperationsModule)
    tree = ast.parse(source)
    definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    retired = {
        "_create_manual_player",
        "_continue_player_onboarding",
        "_parse_manual_player_payload",
    }

    assert not retired.intersection(definitions)
    assert not retired.intersection(calls)
    assert "pending_player_onboarding" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_text",
    (
        "dar de alta jugador Juan Uno, 01/01/2010",
        "agregar jugador al equipo Academicos",
        "registrar jugador que no viene en la cédula",
    ),
)
async def test_manual_player_requests_are_denied_without_backend_access(message_text: str) -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.db = object()

    response = await operations._handle_conversational_actions(77, 88, message_text)

    assert REGS11_MANUAL_PLAYER_CREATION_RETIRED in response
    assert "precaptura gobernada" in response


@pytest.mark.asyncio
async def test_handle_intercepts_manual_player_before_other_conversation_routes() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.pending_saves = {}
    operations.pending_edits = {}

    async def no_ai(message):
        return None

    async def forbidden(*args, **kwargs):
        raise AssertionError("manual-player request reached a downstream route")

    operations._handle_ai_workspace_commands = no_ai
    operations._apply_freeform_corrections = forbidden
    operations._handle_conversational_query = forbidden

    response = await operations.handle(
        SimpleNamespace(
            text="dar de alta jugador Juan Uno, 01/01/2010",
            chat_id=77,
            user_id=88,
            photo=None,
        )
    )

    assert REGS11_MANUAL_PLAYER_CREATION_RETIRED in response


def test_startup_guard_rejects_reintroduced_manual_player_writer(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
class OperationsModule:
    async def handle(self):
        await self._handle_conversational_actions()

    async def _handle_conversational_actions(self):
        return "dar de alta agregar jugador registrar jugador REGS11_MANUAL_PLAYER_CREATION_RETIRED"

    async def _create_manual_player(self):
        return True
''',
        encoding="utf-8",
    )

    assert CHECKER.regs11_retirement_reasons(tmp_path) == [
        "REGS11_MANUAL_PLAYER_WRITER_PRESENT"
    ]


def test_startup_guard_accepts_retired_manual_player_surface(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
class OperationsModule:
    async def handle(self):
        await self._handle_conversational_actions()

    async def _handle_conversational_actions(self):
        return "dar de alta agregar jugador registrar jugador REGS11_MANUAL_PLAYER_CREATION_RETIRED"
''',
        encoding="utf-8",
    )

    assert CHECKER.regs11_retirement_reasons(tmp_path) == []
