"""Default ``task_update`` tool — in-place task metadata updates (mutating, tier 1)."""

from __future__ import annotations

import sys
from pathlib import Path

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.task_update import TaskUpdateInput, TaskUpdateTool


def _ctx(working_dir: Path, task_ctx: TaskSessionContext | None) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if task_ctx is not None:
        put_task_context(metadata, task_ctx)
    return ToolExecutionContext(working_dir=working_dir, session_id="s_test", metadata=metadata)


def _session(tmp_path: Path) -> TaskSessionContext:
    return TaskSessionContext(manager=BackgroundTaskManager(tasks_dir=tmp_path / "tasks"))


async def _spawn(sc: TaskSessionContext, tmp_path: Path) -> str:
    task = await sc.manager.create_shell_task(
        description="original",
        cwd=tmp_path,
        argv=[sys.executable, "-c", "print('hi')"],
    )
    return task.id


def test_task_update_is_mutating_tier_1() -> None:
    tool = TaskUpdateTool()
    assert tool.name == "task_update"
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required == 1
    assert tool.is_read_only() is False


def test_task_update_requires_at_least_one_field() -> None:
    import pytest

    with pytest.raises(ValueError):
        TaskUpdateInput.model_validate({"task_id": "t_1"})


async def test_task_update_sets_description_and_metadata(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task_id = await _spawn(sc, tmp_path)
    result = await TaskUpdateTool().execute(
        {"task_id": task_id, "description": "new", "progress": 42, "status_note": "halfway"},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is False
    updated = sc.manager.get_task(task_id)
    assert updated is not None
    assert updated.description == "new"
    assert updated.metadata.get("progress") == "42"
    assert updated.metadata.get("status_note") == "halfway"


async def test_task_update_partial_leaves_others(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task_id = await _spawn(sc, tmp_path)
    await TaskUpdateTool().execute({"task_id": task_id, "progress": 10}, _ctx(tmp_path, sc))
    updated = sc.manager.get_task(task_id)
    assert updated is not None
    assert updated.description == "original"  # untouched
    assert updated.metadata.get("progress") == "10"


async def test_task_update_unknown_id_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskUpdateTool().execute(
        {"task_id": "nope", "progress": 1}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert result.metadata.get("root_cause")


async def test_task_update_no_context_is_error(tmp_path: Path) -> None:
    result = await TaskUpdateTool().execute(
        {"task_id": "x", "progress": 1}, _ctx(tmp_path, None)
    )
    assert result.is_error is True
