"""Spec 01 — per-task sidecar bundle and schema-validated state.json.

The sidecar (logs/metrics/scratch/state.json) is the per-task now-state, deleted
with the worktree. state.json is updated atomically under an exclusive lock and
stamps the harness version so mismatched versions refuse to coexist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.state.sidecar import (
    TaskState,
    assert_no_version_conflicts,
    create_sidecar,
    ensure_version_compatible,
    read_state,
    remove_sidecar,
    update_state,
)
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
