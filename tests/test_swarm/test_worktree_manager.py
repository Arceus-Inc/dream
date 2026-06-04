"""Spec 01 — WorktreeManager lifecycle: create / fast-resume / remove / list / cleanup.

These exercise real git worktrees nested under the gitignored ``.dream/worktrees``
root, plus common-dir symlinking and agent-id persistence (so cleanup_stale works).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.swarm._worktree import WorktreeManager, WorktreeSlug


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


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
def mgr(repo: Path, tmp_path: Path) -> WorktreeManager:
    paths = DreamPaths.resolve(repo, home=tmp_path / "home", env={})
    return WorktreeManager(paths)


def test_create_worktree_happy_path(mgr: WorktreeManager) -> None:
    info = mgr.create_worktree("T1")
    assert info.slug == "T1"
    assert info.branch == "worktree-T1"
    assert info.path.is_dir()
    assert (info.path / "README.md").read_text() == "# repo\n"  # checked out from HEAD
    assert (info.path / ".git").exists()  # a real git worktree


def test_create_worktree_fast_resume(mgr: WorktreeManager) -> None:
    first = mgr.create_worktree("T1")
    second = mgr.create_worktree("T1")  # would error if it re-ran `git worktree add`
    assert second.path == first.path
    assert second.branch == first.branch


def test_create_resets_orphan_branch(mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1")
    mgr.remove_worktree("T1")  # leaves the worktree-T1 branch behind
    again = mgr.create_worktree("T1")  # -B must reset, not collide
    assert again.path.is_dir()


def test_create_accepts_worktree_slug_object(mgr: WorktreeManager) -> None:
    info = mgr.create_worktree(WorktreeSlug("task-9"))
    assert info.slug == "task-9"


def test_nested_slug_flattened_dir(mgr: WorktreeManager) -> None:
    info = mgr.create_worktree("feat/login")
    assert info.path.name == "feat+login"
    assert info.branch == "worktree-feat+login"
    listed = mgr.list_worktrees()
    assert any(w.slug == "feat/login" for w in listed)  # '+' restored to '/'


def test_remove_worktree_returns_true_and_removes(mgr: WorktreeManager) -> None:
    info = mgr.create_worktree("T1")
    assert mgr.remove_worktree("T1") is True
    assert not info.path.exists()


def test_remove_worktree_absent_returns_false(mgr: WorktreeManager) -> None:
    assert mgr.remove_worktree("never-made") is False


def test_remove_worktree_removes_symlinks_first(mgr: WorktreeManager, repo: Path) -> None:
    node_modules = repo / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg.txt").write_text("dep")

    info = mgr.create_worktree("T1")
    link = info.path / "node_modules"
    assert link.is_symlink()  # symlinked, not copied

    mgr.remove_worktree("T1")
    assert node_modules.is_dir()  # shared original preserved
    assert (node_modules / "pkg.txt").read_text() == "dep"


def test_symlink_common_dirs_created(mgr: WorktreeManager, repo: Path) -> None:
    venv = repo / ".venv"
    venv.mkdir()
    (venv / "marker").write_text("v")
    info = mgr.create_worktree("T1")
    link = info.path / ".venv"
    assert link.is_symlink()
    assert (link / "marker").read_text() == "v"


def test_symlink_failure_is_non_fatal(
    mgr: WorktreeManager, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "node_modules").mkdir()

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("no symlinks here")

    monkeypatch.setattr(Path, "symlink_to", boom)
    info = mgr.create_worktree("T1")  # must still succeed
    assert info.path.is_dir()
    assert not (info.path / "node_modules").exists()


def test_list_worktrees_recovers_slug_and_agent(mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1", agent_id="A1")
    mgr.create_worktree("T2", agent_id="A2")
    listed = {w.slug: w for w in mgr.list_worktrees()}
    assert set(listed) == {"T1", "T2"}
    assert listed["T1"].agent_id == "A1"
    assert listed["T2"].agent_id == "A2"


def test_cleanup_stale_prunes_only_dead_agents(mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1", agent_id="A1")  # dead
    mgr.create_worktree("T2", agent_id="A2")  # alive
    removed = mgr.cleanup_stale(active_agent_ids={"A2"})
    assert removed == ["T1"]
    assert {w.slug for w in mgr.list_worktrees()} == {"T2"}


def test_cleanup_stale_none_sweeps_all_agent_owned(mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1", agent_id="A1")
    mgr.create_worktree("T2", agent_id="A2")
    removed = mgr.cleanup_stale(active_agent_ids=None)
    assert set(removed) == {"T1", "T2"}
    assert mgr.list_worktrees() == []


def test_create_raises_when_git_add_fails(
    mgr: WorktreeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dream.swarm._worktree.run_git", lambda *_a, **_k: (1, "", "boom"))
    with pytest.raises(RuntimeError, match="git worktree add failed"):
        mgr.create_worktree("T1")


def test_list_handles_corrupt_meta(mgr: WorktreeManager) -> None:
    mgr.create_worktree("T1", agent_id="A1")
    (mgr.base_dir / "T1.meta.json").write_text("not json{")
    listed = mgr.list_worktrees()
    assert [w.slug for w in listed] == ["T1"]
    assert listed[0].agent_id is None  # corrupt meta degrades gracefully
    assert isinstance(listed[0].created_at, float)


def test_two_concurrent_worktrees_isolated(mgr: WorktreeManager) -> None:
    a = mgr.create_worktree("T1")
    b = mgr.create_worktree("T2")
    assert a.path != b.path
    assert a.path.is_dir() and b.path.is_dir()
    (a.path / "scratch.txt").write_text("a-only")
    assert not (b.path / "scratch.txt").exists()  # no shared working tree
