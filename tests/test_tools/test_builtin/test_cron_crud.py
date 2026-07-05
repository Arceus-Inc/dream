"""Default cron CRUD tools — ``cron_create`` / ``cron_delete`` / ``cron_toggle``
and ``remote_trigger``. Mutating (tier 1) complements to the read-only
``cron_list`` / ``cron_show``. All operate on the session's cron registry via
:class:`TaskSessionContext`; a missing registry path is the Spec 05 contract.
"""

from __future__ import annotations

from pathlib import Path

from dream.tasks._cron import get_cron_job
from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import TaskSessionContext, put_task_context
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.cron_create import CronCreateTool
from dream.tools.builtin.cron_delete import CronDeleteTool
from dream.tools.builtin.cron_toggle import CronToggleTool
from dream.tools.builtin.remote_trigger import RemoteTriggerTool


def _ctx(working_dir: Path, task_ctx: TaskSessionContext | None) -> ToolExecutionContext:
    metadata: dict[str, object] = {}
    if task_ctx is not None:
        put_task_context(metadata, task_ctx)
    return ToolExecutionContext(working_dir=working_dir, session_id="s_test", metadata=metadata)


def _session(tmp_path: Path, *, registry_path: Path | None) -> TaskSessionContext:
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    return TaskSessionContext(manager=manager, cron_registry_path=registry_path)


# --- declarations ----------------------------------------------------------


def test_cron_crud_tools_are_mutating_tier_1() -> None:
    for tool in (CronCreateTool(), CronDeleteTool(), CronToggleTool(), RemoteTriggerTool()):
        assert tool.declaration.risk == "mutating"
        assert tool.declaration.tier_required == 1
        assert tool.is_read_only() is False


# --- cron_create -----------------------------------------------------------


async def test_cron_create_persists_job(tmp_path: Path) -> None:
    reg = tmp_path / "cron.json"
    sc = _session(tmp_path, registry_path=reg)
    result = await CronCreateTool().execute(
        {"name": "digest", "schedule": "0 9 * * *", "entry_prompt": "summarize"},
        _ctx(tmp_path, sc),
    )
    assert result.is_error is False
    job = get_cron_job(reg, "digest")
    assert job is not None
    assert job.schedule == "0 9 * * *"
    assert job.entry_prompt == "summarize"
    assert job.next_run is not None


async def test_cron_create_invalid_schedule_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=tmp_path / "cron.json")
    result = await CronCreateTool().execute(
        {"name": "bad", "schedule": "not a cron"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True
    assert result.metadata.get("root_cause")


async def test_cron_create_no_registry_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=None)
    result = await CronCreateTool().execute(
        {"name": "x", "schedule": "0 9 * * *"}, _ctx(tmp_path, sc)
    )
    assert result.is_error is True


async def test_cron_create_no_context_is_error(tmp_path: Path) -> None:
    result = await CronCreateTool().execute(
        {"name": "x", "schedule": "0 9 * * *"}, _ctx(tmp_path, None)
    )
    assert result.is_error is True


# --- cron_delete -----------------------------------------------------------


async def test_cron_delete_removes_job(tmp_path: Path) -> None:
    reg = tmp_path / "cron.json"
    sc = _session(tmp_path, registry_path=reg)
    await CronCreateTool().execute({"name": "gone", "schedule": "0 9 * * *"}, _ctx(tmp_path, sc))
    result = await CronDeleteTool().execute({"name": "gone"}, _ctx(tmp_path, sc))
    assert result.is_error is False
    assert get_cron_job(reg, "gone") is None


async def test_cron_delete_missing_is_error(tmp_path: Path) -> None:
    reg = tmp_path / "cron.json"
    sc = _session(tmp_path, registry_path=reg)
    result = await CronDeleteTool().execute({"name": "nope"}, _ctx(tmp_path, sc))
    assert result.is_error is True
    assert "not found" in result.metadata.get("root_cause", "")


# --- cron_toggle -----------------------------------------------------------


async def test_cron_toggle_flips_enabled(tmp_path: Path) -> None:
    reg = tmp_path / "cron.json"
    sc = _session(tmp_path, registry_path=reg)
    await CronCreateTool().execute(
        {"name": "t", "schedule": "0 9 * * *", "enabled": True}, _ctx(tmp_path, sc)
    )
    result = await CronToggleTool().execute({"name": "t", "enabled": False}, _ctx(tmp_path, sc))
    assert result.is_error is False
    job = get_cron_job(reg, "t")
    assert job is not None and job.enabled is False


async def test_cron_toggle_missing_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=tmp_path / "cron.json")
    result = await CronToggleTool().execute({"name": "nope", "enabled": True}, _ctx(tmp_path, sc))
    assert result.is_error is True


# --- remote_trigger --------------------------------------------------------


async def test_remote_trigger_no_manifest_is_error(tmp_path: Path) -> None:
    # A registry exists but there is no .harness/cron/<name>.toml manifest.
    reg = tmp_path / "cron.json"
    sc = _session(tmp_path, registry_path=reg)
    await CronCreateTool().execute({"name": "nightly", "schedule": "0 0 * * *"}, _ctx(tmp_path, sc))
    result = await RemoteTriggerTool().execute({"name": "nightly"}, _ctx(tmp_path, sc))
    assert result.is_error is True
    assert "manifest" in result.content.lower()


async def test_remote_trigger_no_registry_is_error(tmp_path: Path) -> None:
    sc = _session(tmp_path, registry_path=None)
    result = await RemoteTriggerTool().execute({"name": "x"}, _ctx(tmp_path, sc))
    assert result.is_error is True
