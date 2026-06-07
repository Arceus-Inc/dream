"""Default ``task_create`` tool — Spec 07 wiring slice.

Wraps :meth:`BackgroundTaskManager.create_shell_task` so the engine loop can
spawn background ``local_bash`` tasks. Mutating (the subprocess is real),
tier-1. Pulls the per-session manager out of the typed
:class:`TaskSessionContext` placed on ``ctx.metadata`` — no globals.

Scope is deliberately narrow: only ``local_bash``. ``local_agent`` requires
manager plumbing that does not exist yet; the tool refuses other task types
with a structured error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.task_create import TaskCreateTool


def _ctx(
    working_dir: Path, task_ctx: TaskSessionContext | None = None
) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if task_ctx is not None:
        put_task_context(metadata, task_ctx)
    return ToolExecutionContext(
        working_dir=working_dir, session_id="s_test", metadata=metadata
    )


def _session(tmp_path: Path) -> TaskSessionContext:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    return TaskSessionContext(manager=manager)


# --- declaration -----------------------------------------------------------


def test_declaration_is_mutating_tier_1() -> None:
    tool = TaskCreateTool()
    assert tool.name == "task_create"
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required == 1
    # not declared read-only (it really does spawn a process)
    assert tool.is_read_only() is False


# --- happy path ------------------------------------------------------------


async def test_creates_shell_task_with_command(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {"description": "echo hi", "command": "echo hi"},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is False
    # one task now exists in the manager
    tasks = sc.manager.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].command == "echo hi"
    # the result content surfaces the task id and the type
    assert tasks[0].id in result.content
    assert tasks[0].type in result.content
    # structured metadata so callers can parse without scraping content
    assert result.metadata.get("task_id") == tasks[0].id
    assert result.metadata.get("task_type") == "local_bash"
    # let watcher finish so pytest unraisable warnings don't fire
    await sc.manager.stop_task(tasks[0].id)


async def test_creates_shell_task_with_argv(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {"description": "list dir", "argv": ["cmd", "/c", "echo", "hi"]},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is False
    tasks = sc.manager.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].argv == ("cmd", "/c", "echo", "hi")
    await sc.manager.stop_task(tasks[0].id)


async def test_uses_ctx_working_dir_when_no_cwd_given(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {"description": "pwd", "command": "echo hi"},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is False
    tasks = sc.manager.list_tasks()
    assert Path(tasks[0].cwd) == tmp_path.resolve()
    await sc.manager.stop_task(tasks[0].id)


async def test_explicit_cwd_overrides_ctx(tmp_path: Path) -> None:
    sub = tmp_path / "elsewhere"
    sub.mkdir()
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {"description": "pwd elsewhere", "command": "echo hi", "cwd": str(sub)},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is False
    tasks = sc.manager.list_tasks()
    assert Path(tasks[0].cwd) == sub.resolve()
    await sc.manager.stop_task(tasks[0].id)


# --- structured errors -----------------------------------------------------


async def test_missing_task_context_is_structured_error(tmp_path: Path) -> None:
    result = await TaskCreateTool().execute(
        {"description": "x", "command": "echo hi"},
        _ctx(tmp_path, None),
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_neither_command_nor_argv_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {"description": "x"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "command" in result.content.lower() or "argv" in result.content.lower()
    assert "root_cause" in result.metadata


async def test_both_command_and_argv_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {"description": "x", "command": "echo hi", "argv": ["echo"]},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_unsupported_task_type_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskCreateTool().execute(
        {
            "description": "x",
            "command": "echo hi",
            "task_type": "local_agent",
        },
        _ctx(tmp_path, sc),
    )
    assert result.is_error is True
    assert "local_agent" in result.content
    assert "root_cause" in result.metadata


async def test_invalid_input_schema_raises(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    # description missing → pydantic validation error
    with pytest.raises(Exception):
        await TaskCreateTool().execute({"command": "echo hi"}, _ctx(tmp_path, sc))
