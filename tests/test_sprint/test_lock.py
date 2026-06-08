"""Tests for the per-role single-active-instance lock.

Spec 10 acceptance criteria #14:

- MUST allow at most one generator and one evaluator per task at a time.

The lock is implemented over :func:`dream.utils.file_lock.try_exclusive_file_lock`,
which the rest of the harness already uses for cross-process exclusion
on shared registries. The lockfile lives under
``<wt>/.dream/sprint/{task-id}/{role}.lock`` so each task gets its own
namespace.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_acquire_role_lock_creates_lockfile_during_context(tmp_path: Path) -> None:
    from dream.sprint import acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="generator") as lock_path:
        assert lock_path.exists()


def test_acquire_role_lock_releases_after_normal_exit(tmp_path: Path) -> None:
    """The same role can be re-acquired after the prior holder releases."""
    from dream.sprint import acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        pass
    # second acquire must succeed without error
    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        pass


def test_acquire_role_lock_releases_after_exception(tmp_path: Path) -> None:
    from dream.sprint import acquire_role_lock

    with pytest.raises(RuntimeError, match="boom"):
        with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
            raise RuntimeError("boom")
    # lock was released — re-acquire must succeed
    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        pass


def test_acquire_role_lock_refuses_second_holder(tmp_path: Path) -> None:
    from dream.sprint import RoleAlreadyActive, acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        with pytest.raises(RoleAlreadyActive, match="generator"):
            with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
                pass


def test_at_most_one_generator_per_task(tmp_path: Path) -> None:
    from dream.sprint import RoleAlreadyActive, acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        with pytest.raises(RoleAlreadyActive):
            with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
                pass


def test_at_most_one_evaluator_per_task(tmp_path: Path) -> None:
    from dream.sprint import RoleAlreadyActive, acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="evaluator"):
        with pytest.raises(RoleAlreadyActive):
            with acquire_role_lock(tmp_path, task_id="t1", role="evaluator"):
                pass


def test_generator_and_evaluator_locks_are_independent(tmp_path: Path) -> None:
    from dream.sprint import acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        with acquire_role_lock(tmp_path, task_id="t1", role="evaluator"):
            pass  # both held concurrently — fine


def test_role_lock_isolated_per_task(tmp_path: Path) -> None:
    from dream.sprint import acquire_role_lock

    with acquire_role_lock(tmp_path, task_id="t1", role="generator"):
        # different task → different lock; no conflict.
        with acquire_role_lock(tmp_path, task_id="t2", role="generator"):
            pass


def test_acquire_role_lock_rejects_unknown_role(tmp_path: Path) -> None:
    from dream.sprint import acquire_role_lock

    with pytest.raises(ValueError, match="role"):
        with acquire_role_lock(tmp_path, task_id="t1", role="planner"):  # type: ignore[arg-type]
            pass


def test_acquire_role_lock_rejects_unsafe_task_id(tmp_path: Path) -> None:
    from dream.sprint import acquire_role_lock

    with pytest.raises(ValueError, match="task_id|unsafe"):
        with acquire_role_lock(tmp_path, task_id="a/b", role="generator"):
            pass
