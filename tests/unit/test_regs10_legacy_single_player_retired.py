from __future__ import annotations

import ast
import importlib.util
import inspect
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.tournaments.core.operations_module import (
    REGS10_LEGACY_SINGLE_PLAYER_RETIRED,
    OperationsModule,
)


CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check-registration-operational-surface.py"
)
CHECKER_SPEC = importlib.util.spec_from_file_location("regs10_surface_guard", CHECKER_PATH)
CHECKER = importlib.util.module_from_spec(CHECKER_SPEC)
assert CHECKER_SPEC.loader is not None
CHECKER_SPEC.loader.exec_module(CHECKER)


def jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 40), (220, 220, 220)).save(output, format="JPEG")
    return output.getvalue()


def test_legacy_single_player_finalizers_and_handlers_are_removed() -> None:
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
        "_save_to_database",
        "_send_final_confirmation",
        "_legacy_single_player_ocr",
        "_call_claude_vision",
        "_request_human_verification",
    }

    assert not retired.intersection(definitions)
    assert not retired.intersection(calls)


@pytest.mark.asyncio
async def test_legacy_provider_is_denied_without_ocr_or_database_access() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.ocr_enabled = True
    operations.ocr_provider = "claude_vision"
    operations.pending_back_photos = {}
    operations.db = object()

    message = SimpleNamespace(chat_id=77, user_id=88, photo=jpeg_bytes())
    response = await operations.process_ocr_registration(message)

    assert REGS10_LEGACY_SINGLE_PLAYER_RETIRED in response
    assert "no puede crear equipos o jugadores" in response


@pytest.mark.asyncio
async def test_historical_legacy_callbacks_are_denied_explicitly() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.pending_back_photos = {}
    answers = []
    messages = []

    class Adapter:
        async def answer_callback_query(self, callback_id, text):
            answers.append((callback_id, text))

        async def send_message(self, chat_id, text, **kwargs):
            messages.append((chat_id, text))

    await operations.handle_callback_query(
        {
            "id": "callback-1",
            "message": {"chat": {"id": 77}},
            "data": "confirm_0_Jugador Uno",
        },
        Adapter(),
    )

    assert answers == [("callback-1", "Ruta de registro retirada")]
    assert len(messages) == 1
    assert REGS10_LEGACY_SINGLE_PLAYER_RETIRED in messages[0][1]


def test_startup_guard_rejects_reintroduced_legacy_finalizer(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
class OperationsModule:
    async def process_ocr_registration(self):
        if self.ocr_provider == "claude_vision":
            return "REGS10_LEGACY_SINGLE_PLAYER_RETIRED"

    async def handle_callback_query(self):
        return "confirm_ use_detected_ write_manually REGS10_LEGACY_SINGLE_PLAYER_RETIRED"

    async def _save_to_database(self):
        return True
''',
        encoding="utf-8",
    )

    assert CHECKER.regs10_retirement_reasons(tmp_path) == [
        "REGS10_LEGACY_FINALIZER_PRESENT"
    ]


def test_startup_guard_accepts_explicit_legacy_retirement(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
class OperationsModule:
    async def process_ocr_registration(self):
        if self.ocr_provider == "claude_vision":
            return "REGS10_LEGACY_SINGLE_PLAYER_RETIRED"

    async def handle_callback_query(self):
        return "confirm_ use_detected_ write_manually REGS10_LEGACY_SINGLE_PLAYER_RETIRED"
''',
        encoding="utf-8",
    )

    assert CHECKER.regs10_retirement_reasons(tmp_path) == []
