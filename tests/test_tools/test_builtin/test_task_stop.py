"""Default ``task_stop`` tool — Spec 07 wiring slice.

Mutating tier-1: terminates a supervised subprocess and triggers completion
listeners. The harness manager already handles the "already terminal" no-op
case; we surface its :class:`ValueError` (unknown id / not running) as the
Spec 05 three-part structured error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.task_stop import TaskStopTool


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


def test_declaration_is_mutating_tier_1() -> None:
    tool = TaskStopTool()
    assert tool.name == "task_stop"
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required == 1
    assert tool.is_read_only() is False


async def test_stops_running_task(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="sleeper",
        cwd=str(tmp_path),
        argv=["cmd", "/c", "ping", "-n", "30", "127.0.0.1"],
    )
    result = await TaskStopTool().execute({"task_id": task.id}, _ctx(tmp_path, sc))
    assert result.is_error is False
    assert task.id in result.content

    stopped = sc.manager.get_task(task.id)
    assert stopped is not None
    assert stopped.status in {"killed", "completed", "failed"}
    assert result.metadata.get("task_id") == task.id
    assert result.metadata.get("status") == stopped.status


async def test_stop_unknown_id_is_structured_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskStopTool().execute({"task_id": "nope"}, _ctx(tmp_path, sc))
    assert result.is_error is True
    assert "nope" in result.content
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_stop_already_terminal_task_is_noop(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="quick",
        cwd=str(tmp_path),
        argv=["cmd", "/c", "echo", "hi"],
    )
    # let it finish naturally
    await sc.manager.stop_task(task.id)
    final = sc.manager.get_task(task.id)
    assert final is not None
    # second stop is a no-op at the manager level — surface as success
    result = await TaskStopTool().execute({"task_id": task.id}, _ctx(tmp_path, sc))
    assert result.is_error is False


async def test_missing_task_context_is_error(tmp_path: Path) -> None:
    result = await TaskStopTool().execute({"task_id": "x"}, _ctx(tmp_path, None))
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_invalid_input_raises(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    with pytest.raises(Exception):
        await TaskStopTool().execute({}, _ctx(tmp_path, sc))
