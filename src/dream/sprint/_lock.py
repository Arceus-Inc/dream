"""Per-role single-active-instance lock for a task.

Spec 10 acceptance criterion #14 — "MUST allow at most one generator and
one evaluator per task at a time".

Built on :func:`dream.utils.file_lock.try_exclusive_file_lock`, which
already provides cross-process exclusion on POSIX (``fcntl.flock``) and
Windows (``msvcrt.locking``). Each role gets its own lockfile under
``<wt>/.dream/sprint/{task-id}/{role}.lock`` so generator and evaluator
locks are independent and tasks don't cross-contaminate.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from dream.utils.file_lock import try_exclusive_file_lock

from ._checks import checked_task_id

__all__ = ["RoleAlreadyActive", "SprintRole", "acquire_role_lock"]


SprintRole = Literal["generator", "evaluator"]
_VALID_ROLES: frozenset[str] = frozenset({"generator", "evaluator"})


class RoleAlreadyActive(RuntimeError):
    """Raised when a role lock is already held for this task."""


@contextmanager
def acquire_role_lock(
    worktree_root: str | Path, *, task_id: str, role: SprintRole
) -> Iterator[Path]:
    """Hold the per-role lock for the context body.

    Yields the lockfile path so callers can write a small marker payload
    (e.g. the session id holding the lock) if they want — this module
    doesn't mandate one.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(_VALID_ROLES)}")
    safe_id = checked_task_id(task_id)
    lock_path = (
        Path(worktree_root) / ".dream" / "sprint" / safe_id / f"{role}.lock"
    )
    with try_exclusive_file_lock(lock_path) as acquired:
        if not acquired:
            raise RoleAlreadyActive(
                f"role {role!r} is already active for task {task_id!r}"
            )
        yield lock_path
