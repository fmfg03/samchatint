from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from devnous.tournaments.core.operations_module import (
    REGS09_REVIEW_SESSION_REQUIRED,
    OperationsModule,
)


CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check-registration-operational-surface.py"
)
CHECKER_SPEC = importlib.util.spec_from_file_location("regs09_surface_guard", CHECKER_PATH)
CHECKER = importlib.util.module_from_spec(CHECKER_SPEC)
assert CHECKER_SPEC.loader is not None
CHECKER_SPEC.loader.exec_module(CHECKER)


def test_direct_backpage_finalizer_is_removed() -> None:
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

    assert "_append_players_to_team" not in definitions
    assert "_append_players_to_team" not in calls


@pytest.mark.asyncio
async def test_legacy_team_id_state_fails_before_ocr_and_is_purged() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.pending_back_photos = {99: {"team_id": "legacy-team"}}
    ocr_called = False

    async def forbidden_extract(*args, **kwargs):
        nonlocal ocr_called
        ocr_called = True
        raise AssertionError("OCR must not run without governed review authority")

    operations._extract_registration_form = forbidden_extract

    response = await operations._process_back_photo(
        chat_id=99,
        user_id=42,
        optimized_bytes=b"image",
        image_b64="aW1hZ2U=",
        provider="openai",
    )

    assert REGS09_REVIEW_SESSION_REQUIRED in response
    assert "No se ejecutó OCR" in response
    assert ocr_called is False
    assert 99 not in operations.pending_back_photos


@pytest.mark.asyncio
async def test_governed_backpage_routes_only_through_regs04_append() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.pending_back_photos = {
        99: {
            "review_session_id": "session-1",
            "page_count": 1,
            "max_pages": 3,
        }
    }
    seen = {}

    async def fake_extract(provider, optimized_bytes, image_b64):
        return SimpleNamespace(players=[]), {"provider": provider}

    async def fake_append(**kwargs):
        seen.update(kwargs)
        return True, "https://sam.chat/registration-review/session-1"

    operations._extract_registration_form = fake_extract
    operations._append_back_photo_to_review_session = fake_append
    operations._telegram_review_max_pages = lambda: 3

    response = await operations._process_back_photo(
        chat_id=99,
        user_id=42,
        optimized_bytes=b"image",
        image_b64="aW1hZ2U=",
        provider="openai",
    )

    assert "Página agregada" in response
    assert seen["review_session_id"] == "session-1"
    assert seen["provider"] == "openai"
    assert operations.pending_back_photos[99]["page_count"] == 2


def test_startup_guard_rejects_direct_backpage_finalizer(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
REGS09_REVIEW_SESSION_REQUIRED = "REGS09_REVIEW_SESSION_REQUIRED"

class OperationsModule:
    async def _process_back_photo(self):
        await self._append_back_photo_to_review_session()
        await self._append_players_to_team()

    async def _append_players_to_team(self):
        return True
''',
        encoding="utf-8",
    )

    assert CHECKER.regs09_retirement_reasons(tmp_path) == [
        "REGS09_DIRECT_BACKPAGE_FINALIZER_PRESENT"
    ]


def test_startup_guard_accepts_regs04_only_route(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
REGS09_REVIEW_SESSION_REQUIRED = "REGS09_REVIEW_SESSION_REQUIRED"

class OperationsModule:
    async def _process_back_photo(self):
        await self._append_back_photo_to_review_session()
''',
        encoding="utf-8",
    )

    assert CHECKER.regs09_retirement_reasons(tmp_path) == []
