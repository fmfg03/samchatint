from pathlib import Path

import pytest

from samchat.assistant import router as assistant_router
from samchat.assistant.agent_runtime import evaluate_runtime_tool_call
from samchat.assistant.readonly_workspace import (
    workspace_task_file_create,
    workspace_task_file_read,
    workspace_task_mutation_allowed,
)
from samchat.assistant.tool_registry import build_tool_registry


@pytest.fixture()
def task_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "tasks"
    root.mkdir()
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_MUTATIONS_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_EMPLOYEE_IDS", "EMP-1")
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_ROOT", str(root))
    return root


def test_task_mutations_require_independent_case_insensitive_cohort(
    task_root: Path,
) -> None:
    assert workspace_task_mutation_allowed("emp-1")
    assert not workspace_task_mutation_allowed("emp-2")


@pytest.mark.asyncio
async def test_create_is_scoped_and_readable_only_by_same_conversation(
    task_root: Path,
) -> None:
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="notes/result.md",
        content="controlled result\n",
    )
    assert created["created"] is True
    assert created["overwritten"] is False
    assert created["path"] == "notes/result.md"
    read = await workspace_task_file_read(
        employee_id="emp-1", conversation_id="conv-1", path="notes/result.md"
    )
    assert read["content"] == "controlled result\n"
    with pytest.raises(FileNotFoundError):
        await workspace_task_file_read(
            employee_id="emp-1", conversation_id="conv-2", path="notes/result.md"
        )


@pytest.mark.asyncio
async def test_create_never_overwrites(task_root: Path) -> None:
    kwargs = {
        "employee_id": "emp-1",
        "conversation_id": "conv-1",
        "path": "result.md",
        "content": "first",
    }
    await workspace_task_file_create(**kwargs)
    with pytest.raises(FileExistsError):
        await workspace_task_file_create(**{**kwargs, "content": "second"})


@pytest.mark.asyncio
async def test_read_rejects_symlink_even_inside_scope(task_root: Path) -> None:
    await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="source.md",
        content="source",
    )
    scope = next(task_root.iterdir())
    (scope / "link.md").symlink_to(scope / "source.md")

    with pytest.raises(ValueError, match="symlinks"):
        await workspace_task_file_read(
            employee_id="emp-1", conversation_id="conv-1", path="link.md"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("../escape.md", "/tmp/escape.md", ".secret.md"))
async def test_create_rejects_unsafe_paths(task_root: Path, path: str) -> None:
    with pytest.raises(ValueError):
        await workspace_task_file_create(
            employee_id="emp-1", conversation_id="conv-1", path=path, content="x"
        )


@pytest.mark.asyncio
async def test_create_rejects_other_subject_and_large_content(task_root: Path) -> None:
    with pytest.raises(PermissionError):
        await workspace_task_file_create(
            employee_id="emp-2", conversation_id="conv-1", path="x.md", content="x"
        )
    with pytest.raises(ValueError, match="size"):
        await workspace_task_file_create(
            employee_id="emp-1",
            conversation_id="conv-1",
            path="large.md",
            content="x" * 65_537,
        )


def _task_registry():
    return build_tool_registry(
        tool_defs=[
            {"type": "function", "function": {"name": "workspace_task_file_create"}}
        ],
        read_tools=set(),
        write_tools={"workspace_task_file_create"},
        finance_tools=set(),
        tournament_tools=set(),
        dev_tools=set(),
    )


def test_task_workspace_write_still_requires_confirmation(
    task_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_READONLY_ONLY", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_WRITES_ENABLED", "false")

    decision = evaluate_runtime_tool_call(
        tool_name="workspace_task_file_create",
        args={"path": "result.md", "content": "result"},
        role="empleado",
        registry=_task_registry(),
    )

    assert decision["decision"] == "confirm"
    assert decision["requires_confirmation"] is True
    assert decision["allowed"] is False
    assert assistant_router._can_confirm_write("workspace_task_file_create", "empleado")


@pytest.mark.asyncio
async def test_router_executor_rechecks_cohort_and_scopes_conversation(
    task_root: Path,
) -> None:
    created = await assistant_router._execute_write_tool(
        "workspace_task_file_create",
        {"path": "router.md", "content": "routed"},
        gastos_session=None,
        conversation_id="conv-router",
        empleado_id="emp-1",
        tournament_key_default=None,
    )
    assert created["created"] is True

    read = await assistant_router._run_read_tool(
        "workspace_task_file_read",
        {"path": "router.md"},
        gastos_session=None,
        tournament_key_default=None,
        current_employee_id="emp-1",
        current_conversation_id="conv-router",
    )
    assert read["content"] == "routed"

    with pytest.raises(FileNotFoundError):
        await assistant_router._run_read_tool(
            "workspace_task_file_read",
            {"path": "router.md"},
            gastos_session=None,
            tournament_key_default=None,
            current_employee_id="emp-1",
            current_conversation_id="other-conversation",
        )


def test_task_workspace_write_is_denied_without_independent_flag(
    task_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASSISTANT_AGENT_RUNTIME_READONLY_ONLY", "true")
    monkeypatch.setenv("ASSISTANT_AGENT_WRITES_ENABLED", "false")
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_MUTATIONS_ENABLED", "false")

    decision = evaluate_runtime_tool_call(
        tool_name="workspace_task_file_create",
        args={"path": "result.md", "content": "result"},
        role="empleado",
        registry=_task_registry(),
    )

    assert decision["decision"] == "deny"
    assert decision["reason"] == "runtime_readonly_write_blocked"


def test_prompts_route_task_workspace_to_integrated_confirmation_gate() -> None:
    system_prompt = assistant_router._assistant_system_prompt()
    route_prompt = assistant_router._assistant_route_system_prompt(
        {"route": "code_agentic", "domain": "code"}
    )

    assert "no pidas una confirmacion conversacional adicional" in system_prompt
    assert "no son ediciones del repositorio" in system_prompt
    assert "usa workspace_task_*" in route_prompt
    assert "confirmacion al gate integrado" in route_prompt
