"""Per-session task wiring (Spec 07 wiring slice).

Mirrors ``dream.skills._session`` and pins the same contract: a typed
:class:`TaskSessionContext` rides the generic ``ToolExecutionContext.metadata``
channel under a known key so the new task/cron/plan tools can read a typed
bundle rather than poking ``Any``. The engine stays task-agnostic.
"""

from __future__ import annotations

from pathlib import Path

from dream.tasks._manager import BackgroundTaskManager
from dream.tasks._session import (
    TASK_CONTEXT_KEY,
    TaskSessionContext,
    put_task_context,
    read_task_context,
)


def test_put_and_read_round_trips(tmp_path: Path) -> None:
    """A context written to a metadata dict is recovered by ``read_task_context``."""
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    ctx = TaskSessionContext(
        manager=manager,
        cron_registry_path=tmp_path / "cron" / "jobs.json",
        plans_root=tmp_path / "exec-plans",
    )

    metadata: dict[str, object] = {}
    put_task_context(metadata, ctx)

    assert metadata[TASK_CONTEXT_KEY] is ctx
    assert read_task_context(metadata) is ctx


def test_read_returns_none_when_missing() -> None:
    """No key in metadata → ``None`` (rather than KeyError)."""
    assert read_task_context({}) is None


def test_read_returns_none_when_value_is_wrong_type() -> None:
    """A bogus value under the key is ignored, not coerced."""
    metadata: dict[str, object] = {TASK_CONTEXT_KEY: "not a TaskSessionContext"}
    assert read_task_context(metadata) is None


def test_optional_paths_default_to_none(tmp_path: Path) -> None:
    """``cron_registry_path`` and ``plans_root`` are optional so callers can wire
    them in piecemeal as the surface grows."""
    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    ctx = TaskSessionContext(manager=manager)

    assert ctx.cron_registry_path is None
    assert ctx.plans_root is None


def test_context_is_frozen(tmp_path: Path) -> None:
    """``TaskSessionContext`` is a frozen dataclass — tools cannot mutate it
    out from under siblings sharing the same metadata dict."""
    import dataclasses

    manager = BackgroundTaskManager(tasks_dir=tmp_path / "tasks")
    ctx = TaskSessionContext(manager=manager)

    try:
        ctx.manager = manager  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("TaskSessionContext should be frozen")
