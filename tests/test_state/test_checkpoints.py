"""Spec 01 — checkpoints (git refs) and resume-from-checkpoint.

Checkpoints are refs under ``refs/dream/checkpoints/{task}/{n}`` (invisible to
``git branch``) that **survive worktree teardown** — the disposable worktree
dies, the durable checkpoint persists. Resume spawns a fresh worktree at any
checkpoint with parent lineage recorded in the new task's state.json.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.state.checkpoints import (
    gc_checkpoints,
    list_checkpoints,
    resume_from,
    write_checkpoint,
    write_done,
)
from dream.state.sidecar import read_state
from dream.swarm._worktree import WorktreeManager


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _ref_resolves(repo: Path, ref: str) -> bool:
    return (
        subprocess.run(["git", "rev-parse", "--verify", ref], cwd=repo, capture_output=True).returncode
        == 0
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("# repo\n")
    (r / ".gitignore").write_text(".dream/\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")
    return r


@pytest.fixture
def paths(repo: Path, tmp_path: Path) -> DreamPaths:
    return DreamPaths.resolve(repo, home=tmp_path / "home", env={})


@pytest.fixture
def mgr(paths: DreamPaths) -> WorktreeManager:
    return WorktreeManager(paths)


def test_write_checkpoint_creates_ref(paths: DreamPaths, mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1")
    sha = write_checkpoint(paths, "T1", 1)
    assert _ref_resolves(paths.repo, "refs/dream/checkpoints/T1/1")
    assert _git(paths.repo, "rev-parse", "refs/dream/checkpoints/T1/1") == sha


def test_checkpoint_ref_per_turn(paths: DreamPaths, mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1")
    for turn in (1, 2, 3):
        write_checkpoint(paths, "T1", turn)
    names = [name for name, _ in list_checkpoints(paths, "T1")]
    assert names == ["1", "2", "3"]


def test_write_done_creates_done_ref(paths: DreamPaths, mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1")
    write_checkpoint(paths, "T1", 1)
    write_done(paths, "T1")
    assert _ref_resolves(paths.repo, "refs/dream/checkpoints/T1/done")


def test_checkpoints_survive_teardown(paths: DreamPaths, mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1")
    for turn in (1, 2, 3):
        write_checkpoint(paths, "T1", turn)
    write_done(paths, "T1")

    mgr.remove_worktree("T1")
    assert not paths.worktree("T1").exists()  # worktree gone

    for turn in (1, 2, 3):
        assert _ref_resolves(paths.repo, f"refs/dream/checkpoints/T1/{turn}")
    assert _ref_resolves(paths.repo, "refs/dream/checkpoints/T1/done")


def test_resume_creates_new_worktree_with_parent_lineage(
    paths: DreamPaths, mgr: WorktreeManager
) -> None:
    mgr.create_worktree("T1")
    write_checkpoint(paths, "T1", 1)
    sha2 = write_checkpoint(paths, "T1", 2)

    info = resume_from(
        paths, "refs/dream/checkpoints/T1/2", "T2", harness_version="0.1.0"
    )
    assert info.slug == "T2"
    assert paths.worktree("T2").is_dir()
    state = read_state(paths, "T2")
    assert state.parent_checkpoint_ref == "refs/dream/checkpoints/T1/2"
    assert _git(paths.worktree("T2"), "rev-parse", "HEAD") == sha2  # tree at checkpoint


def test_resumed_tree_byte_matches_checkpoint(
    paths: DreamPaths, mgr: WorktreeManager
) -> None:
    wt = mgr.create_worktree("T1").path
    (wt / "data.txt").write_text("v1")
    write_checkpoint(paths, "T1", 1)
    (wt / "data.txt").write_text("v2")
    write_checkpoint(paths, "T1", 2)

    resume_from(paths, "refs/dream/checkpoints/T1/1", "T2", harness_version="0.1.0")
    assert (paths.worktree("T2") / "data.txt").read_text() == "v1"  # checkpoint-1 state


def test_resume_refuses_reused_task_id(paths: DreamPaths, mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1")
    write_checkpoint(paths, "T1", 1)
    with pytest.raises(ValueError, match="already in use"):
        resume_from(paths, "refs/dream/checkpoints/T1/1", "T1", harness_version="0.1.0")


def test_gc_removes_old_keeps_done(paths: DreamPaths, mgr: WorktreeManager) -> None:
    wt = mgr.create_worktree("T1").path
    # An old checkpoint: backdate the commit ~100 days, then ref it.
    old = str(int(time.time()) - 100 * 86400)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "old"],
        cwd=wt,
        check=True,
        capture_output=True,
        env={
            "GIT_COMMITTER_DATE": f"{old} +0000",
            "GIT_AUTHOR_DATE": f"{old} +0000",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "HOME": str(paths.home),
        },
    )
    old_sha = _git(wt, "rev-parse", "HEAD")
    _git(paths.repo, "update-ref", "refs/dream/checkpoints/T1/1", old_sha)
    write_done(paths, "T1")  # done points at the same old commit

    removed = gc_checkpoints(paths, "T1", older_than_days=30)
    assert removed == ["1"]
    assert not _ref_resolves(paths.repo, "refs/dream/checkpoints/T1/1")
    assert _ref_resolves(paths.repo, "refs/dream/checkpoints/T1/done")  # never GC'd
