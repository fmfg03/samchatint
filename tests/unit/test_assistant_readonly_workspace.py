from pathlib import Path

import pytest
from fastapi import HTTPException

from samchat.assistant import router as assistant_router
from samchat.assistant.readonly_workspace import (
    readonly_workspace_allowed,
    workspace_file_read,
    workspace_list,
    workspace_search,
)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "brief.md").write_text("Riesgo alto\nSiguiente paso\n", encoding="utf-8")
    (root / "data.json").write_text('{"estado":"abierto"}\n', encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / "credentials.yaml").write_text("token: secret\n", encoding="utf-8")
    (root / "certificate.pem").write_text("private\n", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_READONLY_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_READONLY_WORKSPACE_EMPLOYEE_IDS", "EMP-1")
    monkeypatch.setenv("ASSISTANT_READONLY_WORKSPACE_ROOT", str(root))
    return root


def test_workspace_requires_flag_and_explicit_case_insensitive_cohort(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert readonly_workspace_allowed("emp-1") is True
    assert readonly_workspace_allowed("emp-2") is False
    monkeypatch.delenv("ASSISTANT_READONLY_WORKSPACE_EMPLOYEE_IDS")
    assert readonly_workspace_allowed("emp-1") is False
    monkeypatch.setenv("ASSISTANT_READONLY_WORKSPACE_EMPLOYEE_IDS", "emp-1")
    monkeypatch.setenv("ASSISTANT_READONLY_WORKSPACE_ENABLED", "false")
    assert readonly_workspace_allowed("emp-1") is False


@pytest.mark.asyncio
async def test_list_and_read_return_only_bounded_relative_text(workspace: Path) -> None:
    listing = await workspace_list()
    assert [entry["path"] for entry in listing["entries"]] == ["brief.md", "data.json"]
    result = await workspace_file_read(path="brief.md", end_line=1)
    assert result["path"] == "brief.md"
    assert result["content"] == "1: Riesgo alto"
    assert str(workspace) not in str(result)


@pytest.mark.asyncio
async def test_search_is_in_process_bounded_and_relative(workspace: Path) -> None:
    result = await workspace_search(query="riesgo")
    assert result["matches"] == [{"path": "brief.md", "line": 1, "text": "Riesgo alto"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("../outside.txt", "/etc/passwd", ".env"))
async def test_read_rejects_escape_absolute_and_hidden_paths(
    workspace: Path, path: str
) -> None:
    with pytest.raises(ValueError):
        await workspace_file_read(path=path)


@pytest.mark.asyncio
async def test_read_rejects_symlinks(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        await workspace_file_read(path="link.txt")


@pytest.mark.asyncio
async def test_read_rejects_binary_and_oversized_files(workspace: Path) -> None:
    (workspace / "image.png").write_bytes(b"png")
    (workspace / "large.txt").write_bytes(b"x" * 131_073)
    with pytest.raises(ValueError, match="type"):
        await workspace_file_read(path="image.png")
    with pytest.raises(ValueError, match="limit"):
        await workspace_file_read(path="large.txt")


@pytest.mark.asyncio
async def test_router_executor_requires_the_independent_workspace_cohort(
    workspace: Path,
) -> None:
    result = await assistant_router._run_read_tool(
        "workspace_file_read",
        {"path": "brief.md", "end_line": 1},
        gastos_session=None,
        tournament_key_default=None,
        current_role="empleado",
        current_employee_id="emp-1",
    )
    assert result["content"] == "1: Riesgo alto"

    with pytest.raises(HTTPException) as denied:
        await assistant_router._run_read_tool(
            "workspace_file_read",
            {"path": "brief.md"},
            gastos_session=None,
            tournament_key_default=None,
            current_role="superadmin",
            current_employee_id="emp-2",
        )
    assert denied.value.status_code == 403


def test_workspace_tools_are_registered_as_read_only_workspace_surface() -> None:
    registry = assistant_router._assistant_tool_registry()
    assert {
        name: (registry[name].surface, registry[name].operation_type)
        for name in assistant_router.WORKSPACE_READ_TOOLS
    } == {
        "workspace_file_read": ("workspace", "read"),
        "workspace_list": ("workspace", "read"),
        "workspace_search": ("workspace", "read"),
        "workspace_task_list": ("workspace", "read"),
        "workspace_task_file_read": ("workspace", "read"),
    }

    task_write = registry["workspace_task_file_create"]
    assert (task_write.surface, task_write.operation_type) == ("workspace", "write")
    assert task_write.requires_confirmation is True
