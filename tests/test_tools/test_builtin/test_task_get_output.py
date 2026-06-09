"""Default ``task_get`` and ``task_output`` tools — Spec 07 wiring slice.

Both are read-only tier-0 lookups against the per-session
:class:`BackgroundTaskManager`. Mirrors OpenHarness's split between the
*record* view (``task_get``) and the *log tail* view (``task_output``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.task_get import TaskGetTool
from dream.tools.builtin.task_output import TaskOutputTool


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


# --- declarations ----------------------------------------------------------


def test_task_get_is_read_only_tier_0() -> None:
    tool = TaskGetTool()
    assert tool.name == "task_get"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_task_output_is_read_only_tier_0() -> None:
    tool = TaskOutputTool()
    assert tool.name == "task_output"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


# --- task_get --------------------------------------------------------------


async def test_task_get_returns_record_for_existing_task(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="echo hi", cwd=str(tmp_path), command="echo hi"
    )
    result = await TaskGetTool().execute({"task_id": task.id}, _ctx(tmp_path, sc))
    assert result.is_error is False
    # content surfaces id, type, status — caller can read at a glance
    assert task.id in result.content
    assert task.type in result.content
    assert task.status in result.content
    # structured metadata so callers can parse without scraping
    assert result.metadata.get("task_id") == task.id
    assert result.metadata.get("task_type") == task.type
    assert result.metadata.get("status") == task.status
    await sc.manager.stop_task(task.id)


async def test_task_get_unknown_id_is_structured_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskGetTool().execute({"task_id": "nope"}, _ctx(tmp_path, sc))
    assert result.is_error is True
    assert "nope" in result.content
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_task_get_missing_task_context_is_error(tmp_path: Path) -> None:
    result = await TaskGetTool().execute({"task_id": "x"}, _ctx(tmp_path, None))
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_task_get_invalid_input_raises(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    with pytest.raises(Exception):
        await TaskGetTool().execute({}, _ctx(tmp_path, sc))


# --- task_output -----------------------------------------------------------


async def test_task_output_returns_log_tail(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="seed", cwd=str(tmp_path), command="echo seed"
    )
    # write known content directly to the log so the test doesn't race the
    # subprocess.
    task.output_file.write_text("hello world\n", encoding="utf-8")
    result = await TaskOutputTool().execute(
        {"task_id": task.id}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    assert "hello world" in result.content
    await sc.manager.stop_task(task.id)


async def test_task_output_max_bytes_truncates(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="seed", cwd=str(tmp_path), command="echo seed"
    )
    task.output_file.write_text("X" * 5000, encoding="utf-8")
    result = await TaskOutputTool().execute(
        {"task_id": task.id, "max_bytes": 100}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    # tail semantics: only the last max_bytes are returned
    assert len(result.content) == 100
    await sc.manager.stop_task(task.id)


async def test_task_output_tail_is_byte_exact_on_large_log(tmp_path: Path) -> None:
    """The tail window is a true byte-bounded read from the end: the returned
    text is exactly the last ``max_bytes`` bytes, even for a large log."""
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="seed", cwd=str(tmp_path), command="echo seed"
    )
    body = "".join(f"line-{i:05d}\n" for i in range(20000))  # ~220 KB
    task.output_file.write_text(body, encoding="utf-8")
    result = await TaskOutputTool().execute(
        {"task_id": task.id, "max_bytes": 200}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    expected = body.encode("utf-8")[-200:].decode("utf-8", errors="replace")
    assert result.content == expected
    assert result.metadata["bytes_returned"] == len(expected)
    await sc.manager.stop_task(task.id)


async def test_task_output_empty_log_renders_placeholder(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    task = await sc.manager.create_shell_task(
        description="empty", cwd=str(tmp_path), command="echo seed"
    )
    task.output_file.write_text("", encoding="utf-8")
    result = await TaskOutputTool().execute(
        {"task_id": task.id}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    # caller-friendly placeholder so empty output doesn't look like a bug
    assert "no output" in result.content.lower()
    await sc.manager.stop_task(task.id)


async def test_task_output_unknown_id_is_structured_error(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    result = await TaskOutputTool().execute(
        {"task_id": "nope"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "nope" in result.content
    assert "root_cause" in result.metadata


async def test_task_output_missing_task_context_is_error(tmp_path: Path) -> None:
    result = await TaskOutputTool().execute(
        {"task_id": "x"}, _ctx(tmp_path, None)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_task_output_invalid_max_bytes_raises(tmp_path: Path) -> None:
    sc = _session(tmp_path)
    # negative max_bytes → pydantic validation error (Field has ge=1)
    with pytest.raises(Exception):
        await TaskOutputTool().execute(
            {"task_id": "x", "max_bytes": -1}, _ctx(tmp_path, sc)
        )
