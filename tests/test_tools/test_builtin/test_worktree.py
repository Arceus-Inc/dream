"""Default ``enter_worktree`` / ``exit_worktree`` tools — git worktree lifecycle.

Mutating (tier 1). Both route git through ``dream.utils.git.run_git`` and confine
paths under the repo root. Tests build a throwaway repo and exercise the round trip.
"""

from __future__ import annotations

from pathlib import Path

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.enter_worktree import EnterWorktreeTool
from dream.tools.builtin.exit_worktree import ExitWorktreeTool
from dream.utils.git import run_git


def _ctx(working_dir: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=working_dir, session_id="s_test", metadata={})


def _repo(tmp_path: Path) -> Path:
    run_git(["init", "-b", "main"], cwd=tmp_path)
    run_git(["config", "user.email", "t@example.com"], cwd=tmp_path)
    run_git(["config", "user.name", "tester"], cwd=tmp_path)
    run_git(["commit", "--allow-empty", "-m", "init"], cwd=tmp_path)
    return tmp_path


def test_worktree_tools_are_mutating_tier_1() -> None:
    for tool in (EnterWorktreeTool(), ExitWorktreeTool()):
        assert tool.declaration.risk == "mutating"
        assert tool.declaration.tier_required == 1
        assert tool.is_read_only() is False


async def test_enter_worktree_creates_branch_checkout(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = await EnterWorktreeTool().execute({"branch": "feature-x"}, _ctx(repo))
    assert result.is_error is False, result.content
    wt = repo / ".harness" / "worktrees" / "feature-x"
    assert wt.is_dir()
    assert (wt / ".git").exists()
    # Branch was created.
    _rc, out, _err = run_git(["branch", "--list", "feature-x"], cwd=repo)
    assert "feature-x" in out


async def test_enter_then_exit_worktree_round_trip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    created = await EnterWorktreeTool().execute({"branch": "temp"}, _ctx(repo))
    assert created.is_error is False
    wt_path = created.metadata["path"]
    removed = await ExitWorktreeTool().execute({"path": wt_path}, _ctx(repo))
    assert removed.is_error is False, removed.content
    assert not (repo / ".harness" / "worktrees" / "temp").exists()


async def test_enter_worktree_outside_git_is_error(tmp_path: Path) -> None:
    result = await EnterWorktreeTool().execute({"branch": "x"}, _ctx(tmp_path))
    assert result.is_error is True
    assert "git repository" in result.content.lower()


async def test_enter_worktree_path_escape_is_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = await EnterWorktreeTool().execute(
        {"branch": "x", "path": "../escapee"}, _ctx(repo)
    )
    assert result.is_error is True
    assert "outside the repository" in result.content.lower()


async def test_exit_worktree_unknown_path_is_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = await ExitWorktreeTool().execute({"path": ".harness/worktrees/ghost"}, _ctx(repo))
    assert result.is_error is True
