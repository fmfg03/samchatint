import hashlib
import os
from pathlib import Path
import time

import pytest

from samchat.assistant import router as assistant_router
from samchat.assistant import readonly_workspace as task_workspace
from samchat.assistant.agent_runtime import evaluate_runtime_tool_call
from samchat.assistant.readonly_workspace import (
    cleanup_expired_task_scopes,
    workspace_task_file_create,
    workspace_task_file_patch,
    workspace_task_file_read,
    workspace_task_file_replace,
    workspace_task_list,
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
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_TTL_SECONDS", "86400")
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
    assert read["bytes"] == len("controlled result\n")
    assert read["sha256"] == created["sha256"]
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
async def test_replace_requires_observed_sha_and_is_atomic(task_root: Path) -> None:
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="result.md",
        content="first",
    )
    replaced = await workspace_task_file_replace(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="result.md",
        expected_sha256=created["sha256"],
        content="second",
    )

    assert replaced["replaced"] is True
    assert replaced["created"] is False
    assert replaced["previous_sha256"] == created["sha256"]
    assert replaced["sha256"] == hashlib.sha256(b"second").hexdigest()
    read = await workspace_task_file_read(
        employee_id="emp-1", conversation_id="conv-1", path="result.md"
    )
    assert read["content"] == "second"
    assert next(task_root.rglob("result.md")).stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_replace_rejects_stale_sha_and_preserves_content(task_root: Path) -> None:
    await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="result.md",
        content="original",
    )
    with pytest.raises(ValueError, match="changed"):
        await workspace_task_file_replace(
            employee_id="emp-1",
            conversation_id="conv-1",
            path="result.md",
            expected_sha256="0" * 64,
            content="must-not-land",
        )
    read = await workspace_task_file_read(
        employee_id="emp-1", conversation_id="conv-1", path="result.md"
    )
    assert read["content"] == "original"
    assert not list(task_root.rglob(".replace-*"))


@pytest.mark.asyncio
async def test_replace_failure_cleans_temp_and_preserves_original(
    task_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="result.md",
        content="original",
    )

    def fail_replace(source, target):
        raise RuntimeError("simulated atomic replace failure")

    monkeypatch.setattr(task_workspace.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="simulated"):
        await workspace_task_file_replace(
            employee_id="emp-1",
            conversation_id="conv-1",
            path="result.md",
            expected_sha256=created["sha256"],
            content="must-not-land",
        )

    target = next(task_root.rglob("result.md"))
    assert target.read_text() == "original"
    assert not list(task_root.rglob(".replace-*"))


@pytest.mark.asyncio
async def test_replace_rejects_missing_other_scope_and_invalid_hash(
    task_root: Path,
) -> None:
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-1",
        path="result.md",
        content="original",
    )
    with pytest.raises(FileNotFoundError):
        await workspace_task_file_replace(
            employee_id="emp-1",
            conversation_id="conv-2",
            path="result.md",
            expected_sha256=created["sha256"],
            content="blocked",
        )


@pytest.mark.asyncio
async def test_task_list_is_same_scope_non_recursive_and_bounded(
    task_root: Path,
) -> None:
    for path, content in (
        ("notes/a.md", "a"),
        ("notes/b.txt", "b"),
        ("root.json", "{}"),
    ):
        await workspace_task_file_create(
            employee_id="emp-1",
            conversation_id="conv-list",
            path=path,
            content=content,
        )

    root_listing = await workspace_task_list(
        employee_id="emp-1", conversation_id="conv-list", limit=1
    )
    assert root_listing["path"] == "."
    assert len(root_listing["entries"]) == 1
    assert root_listing["entries"][0] == {
        "path": "notes",
        "kind": "directory",
        "bytes": None,
    }
    nested_listing = await workspace_task_list(
        employee_id="emp-1",
        conversation_id="conv-list",
        path="notes",
    )
    assert [entry["path"] for entry in nested_listing["entries"]] == [
        "notes/a.md",
        "notes/b.txt",
    ]
    with pytest.raises(FileNotFoundError):
        await workspace_task_list(
            employee_id="emp-1", conversation_id="other-conversation"
        )
    with pytest.raises(ValueError, match="relative"):
        await workspace_task_list(
            employee_id="emp-1",
            conversation_id="conv-list",
            path="../escape",
        )


@pytest.mark.asyncio
async def test_task_list_filters_symlinks_hidden_and_unsupported(
    task_root: Path,
) -> None:
    await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-list-filter",
        path="visible.md",
        content="visible",
    )
    scope = task_root / hashlib.sha256(b"emp-1:conv-list-filter").hexdigest()
    (scope / ".hidden.md").write_text("hidden", encoding="utf-8")
    (scope / "binary.bin").write_bytes(b"binary")
    outside = task_root / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (scope / "linked.md").symlink_to(outside)

    listing = await workspace_task_list(
        employee_id="emp-1", conversation_id="conv-list-filter"
    )
    assert [entry["path"] for entry in listing["entries"]] == ["visible.md"]


@pytest.mark.asyncio
async def test_patch_changes_one_exact_context_and_preserves_rest(
    task_root: Path,
) -> None:
    original = "title: Test\nstatus: pending\nowner: Ana\n"
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-patch",
        path="state.md",
        content=original,
    )
    patched = await workspace_task_file_patch(
        employee_id="emp-1",
        conversation_id="conv-patch",
        path="state.md",
        expected_sha256=created["sha256"],
        old_text="status: pending",
        new_text="status: complete",
    )
    read = await workspace_task_file_read(
        employee_id="emp-1", conversation_id="conv-patch", path="state.md"
    )

    assert patched["patched"] is True
    assert patched["created"] is False
    assert patched["occurrences_replaced"] == 1
    assert patched["previous_sha256"] == created["sha256"]
    assert read["content"] == "title: Test\nstatus: complete\nowner: Ana\n"
    assert read["sha256"] == patched["sha256"]
    assert next(task_root.rglob("state.md")).stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_patch_rejects_stale_missing_ambiguous_and_empty_result(
    task_root: Path,
) -> None:
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-patch-negative",
        path="state.md",
        content="same\nsame\n",
    )
    common = {
        "employee_id": "emp-1",
        "conversation_id": "conv-patch-negative",
        "path": "state.md",
        "expected_sha256": created["sha256"],
    }
    with pytest.raises(ValueError, match="changed"):
        await workspace_task_file_patch(
            **{**common, "expected_sha256": "0" * 64},
            old_text="same",
            new_text="other",
        )
    with pytest.raises(ValueError, match="not found"):
        await workspace_task_file_patch(**common, old_text="missing", new_text="other")
    with pytest.raises(ValueError, match="ambiguous"):
        await workspace_task_file_patch(**common, old_text="same", new_text="other")
    overlapping = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-patch-overlap",
        path="overlap.md",
        content="aaa",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        await workspace_task_file_patch(
            employee_id="emp-1",
            conversation_id="conv-patch-overlap",
            path="overlap.md",
            expected_sha256=overlapping["sha256"],
            old_text="aa",
            new_text="b",
        )
    with pytest.raises(ValueError, match="describe a change"):
        await workspace_task_file_patch(**common, old_text="same", new_text="same")
    single = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-patch-empty",
        path="single.md",
        content="only",
    )
    with pytest.raises(ValueError, match="size"):
        await workspace_task_file_patch(
            employee_id="emp-1",
            conversation_id="conv-patch-empty",
            path="single.md",
            expected_sha256=single["sha256"],
            old_text="only",
            new_text="",
        )
    read = await workspace_task_file_read(
        employee_id="emp-1",
        conversation_id="conv-patch-negative",
        path="state.md",
    )
    assert read["content"] == "same\nsame\n"
    assert not list(task_root.rglob(".patch-*"))


@pytest.mark.asyncio
async def test_patch_atomic_failure_preserves_original_and_cleans_temp(
    task_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await workspace_task_file_create(
        employee_id="emp-1",
        conversation_id="conv-patch-atomic",
        path="state.md",
        content="before",
    )

    def fail_replace(source, target):
        raise RuntimeError("simulated patch replace failure")

    monkeypatch.setattr(task_workspace.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="simulated"):
        await workspace_task_file_patch(
            employee_id="emp-1",
            conversation_id="conv-patch-atomic",
            path="state.md",
            expected_sha256=created["sha256"],
            old_text="before",
            new_text="after",
        )
    target = next(task_root.rglob("state.md"))
    assert target.read_text() == "before"
    assert not list(task_root.rglob(".patch-*"))
    with pytest.raises(ValueError, match="SHA-256"):
        await workspace_task_file_replace(
            employee_id="emp-1",
            conversation_id="conv-1",
            path="result.md",
            expected_sha256="not-a-hash",
            content="blocked",
        )


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_valid_scopes(
    task_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_TTL_SECONDS", "300")
    for conversation in ("old", "fresh"):
        await workspace_task_file_create(
            employee_id="emp-1",
            conversation_id=conversation,
            path="result.md",
            content=conversation,
        )
    scopes = sorted(task_root.iterdir())
    old_scope = next(
        scope for scope in scopes if (scope / "result.md").read_text() == "old"
    )
    fresh_scope = next(
        scope for scope in scopes if (scope / "result.md").read_text() == "fresh"
    )
    now = time.time()
    os.utime(old_scope, (now - 600, now - 600))
    unexpected = task_root / "not-a-task-scope"
    unexpected.mkdir()
    os.utime(unexpected, (now - 600, now - 600))
    linked_scope = task_root / ("f" * 64)
    linked_scope.symlink_to(fresh_scope, target_is_directory=True)

    result = cleanup_expired_task_scopes(now=now)

    assert result["removed"] == 1
    assert not old_scope.exists()
    assert fresh_scope.exists()
    assert unexpected.exists()
    assert linked_scope.is_symlink()


def test_cleanup_is_bounded_per_call(
    task_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASSISTANT_TASK_WORKSPACE_TTL_SECONDS", "300")
    now = time.time()
    for index in range(21):
        scope = task_root / f"{index:064x}"
        scope.mkdir()
        os.utime(scope, (now - 600, now - 600))

    result = cleanup_expired_task_scopes(now=now)

    assert result == {"removed": 20, "examined": 20}
    assert len(list(task_root.iterdir())) == 1


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
            {"type": "function", "function": {"name": "workspace_task_file_create"}},
            {"type": "function", "function": {"name": "workspace_task_file_patch"}},
            {"type": "function", "function": {"name": "workspace_task_file_replace"}},
        ],
        read_tools=set(),
        write_tools={
            "workspace_task_file_create",
            "workspace_task_file_patch",
            "workspace_task_file_replace",
        },
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
    replace_decision = evaluate_runtime_tool_call(
        tool_name="workspace_task_file_replace",
        args={
            "path": "result.md",
            "expected_sha256": "0" * 64,
            "content": "replacement",
        },
        role="empleado",
        registry=_task_registry(),
    )
    assert replace_decision["decision"] == "confirm"
    assert assistant_router._can_confirm_write(
        "workspace_task_file_replace", "empleado"
    )
    registered = assistant_router._assistant_tool_registry()[
        "workspace_task_file_replace"
    ]
    assert registered.surface == "workspace"
    assert registered.operation_type == "write"
    assert registered.requires_confirmation is True
    patch_decision = evaluate_runtime_tool_call(
        tool_name="workspace_task_file_patch",
        args={
            "path": "result.md",
            "expected_sha256": "0" * 64,
            "old_text": "before",
            "new_text": "after",
        },
        role="empleado",
        registry=_task_registry(),
    )
    assert patch_decision["decision"] == "confirm"
    assert patch_decision["requires_confirmation"] is True
    assert assistant_router._can_confirm_write("workspace_task_file_patch", "empleado")
    patch_registration = assistant_router._assistant_tool_registry()[
        "workspace_task_file_patch"
    ]
    assert patch_registration.surface == "workspace"
    assert patch_registration.operation_type == "write"
    assert patch_registration.requires_confirmation is True


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
    listing = await assistant_router._run_read_tool(
        "workspace_task_list",
        {"path": "."},
        gastos_session=None,
        tournament_key_default=None,
        current_employee_id="emp-1",
        current_conversation_id="conv-router",
    )
    assert listing["entries"][0]["path"] == "router.md"

    patched = await assistant_router._execute_write_tool(
        "workspace_task_file_patch",
        {
            "path": "router.md",
            "expected_sha256": read["sha256"],
            "old_text": "routed",
            "new_text": "patched",
        },
        gastos_session=None,
        conversation_id="conv-router",
        empleado_id="emp-1",
        tournament_key_default=None,
    )
    assert patched["patched"] is True
    patched_read = await assistant_router._run_read_tool(
        "workspace_task_file_read",
        {"path": "router.md"},
        gastos_session=None,
        tournament_key_default=None,
        current_employee_id="emp-1",
        current_conversation_id="conv-router",
    )
    assert patched_read["content"] == "patched"

    replaced = await assistant_router._execute_write_tool(
        "workspace_task_file_replace",
        {
            "path": "router.md",
            "expected_sha256": patched_read["sha256"],
            "content": "replaced",
        },
        gastos_session=None,
        conversation_id="conv-router",
        empleado_id="emp-1",
        tournament_key_default=None,
    )
    assert replaced["replaced"] is True
    with pytest.raises(assistant_router.HTTPException) as stale:
        await assistant_router._execute_write_tool(
            "workspace_task_file_replace",
            {
                "path": "router.md",
                "expected_sha256": patched_read["sha256"],
                "content": "stale-replacement",
            },
            gastos_session=None,
            conversation_id="conv-router",
            empleado_id="emp-1",
            tournament_key_default=None,
        )
    assert stale.value.status_code == 409

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
    assert "primero usa workspace_task_file_read" in system_prompt
    assert "prefiere workspace_task_file_patch" in system_prompt
    assert "usa workspace_task_list" in system_prompt
    assert "usa workspace_task_*" in route_prompt
    assert "confirmacion al gate integrado" in route_prompt
