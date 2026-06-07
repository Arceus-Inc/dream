"""Default ``cron_list`` and ``cron_show`` tools — Spec 07 wiring slice.

Both are read-only tier-0 inspections of the durable cron registry.
:func:`load_cron_jobs` is tolerant of missing/corrupt files (returns ``[]``)
so neither tool can poison itself on a partial write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tasks._cron import CronJob, save_cron_jobs
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.cron_list import CronListTool
from dream.tools.builtin.cron_show import CronShowTool


def _ctx(
    working_dir: Path, task_ctx: TaskSessionContext | None
) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if task_ctx is not None:
        put_task_context(metadata, task_ctx)
    return ToolExecutionContext(
        working_dir=working_dir, session_id="s_test", metadata=metadata
    )


def _session(tmp_path: Path, *, registry_path: Path | None) -> TaskSessionContext:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    return TaskSessionContext(manager=manager, cron_registry_path=registry_path)


# --- declarations ----------------------------------------------------------


def test_cron_list_is_read_only_tier_0() -> None:
    tool = CronListTool()
    assert tool.name == "cron_list"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_cron_show_is_read_only_tier_0() -> None:
    tool = CronShowTool()
    assert tool.name == "cron_show"
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


# --- cron_list -------------------------------------------------------------


async def test_cron_list_empty_when_no_registry_path_configured(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=None)
    result = await CronListTool().execute({}, _ctx(tmp_path, sc))
    # No registry wired → no jobs available, structured error (caller fault:
    # the session was set up without a cron registry).
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_cron_list_empty_when_registry_file_missing(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=tmp_path / "cron.json")
    result = await CronListTool().execute({}, _ctx(tmp_path, sc))
    assert result.is_error is False
    assert "no cron jobs" in result.content.lower()
    assert result.metadata.get("job_count") == 0


async def test_cron_list_renders_jobs(tmp_path: Path) -> None:
    registry = tmp_path / "cron.json"
    save_cron_jobs(
        registry,
        [
            CronJob(name="doc-garden", schedule="0 6 * * *", enabled=True),
            CronJob(name="quality-grade", schedule="*/15 * * * *", enabled=False),
        ],
    )
    sc = _session(tmp_path, registry_path=registry)
    result = await CronListTool().execute({}, _ctx(tmp_path, sc))
    assert result.is_error is False
    assert "doc-garden" in result.content
    assert "quality-grade" in result.content
    assert "0 6 * * *" in result.content
    assert result.metadata.get("job_count") == 2


async def test_cron_list_missing_task_context_is_error(tmp_path: Path) -> None:
    result = await CronListTool().execute({}, _ctx(tmp_path, None))
    assert result.is_error is True
    assert "root_cause" in result.metadata


# --- cron_show -------------------------------------------------------------


async def test_cron_show_returns_job_details(tmp_path: Path) -> None:
    registry = tmp_path / "cron.json"
    save_cron_jobs(
        registry,
        [CronJob(name="doc-garden", schedule="0 6 * * *", description="garden")],
    )
    sc = _session(tmp_path, registry_path=registry)
    result = await CronShowTool().execute(
        {"name": "doc-garden"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is False
    assert "doc-garden" in result.content
    assert "0 6 * * *" in result.content
    assert "garden" in result.content
    assert result.metadata.get("name") == "doc-garden"


async def test_cron_show_unknown_job_is_structured_error(tmp_path: Path) -> None:
    registry = tmp_path / "cron.json"
    save_cron_jobs(registry, [])
    sc = _session(tmp_path, registry_path=registry)
    result = await CronShowTool().execute(
        {"name": "ghost"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "ghost" in result.content
    assert "root_cause" in result.metadata


async def test_cron_show_missing_registry_path_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=None)
    result = await CronShowTool().execute(
        {"name": "doc-garden"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_cron_show_missing_task_context_is_error(tmp_path: Path) -> None:
    result = await CronShowTool().execute(
        {"name": "x"}, _ctx(tmp_path, None)
    )
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_cron_show_invalid_input_raises(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=tmp_path / "cron.json")
    with pytest.raises(Exception):
        await CronShowTool().execute({}, _ctx(tmp_path, sc))
