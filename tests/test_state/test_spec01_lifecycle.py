"""Spec 01 — integration across worktree, sidecar, and checkpoints.

Per-file tests cover each module in isolation; this file proves they compose.
Claims under test, in spec-01 language:

- "checkpoints are git refs, not branches … they survive worktree teardown"
  (decision 11) — the load-bearing claim that makes resume possible.
- "resume creates a *new* task from any checkpoint ref" with parent lineage
  (decision 12).
- "the *now*-state stores … are git-ignored and never committed" (key decision 1).
- "no worktree-to-worktree communication" (decision 13) — exercised as path
  isolation: two tasks share no on-disk state.

Also includes one xfail that surfaces the missing ``db.sqlite`` from the spec's
sidecar bundle (decision 8) — a known gap, not a regression.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.state import checkpoints
from dream.state.checkpoints import (
    list_checkpoints,
    resume_from,
    write_checkpoint,
    write_done,
)
from dream.state.sidecar import create_sidecar, read_state
from dream.swarm._worktree import WorktreeManager


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _ref_exists(repo: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", ref], cwd=repo, capture_output=True
        ).returncode
        == 0
    )


def _commit_exists(repo: Path, sha: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", sha], cwd=repo, capture_output=True
        ).returncode
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


# --- the load-bearing claim: checkpoints outlive the worktree --------------


def test_checkpoint_ref_survives_worktree_teardown(
    paths: DreamPaths, mgr: WorktreeManager
) -> None:
    """The whole point of refs-not-branches: disposable worktree, durable checkpoint."""
    mgr.create_worktree("T1")
    sha = write_checkpoint(paths, "T1", 1)

    # Worktree goes away …
    assert mgr.remove_worktree("T1") is True
    assert not paths.worktree("T1").exists()

    # … but both the ref and the commit it pins must remain reachable in the
    # main repo. This is what makes resume_from possible.
    assert _ref_exists(paths.repo, paths.checkpoint_ref("T1", 1))
    assert _commit_exists(paths.repo, sha)
    assert list_checkpoints(paths, "T1") == [("1", sha)]


def test_checkpoint_refs_invisible_to_git_branch(
    paths: DreamPaths, mgr: WorktreeManager
) -> None:
    """Decision 11: refs under refs/dream/checkpoints don't clutter ``git branch``."""
    mgr.create_worktree("T1")
    write_checkpoint(paths, "T1", 1)
    write_done(paths, "T1")

    # `git branch -a` lists refs/heads + refs/remotes — never refs/dream/*.
    branches = _git(paths.repo, "branch", "-a")
    assert "dream/checkpoints" not in branches
    assert "T1/1" not in branches
    assert "T1/done" not in branches


# --- full happy-path lifecycle --------------------------------------------


def test_full_lifecycle_create_checkpoint_resume_remove(
    paths: DreamPaths, mgr: WorktreeManager
) -> None:
    """create → sidecar → checkpoint → resume(new task) → cleanup."""
    # 1. start T1
    info = mgr.create_worktree("T1")
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")

    # 2. do work in the worktree and checkpoint it
    (info.path / "work.txt").write_text("turn-1 output\n")
    sha = write_checkpoint(paths, "T1", 1)

    # 3. tear T1 down — checkpoint must still be reachable from the main repo
    mgr.remove_worktree("T1")
    assert _commit_exists(paths.repo, sha)

    # 4. resume *as a new task* from T1's checkpoint
    ref = paths.checkpoint_ref("T1", 1)
    new_info = resume_from(paths, ref, "T2", harness_version="0.1.0", base_branch="main")

    # The resumed worktree must contain the work T1 committed …
    assert (new_info.path / "work.txt").read_text() == "turn-1 output\n"
    # … and T2's state.json must record T1's checkpoint as its parent.
    state = read_state(paths, "T2")
    assert state.parent_checkpoint_ref == ref
    assert state.task_id == "T2"


# --- failure-rollback contracts -------------------------------------------


def test_resume_rolls_back_worktree_on_sidecar_failure(
    paths: DreamPaths, mgr: WorktreeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resume_from's try/except claims to roll back the new worktree on any failure.

    If the rollback is silently broken, the task-id is leaked and can never be
    reused — exactly the corruption the docstring promises to prevent.
    """
    mgr.create_worktree("T1")
    write_checkpoint(paths, "T1", 1)
    ref = paths.checkpoint_ref("T1", 1)

    def _boom(*a: object, **kw: object) -> None:
        raise RuntimeError("sidecar broke")

    monkeypatch.setattr(checkpoints, "create_sidecar", _boom)

    with pytest.raises(RuntimeError, match="sidecar broke"):
        resume_from(paths, ref, "T2", harness_version="0.1.0", base_branch="main")

    # The worktree that resume_from created before the sidecar step must be gone,
    # and the task-id must be reusable for a fresh attempt.
    assert not paths.worktree("T2").exists()


# --- system-of-record invariant: now-state stays out of git ---------------


def test_dream_dir_is_gitignored_after_ensure(paths: DreamPaths) -> None:
    """Spec 00 invariant 2 + Spec 01 key decision 1: .dream/ is *now*-state, never committed."""
    paths.ensure()
    # Create files in every now-state subdir we own.
    (paths.worktrees_dir / "T1").mkdir(parents=True, exist_ok=True)
    (paths.worktrees_dir / "T1" / "junk.txt").write_text("scratch")
    (paths.sidecars_dir / "T1").mkdir(parents=True, exist_ok=True)
    (paths.sidecars_dir / "T1" / "state.json").write_text("{}")
    (paths.coordination_dir / "board.sqlite").write_text("")

    status = _git(paths.repo, "status", "--porcelain")
    # Nothing under .dream/ should appear as tracked, untracked, or modified.
    assert ".dream" not in status, (
        f"git sees state under .dream/ — gitignore is broken:\n{status}"
    )


# --- isolation: two tasks share nothing on disk ---------------------------


def test_two_tasks_have_disjoint_storage(paths: DreamPaths, mgr: WorktreeManager) -> None:
    """Decision 13: no worktree-to-worktree communication, enforced at the path layer."""
    info1 = mgr.create_worktree("T1")
    info2 = mgr.create_worktree("T2")
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    create_sidecar(paths, "T2", base_branch="main", harness_version="0.1.0")

    # Worktrees and sidecars are siblings, not nested; the only shared ancestor
    # is the .dream/ root itself.
    assert info1.path != info2.path
    assert info1.path.parent == info2.path.parent
    assert paths.sidecar("T1") != paths.sidecar("T2")
    assert paths.sidecar("T1").parent == paths.sidecar("T2").parent
    # Checkpoint ref namespaces are disjoint too.
    assert paths.checkpoint_ref("T1", 1) != paths.checkpoint_ref("T2", 1)


# --- spec gap: sidecar bundle is missing db.sqlite ------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Spec 01 decision 8 lists `db.sqlite` as part of the sidecar bundle "
        "(per-task structured state). Current create_sidecar only creates "
        "logs/, metrics/, scratch/, state.json. When db.sqlite is added, "
        "remove this xfail."
    ),
)
def test_sidecar_includes_db_sqlite_per_spec(paths: DreamPaths) -> None:
    create_sidecar(paths, "T1", base_branch="main", harness_version="0.1.0")
    assert (paths.sidecar("T1") / "db.sqlite").exists()
