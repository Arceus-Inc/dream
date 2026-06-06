"""Spec 01 — per-task sidecar bundle and schema-validated state.json.

The sidecar (logs/metrics/scratch/state.json) is the per-task now-state, deleted
with the worktree. state.json is updated atomically under an exclusive lock and
stamps the harness version so mismatched versions refuse to coexist.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import dream.state.sidecar as sidecar_module
from dream.config.paths import DreamPaths
from dream.state.sidecar import (
    TaskState,
    # private API: test verifies create/update/remove share one lock
    _lock_path,
    assert_no_version_conflicts,
    create_sidecar,
    ensure_version_compatible,
    read_state,
    remove_sidecar,
    update_state,
)
from dream.utils.file_lock import exclusive_file_lock
from dream.utils.fs import atomic_write_text


@pytest.fixture
def paths(tmp_path: Path) -> DreamPaths:
    return DreamPaths.resolve(tmp_path / "repo", home=tmp_path / "home", env={})


def test_create_sidecar_layout(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    sidecar = paths.sidecar("T1")
    assert (sidecar / "logs").is_dir()
    assert (sidecar / "metrics").is_dir()
    assert (sidecar / "scratch").is_dir()
    assert (sidecar / "state.json").is_file()


def test_create_stamps_fields(paths: DreamPaths) -> None:
    state = create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    assert state.task_id == "T1"
    assert state.base_branch == "main"
    assert state.harness_version == "0.1.0"
    assert state.status == "running"
    assert state.last_checkpoint_turn == 0
    assert state.parent_checkpoint_ref is None
    assert state.created_at  # ISO timestamp present


def test_state_roundtrip(paths: DreamPaths) -> None:
    created = create_sidecar(paths, "T1", base_branch="dev", harness_version="0.1.0")
    loaded = read_state(paths, "T1")
    assert loaded == created


def test_update_state_changes_field(paths: DreamPaths) -> None:
    created = create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    updated = update_state(paths, "T1", status="completed", last_checkpoint_turn=3)
    assert updated.status == "completed"
    assert updated.last_checkpoint_turn == 3
    assert updated.created_at == created.created_at  # unchanged
    assert read_state(paths, "T1").status == "completed"  # persisted


def test_two_tasks_have_isolated_sidecars(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    create_sidecar(paths, "T2", base_branch="main", harness_version="0.1.0")
    update_state(paths, "T1", status="failed")
    assert read_state(paths, "T1").status == "failed"
    assert read_state(paths, "T2").status == "running"  # untouched


def test_remove_sidecar(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    remove_sidecar(paths, "T1")
    assert not paths.sidecar("T1").exists()


def test_remove_nonexistent_does_not_recreate(paths: DreamPaths) -> None:
    remove_sidecar(paths, "never-made")  # idempotent, no lock-induced recreate
    assert not paths.sidecar("never-made").exists()


def test_update_rejects_invalid_value(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    with pytest.raises(ValueError):
        update_state(paths, "T1", status="bogus")
    # state.json must remain valid and unchanged (no corrupt write).
    assert read_state(paths, "T1").status == "running"


def test_read_state_missing_raises(paths: DreamPaths) -> None:
    with pytest.raises(FileNotFoundError):
        read_state(paths, "never-made")


def test_version_mismatch_raises(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    state = read_state(paths, "T1")
    with pytest.raises(ValueError, match="version"):
        ensure_version_compatible(state, "0.2.0")


def test_version_compatible_ok(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    state = read_state(paths, "T1")
    ensure_version_compatible(state, "0.1.0")  # no raise


def test_create_rejects_unsafe_task_id(paths: DreamPaths) -> None:
    with pytest.raises(ValueError):
        create_sidecar(paths, "../escape", base_branch="main", harness_version="0.1.0")


def test_taskstate_rejects_bad_status() -> None:
    with pytest.raises(ValueError):
        TaskState(
            task_id="T1",
            base_branch="main",
            created_at="2026-01-01T00:00:00Z",
            harness_version="0.1.0",
            status="bogus",  # type: ignore[arg-type]
        )


# --- spec 01 decision 8: per-task structured state ------------------------


def test_create_sidecar_creates_db_sqlite(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    db = paths.sidecar("T1") / "db.sqlite"
    assert db.is_file()
    assert db.read_bytes() == b""  # created empty; schema owned by first opener


# --- spec 01 criterion 21: two harness versions refuse to coexist ---------


def test_create_sidecar_refuses_mismatched_existing_version(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    with pytest.raises(ValueError, match="refusing to start"):
        create_sidecar(paths, "T2", base_branch="main", harness_version="0.2.0")
    assert not paths.sidecar("T2").exists()


def test_assert_no_version_conflicts_no_op_when_empty(paths: DreamPaths) -> None:
    assert_no_version_conflicts(paths, "0.1.0")  # no sidecars yet, no raise


def test_assert_no_version_conflicts_passes_on_matching_version(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    assert_no_version_conflicts(paths, "0.1.0")  # no raise


def test_assert_no_version_conflicts_ignores_corrupt_state(paths: DreamPaths) -> None:
    """A corrupt state.json is a separate failure mode, not a version conflict."""
    paths.ensure()
    bad = paths.sidecar("T1")
    bad.mkdir(parents=True)
    (bad / "state.json").write_text("not json")
    assert_no_version_conflicts(paths, "0.1.0")  # no raise


# --- spec 01 criterion 13: orphan .tmp.* swept at task start --------------


def test_create_sidecar_sweeps_orphan_temp_files(paths: DreamPaths) -> None:
    paths.ensure()
    suffix = "0123456789abcdef" * 2  # 32 lowercase hex chars, matches writer scheme
    orphan_wt = paths.worktrees_dir / f"T0.meta.json.tmp.{suffix}"
    orphan_wt.write_text("partial")
    orphan_sc = paths.sidecars_dir / f"T0_state.json.tmp.{suffix}"
    orphan_sc.write_text("partial")

    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")

    assert not orphan_wt.exists()
    assert not orphan_sc.exists()


def test_create_sidecar_sweeps_orphan_in_task_subdir(paths: DreamPaths) -> None:
    """state.json temp orphans live one level down: sidecars/<task-id>/state.json.tmp.*"""
    paths.ensure()
    suffix = "0123456789abcdef" * 2  # 32 lowercase hex, matches the writer scheme
    task_dir = paths.sidecars_dir / "T0"
    task_dir.mkdir(parents=True)
    orphan = task_dir / f"state.json.tmp.{suffix}"
    orphan.write_text("partial")

    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")

    assert not orphan.exists()  # recursive sweep reached the per-task subdir


def test_create_sidecar_does_not_touch_real_files(paths: DreamPaths) -> None:
    """Sweep must remove only ``.tmp.*`` files — real files are untouched."""
    paths.ensure()
    real = paths.sidecars_dir / "keep.json"
    real.write_text("{}")
    atomic_write_text(paths.sidecars_dir / "also-keep.json", "{}")
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    assert real.read_text() == "{}"
    assert (paths.sidecars_dir / "also-keep.json").read_text() == "{}"


# --- the state.json write must happen under the per-task exclusive lock ----


def test_create_sidecar_holds_lock_during_state_write(
    paths: DreamPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_sidecar``'s ``state.json`` write must run under the per-task lock.

    A daemon thread races to grab the same lock the moment the write is invoked.
    If the write were unlocked, the thread would acquire immediately; we assert it
    is still blocked during the write, then acquires once ``create_sidecar`` exits.
    """
    paths.ensure()  # create sidecars_dir so _lock_path's parent exists for the rival
    lock_path = _lock_path(paths, "T1")

    real_atomic_write_text = sidecar_module.atomic_write_text
    contended = threading.Event()  # set right before the rival tries to acquire
    rival_blocked = threading.Event()  # set once the rival reaches the lock barrier
    rival_acquired = threading.Event()  # set once the rival holds the lock

    def rival() -> None:
        contended.wait(timeout=5.0)
        rival_blocked.set()  # at the contention point, about to block on the lock
        with exclusive_file_lock(lock_path):
            rival_acquired.set()

    rival_thread = threading.Thread(target=rival, daemon=True)
    rival_thread.start()

    def spy_write(path: Path, text: str) -> None:
        contended.set()  # tell the rival to start contending for the lock now
        # Happens-before, no timing race: wait until the rival has reached the
        # lock barrier, then assert it has NOT acquired — it cannot, because we
        # (inside create_sidecar) still hold the lock.
        assert rival_blocked.wait(timeout=5.0), "rival never reached the lock barrier"
        # Bounded wait, not a bare is_set(): in GREEN the rival is blocked in
        # ``flock`` and never sets the event, so this returns False deterministically;
        # in RED (unlocked) the uncontended rival acquires within the window and the
        # assertion fires. The barrier above guarantees the rival is actually racing.
        assert not rival_acquired.wait(timeout=0.5), "lock was not held during state.json write"
        real_atomic_write_text(path, text)

    monkeypatch.setattr(sidecar_module, "atomic_write_text", spy_write)

    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")

    # Lock released on return: the rival now acquires promptly.
    assert rival_acquired.wait(timeout=5.0), "lock was not released after create_sidecar returned"
    rival_thread.join(timeout=5.0)
    assert not rival_thread.is_alive()

    # The write still produced a valid sidecar.
    assert read_state(paths, "T1").task_id == "T1"
    assert (paths.sidecar("T1") / "db.sqlite").is_file()
