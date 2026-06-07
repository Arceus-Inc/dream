"""Spec 07 slice 2 — runtime task record.

A :class:`TaskRecord` is the *ephemeral* in-memory handle for a single
background subprocess the :class:`~dream.tasks._manager.BackgroundTaskManager`
spawns. Unlike OpenHarness's mutable dataclass we keep the record
**frozen** to match the rest of the Dream codebase
(:class:`~dream.tasks._ledger.Ledger`, :class:`~dream.wake._state`); every
state transition goes through a ``with_*`` helper that returns a new
record. The manager owns the canonical ``id -> TaskRecord`` mapping and
rebinds it on each transition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal

__all__ = [
    "TaskRecord",
    "TaskStatus",
    "TaskType",
]


def _frozen_map(value: Mapping[str, str]) -> Mapping[str, str]:
    """Return a read-only snapshot of ``value`` that callers can't mutate."""
    return MappingProxyType(dict(value))

TaskType = Literal[
    "local_bash",
    "local_agent",
    "remote_agent",
    "in_process_teammate",
    "dream",
]
"""The five task-type tags from OpenHarness's runtime taxonomy. Slice 2
only spawns ``local_bash`` and ``local_agent``; the rest are reserved for
later slices that wire teammates, dream sessions, and remote agents."""

TaskStatus = Literal["pending", "running", "completed", "failed", "killed"]
"""Five-state FSM: ``pending -> running -> {completed, failed, killed}``."""


@dataclass(frozen=True)
class TaskRecord:
    """Runtime handle for a background task.

    Frozen on purpose: each lifecycle transition returns a new record via
    one of the ``with_*`` helpers. The manager owns the canonical map and
    rebinds it; no aliasing or in-place mutation.
    """

    id: str
    type: TaskType
    status: TaskStatus
    description: str
    cwd: str
    output_file: Path
    command: str | None = None
    prompt: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    ended_at: float | None = None
    return_code: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] | None = None
    argv: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        # Freeze the container fields so the "frozen" promise holds for the
        # collections too: callers can pass an ordinary dict/list for
        # ergonomics, but the stored value is a read-only snapshot that
        # cannot be mutated in place to change task state behind the manager.
        object.__setattr__(self, "metadata", _frozen_map(self.metadata))
        if self.env is not None:
            object.__setattr__(self, "env", _frozen_map(self.env))
        if self.argv is not None and not isinstance(self.argv, tuple):
            object.__setattr__(self, "argv", tuple(self.argv))

    # --- transition helpers (always return a new record) -----------------

    def with_status(self, status: TaskStatus) -> TaskRecord:
        return replace(self, status=status)

    def with_started(self, started_at: float) -> TaskRecord:
        return replace(self, started_at=started_at)

    def with_ended(self, ended_at: float) -> TaskRecord:
        return replace(self, ended_at=ended_at)

    def with_return_code(self, return_code: int) -> TaskRecord:
        return replace(self, return_code=return_code)

    def with_metadata(self, extra: Mapping[str, str]) -> TaskRecord:
        merged = {**self.metadata, **extra}
        return replace(self, metadata=merged)
