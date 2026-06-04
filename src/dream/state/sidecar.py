"""Per-task sidecar bundle and schema-validated ``state.json`` (spec 01).

The sidecar holds a task's ephemeral now-state — ``logs/``, ``metrics/``,
``scratch/``, and ``state.json`` — under ``<repo>/.dream/sidecars/{task-id}/``.
It is created at task start and deleted with the worktree at task end.

``state.json`` is updated atomically (PR1 ``atomic_write_text``) under an
exclusive lock (PR1 ``exclusive_file_lock``), and stamps ``harness_version`` so
two harness versions refuse to coexist in one repo (spec criterion 21).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from dream.config.paths import DreamPaths
from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_text

__all__ = [
    "TaskState",
    "create_sidecar",
    "ensure_version_compatible",
    "read_state",
    "remove_sidecar",
    "update_state",
]

_SIDECAR_SUBDIRS = ("logs", "metrics", "scratch")
TaskStatus = Literal["running", "paused", "completed", "failed"]


class TaskState(BaseModel):
    """Schema-validated task state persisted at ``sidecars/{task-id}/state.json``."""

    task_id: str
    base_branch: str
    created_at: str
    harness_version: str
    last_checkpoint_turn: int = 0
    status: TaskStatus = "running"
    parent_checkpoint_ref: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_sidecar(
    paths: DreamPaths,
    task_id: str,
    *,
    base_branch: str,
    harness_version: str,
    parent_checkpoint_ref: str | None = None,
) -> TaskState:
    """Create the sidecar bundle and write the initial ``state.json``."""
    sidecar = paths.sidecar(task_id)  # validates task_id (PR1 traversal guard)
    for sub in _SIDECAR_SUBDIRS:
        (sidecar / sub).mkdir(parents=True, exist_ok=True)
    state = TaskState(
        task_id=task_id,
        base_branch=base_branch,
        created_at=_now_iso(),
        harness_version=harness_version,
        parent_checkpoint_ref=parent_checkpoint_ref,
    )
    atomic_write_text(sidecar / "state.json", state.model_dump_json(indent=2))
    return state


def read_state(paths: DreamPaths, task_id: str) -> TaskState:
    """Load and validate ``state.json`` for a task (raises if absent)."""
    state_path = paths.sidecar(task_id) / "state.json"
    return TaskState.model_validate_json(state_path.read_text())


def update_state(paths: DreamPaths, task_id: str, **changes: object) -> TaskState:
    """Atomically apply ``changes`` to ``state.json`` under an exclusive lock."""
    sidecar = paths.sidecar(task_id)
    state_path = sidecar / "state.json"
    lock_path = sidecar / "state.json.lock"
    with exclusive_file_lock(lock_path):
        current = TaskState.model_validate_json(state_path.read_text())
        updated = current.model_copy(update=changes)
        atomic_write_text(state_path, updated.model_dump_json(indent=2))
    return updated


def remove_sidecar(paths: DreamPaths, task_id: str) -> None:
    """Delete the sidecar bundle (idempotent)."""
    shutil.rmtree(paths.sidecar(task_id), ignore_errors=True)


def ensure_version_compatible(state: TaskState, current_version: str) -> None:
    """Raise if a task's stamped harness version differs from the running one."""
    if state.harness_version != current_version:
        raise ValueError(
            f"harness version mismatch: task was written by "
            f"{state.harness_version!r}, current is {current_version!r}"
        )
