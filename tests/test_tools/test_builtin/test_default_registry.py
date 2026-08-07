"""Default tool registry composition pin."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolSource
from dream.tools.builtin import default_registry
from dream.tools.builtin.task_get import TaskGetTool
from dream.tools.builtin.task_output import TaskOutputTool
from dream.tools.builtin.task_stop import TaskStopTool


def test_default_registry_holds_all_default_tools() -> None:
    reg = default_registry()
    names = [t.name for t in reg.list_tools()]
    assert set(names) == {
        "read_file",
        "edit_file",
        "write_file",
        "bash",
        "git",
        "read_offloaded",
        "glob",
        "grep",
        "lsp",
        "skill",
        "memory_search",
        "memory_get",
        "query_logs",
        "query_metrics",
        "task_create",
        "task_get",
        "task_output",
        "task_stop",
        "task_update",
        "cron_list",
        "cron_show",
        "cron_create",
        "cron_delete",
        "cron_toggle",
        "remote_trigger",
        "enter_worktree",
        "exit_worktree",
        "plan_show",
        "todo_write",
        "web_search",
        "web_extract",
        "web_fetch",
        "browser_run",
        "execute_code",
    }


def test_default_registry_order_is_canonical() -> None:
    """Order is byte-stable so the model-facing API schema does not jitter."""
    reg = default_registry()
    names = [t.name for t in reg.list_tools()]
    assert names == [
        "read_file",
        "edit_file",
        "write_file",
        "bash",
        "git",
        "read_offloaded",
        "glob",
        "grep",
        "lsp",
        "skill",
        "memory_search",
        "memory_get",
        "query_logs",
        "query_metrics",
        "task_create",
        "task_get",
        "task_output",
        "task_stop",
        "task_update",
        "cron_list",
        "cron_show",
        "cron_create",
        "cron_delete",
        "cron_toggle",
        "remote_trigger",
        "enter_worktree",
        "exit_worktree",
        "plan_show",
        "todo_write",
        "web_search",
        "web_extract",
        "web_fetch",
        "browser_run",
        "execute_code",
    ]


def test_default_registry_tools_are_marked_default_source() -> None:
    from dream.tools._registry import ToolCollisionError

    reg = default_registry()
    # Re-registering an already-present tool collides regardless of source.
    tool = next(iter(reg))
    with pytest.raises(ToolCollisionError):
        reg.register(tool, source=ToolSource.PER_REPO)


def test_default_registry_is_independent_between_calls() -> None:
    a = default_registry()
    b = default_registry()
    assert a is not b
    assert [t.name for t in a] == [t.name for t in b]


async def test_task_tool_recovery_guidance_names_only_registered_tools(
    tmp_path: Path,
) -> None:
    """Unknown-id error guidance must point at real tools. ``task_list`` was
    never registered, so naming it traps the agent in a dead-end retry."""
    registered = {t.name for t in default_registry().list_tools()}

    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    metadata: dict[str, object] = {}
    put_task_context(metadata, TaskSessionContext(manager=manager))
    ctx = ToolExecutionContext(
        working_dir=tmp_path, session_id="s_test", metadata=metadata
    )

    for tool in (TaskGetTool(), TaskOutputTool(), TaskStopTool()):
        result = await tool.execute({"task_id": "nope"}, ctx)
        assert result.is_error is True
        retry = result.metadata["safe_retry"]
        assert "task_list" not in retry, f"{tool.name} still references task_list"
        # The guidance must name at least one tool that actually exists.
        mentioned = {name for name in registered if name in retry}
        assert mentioned, f"{tool.name} guidance names no registered tool: {retry!r}"
