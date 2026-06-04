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
    create_sidecar,
    ensure_version_compatible,
    read_state,
    remove_sidecar,
    update_state,
)


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
