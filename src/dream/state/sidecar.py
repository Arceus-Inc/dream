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
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from dream.config.paths import DreamPaths
from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_bytes, atomic_write_text, clean_orphan_temp_files

__all__ = [
    "TaskState",
    "assert_no_version_conflicts",
    "create_sidecar",
    "ensure_version_compatible",
    "read_state",
    "remove_sidecar",
    "update_state",
]

_SIDECAR_SUBDIRS = ("logs", "metrics", "scratch")
_SIDECAR_DB = "db.sqlite"  # spec 01 decision 8 — per-task structured state
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
    """Create the sidecar bundle and write the initial ``state.json``.

    At task start this also (a) refuses to coexist with sidecars stamped at a
    different ``harness_version`` (spec criterion 21) and (b) sweeps any orphan
    ``.tmp.*`` files left in the now-state dirs by an interrupted prior write
    (spec criterion 13).
    """
    paths.ensure()
    assert_no_version_conflicts(paths, harness_version)
    clean_orphan_temp_files(paths.worktrees_dir)
    clean_orphan_temp_files(paths.sidecars_dir)

    sidecar = paths.sidecar(task_id)  # validates task_id (PR1 traversal guard)
    for sub in _SIDECAR_SUBDIRS:
        (sidecar / sub).mkdir(parents=True, exist_ok=True)
    # Per-task structured state (spec decision 8). Created empty; schema is
    # owned by whatever component first opens it. An atomic write means a
    # concurrent reader never sees a torn header.
    db_path = sidecar / _SIDECAR_DB
    if not db_path.exists():
        atomic_write_bytes(db_path, b"")
    state = TaskState(
        task_id=task_id,
        base_branch=base_branch,
        created_at=_now_iso(),
        harness_version=harness_version,
        parent_checkpoint_ref=parent_checkpoint_ref,
    )
    atomic_write_text(sidecar / "state.json", state.model_dump_json(indent=2))
    return state


def _lock_path(paths: DreamPaths, task_id: str) -> Path:
    """The per-task state lock — a *sibling* of the sidecar, not inside it.

    Keeping the lock file outside the sidecar means acquiring it never recreates
    a sidecar that ``remove_sidecar`` just deleted.
    """
    sidecar = paths.sidecar(task_id)  # validates task_id (PR1 traversal guard)
    return sidecar.parent / f"{sidecar.name}.lock"


def read_state(paths: DreamPaths, task_id: str) -> TaskState:
    """Load and validate ``state.json`` for a task (raises if absent)."""
    state_path = paths.sidecar(task_id) / "state.json"
    return TaskState.model_validate_json(state_path.read_text())


def update_state(paths: DreamPaths, task_id: str, **changes: object) -> TaskState:
    """Atomically apply ``changes`` to ``state.json`` under an exclusive lock.

    The merged result is re-validated through ``TaskState`` before writing, so an
    invalid update (e.g. an unsupported status) is rejected instead of corrupting
    ``state.json``.
    """
    state_path = paths.sidecar(task_id) / "state.json"
    with exclusive_file_lock(_lock_path(paths, task_id)):
        current = TaskState.model_validate_json(state_path.read_text())
        data = current.model_dump()
        data.update(changes)
        updated = TaskState.model_validate(data)  # re-validate the merged values
        atomic_write_text(state_path, updated.model_dump_json(indent=2))
    return updated


def remove_sidecar(paths: DreamPaths, task_id: str) -> None:
    """Delete the sidecar bundle, serialized with ``update_state`` (idempotent)."""
    sidecar = paths.sidecar(task_id)
    if not sidecar.exists():
        return
    with exclusive_file_lock(_lock_path(paths, task_id)):
        shutil.rmtree(sidecar, ignore_errors=True)


def ensure_version_compatible(state: TaskState, current_version: str) -> None:
    """Raise if a task's stamped harness version differs from the running one."""
    if state.harness_version != current_version:
        raise ValueError(
            f"harness version mismatch: task was written by "
            f"{state.harness_version!r}, current is {current_version!r}"
        )


def assert_no_version_conflicts(paths: DreamPaths, current_version: str) -> None:
    """Refuse to start if any existing sidecar was written by a different harness version.

    Implements spec 01 criterion 21 ("mismatched harness versions MUST refuse
    to coexist in one repo") and its acceptance scenario at task start. Silently
    skips sidecars with no/malformed ``state.json`` — they're already corrupt
    and a separate problem from a clean version conflict.
    """
    sidecars = paths.sidecars_dir
    if not sidecars.is_dir():
        return
    for child in sorted(sidecars.iterdir()):
        if not child.is_dir():
            continue
        state_path = child / "state.json"
        if not state_path.is_file():
            continue
        try:
            other = TaskState.model_validate_json(state_path.read_text())
        except (OSError, ValueError):
            continue
        if other.harness_version != current_version:
            raise ValueError(
                f"refusing to start: task {other.task_id!r} in this repo is at "
                f"harness {other.harness_version!r}, current runner is "
                f"{current_version!r}"
            )
