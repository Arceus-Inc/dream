"""Per-session task-memory wiring (spec 11a).

Carries the session's :class:`~dream.memory._working.WorkingMemory` instance and
the durable proposals-queue location to the ``working_memory_*`` / ``memory_propose``
tools through the generic ``ToolExecutionContext.metadata`` channel — so the
engine stays memory-agnostic and the tools read a typed bundle rather than poking
``Any``. Mirrors the read-store wiring in :mod:`dream.memory._context`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.memory._working import WorkingMemory

TASK_MEMORY_CONTEXT_KEY = "task_memory_context"


@dataclass(frozen=True)
class TaskMemoryContext:
    """The per-session task-memory state a task-memory tool call needs."""

    working_memory: WorkingMemory
    proposals_dir: Path
    source_ref: str


def put_task_memory_context(
    metadata: dict[str, object], context: TaskMemoryContext
) -> None:
    """Place ``context`` into a tool ``metadata`` dict under the known key."""
    metadata[TASK_MEMORY_CONTEXT_KEY] = context


def read_task_memory_context(
    metadata: dict[str, object],
) -> TaskMemoryContext | None:
    """Return the :class:`TaskMemoryContext` from tool ``metadata``, or ``None``."""
    value = metadata.get(TASK_MEMORY_CONTEXT_KEY)
    return value if isinstance(value, TaskMemoryContext) else None


__all__ = [
    "TASK_MEMORY_CONTEXT_KEY",
    "TaskMemoryContext",
    "put_task_memory_context",
    "read_task_memory_context",
]
