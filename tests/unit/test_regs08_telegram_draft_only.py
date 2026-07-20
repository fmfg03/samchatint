from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path

import pytest

from devnous.tournaments.core.operations_module import (
    REGS08_GOVERNED_REVIEW_UNAVAILABLE,
    OperationsModule,
)


CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "check-registration-operational-surface.py"
)
CHECKER_SPEC = importlib.util.spec_from_file_location("regs08_surface_guard", CHECKER_PATH)
CHECKER = importlib.util.module_from_spec(CHECKER_SPEC)
assert CHECKER_SPEC.loader is not None
CHECKER_SPEC.loader.exec_module(CHECKER)


def test_direct_telegram_registration_finalizer_is_removed() -> None:
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

    assert "_save_registration_form_to_database" not in definitions
    assert "_save_registration_form_to_database" not in calls


def test_new_telegram_keyboards_expose_precapture_not_direct_save() -> None:
    source = inspect.getsource(OperationsModule)

    assert '"callback_data": f"save_ocr:' not in source
    assert '"callback_data": "save_ocr:' not in source
    assert "💾 Guardar" not in source
    assert "stage_ocr:" in source


@pytest.mark.asyncio
async def test_staging_fails_closed_when_governed_review_is_unavailable() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations._web_review_enabled = lambda: False
    called = False

    async def forbidden_create(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("governed review creation must not be called")

    operations._create_web_review_session_from_pending = forbidden_create

    ok, reason = await operations._stage_pending_registration_review(77, "openai")

    assert ok is False
    assert REGS08_GOVERNED_REVIEW_UNAVAILABLE in reason
    assert "no se creó ningún equipo o jugador" in reason
    assert called is False


@pytest.mark.asyncio
async def test_staging_routes_only_to_governed_review_draft() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations._web_review_enabled = lambda: True
    seen = {}

    async def create_review(chat_id, provider, *, expect_back_photo):
        seen.update(
            chat_id=chat_id,
            provider=provider,
            expect_back_photo=expect_back_photo,
        )
        return True, "https://sam.chat/registration-review/regs08-witness"

    operations._create_web_review_session_from_pending = create_review

    ok, url = await operations._stage_pending_registration_review(77, "openai")

    assert ok is True
    assert url.endswith("/regs08-witness")
    assert seen == {
        "chat_id": 77,
        "provider": "openai",
        "expect_back_photo": True,
    }


def test_startup_guard_rejects_reintroduced_direct_finalizer(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
class OperationsModule:
    async def _stage_pending_registration_review(self):
        await self._create_web_review_session_from_pending()

    async def _save_registration_form_to_database(self):
        return True

STAGE = "stage_ocr:openai"
""",
        encoding="utf-8",
    )

    assert CHECKER.regs08_retirement_reasons(tmp_path) == [
        "REGS08_DIRECT_FINALIZER_PRESENT"
    ]


def test_startup_guard_accepts_governed_precapture_only(tmp_path: Path) -> None:
    source = tmp_path / "src/devnous/tournaments/core/operations_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
class OperationsModule:
    async def _stage_pending_registration_review(self):
        await self._create_web_review_session_from_pending()

STAGE = "stage_ocr:openai"
""",
        encoding="utf-8",
    )

    assert CHECKER.regs08_retirement_reasons(tmp_path) == []
