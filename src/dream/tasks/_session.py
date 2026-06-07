"""Per-session task wiring for the new task/cron/plan tools.

Carries a session's :class:`BackgroundTaskManager` (plus optional cron-registry
and plans-root paths) to the new ``task_*`` / ``cron_*`` / ``plan_*`` tools
through the generic ``ToolExecutionContext.metadata`` channel, mirroring the
pattern :mod:`dream.skills._session` uses for skills. The engine stays
task-agnostic — the tools read a typed bundle out of ``ctx.metadata`` rather
than poking ``Any`` or reaching for a process global.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.tasks._manager import BackgroundTaskManager

TASK_CONTEXT_KEY = "task_context"


@dataclass(frozen=True)
class TaskSessionContext:
    """The per-session task state a ``task_*`` tool call needs."""

    manager: BackgroundTaskManager
    cron_registry_path: Path | None = None
    plans_root: Path | None = None


def put_task_context(metadata: dict[str, object], task_context: TaskSessionContext) -> None:
    """Place ``task_context`` into a tool ``metadata`` dict under the known key."""
    metadata[TASK_CONTEXT_KEY] = task_context


def read_task_context(metadata: dict[str, object]) -> TaskSessionContext | None:
    """Return the :class:`TaskSessionContext` from tool ``metadata``, or ``None``."""
    value = metadata.get(TASK_CONTEXT_KEY)
    return value if isinstance(value, TaskSessionContext) else None


__all__ = [
    "TASK_CONTEXT_KEY",
    "TaskSessionContext",
    "put_task_context",
    "read_task_context",
]
