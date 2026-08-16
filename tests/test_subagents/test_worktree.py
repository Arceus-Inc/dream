"""Worktree isolation: child cwd confinement and ephemeral cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.permissions import Outcome, PermissionDecision, PermissionRequest
from dream.subagents._declaration import Subagent
from dream.subagents._delegate import build_child_prompt
from dream.subagents._inline_executor import _build_subagent_manifest
from dream.subagents._isolation import IsolationMode
from dream.subagents._overlay_gate import confine_permission_gate
from dream.subagents._worktree import SubagentWorktreeFactory, forget_worktree
from dream.utils.git import run_git


def _allow(_request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision(outcome=Outcome.ALLOW, reason="parent allow", rule="test")


def _init_repo(repo: Path) -> Path:
    repo.mkdir()
    run_git(["init", "-b", "main"], cwd=repo)
    run_git(["config", "user.email", "test@example.com"], cwd=repo)
    run_git(["config", "user.name", "test"], cwd=repo)
    run_git(["config", "commit.gpgsign", "false"], cwd=repo)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    run_git(["add", "README.md"], cwd=repo)
    run_git(["commit", "-m", "init"], cwd=repo)
    return repo


def test_confine_allows_write_inside_child_cwd(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    target = child / "note.txt"
    gate = confine_permission_gate(_allow, child)
    decision = gate(
        PermissionRequest(tool_name="write_file", is_read_only=False, target_paths=(target,))
    )
    assert decision.outcome is Outcome.ALLOW


def test_confine_denies_write_to_parent_tree(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    outside = parent / "secret.txt"
    gate = confine_permission_gate(_allow, child)
    decision = gate(
        PermissionRequest(tool_name="write_file", is_read_only=False, target_paths=(outside,))
    )
    assert decision.outcome is Outcome.DENY
    assert decision.rule == "subagent_worktree_confine"


def test_worktree_create_and_safe_remove(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    scratch = tmp_path / "scratch"
    factory = SubagentWorktreeFactory(scratch_dir=scratch, parent_cwd=repo)
    worktree = factory.create("explore")
    assert worktree.path.is_dir()
    assert (worktree.path / "README.md").is_file()
    worktree.remove()
    assert not worktree.path.exists()
    rc, branches, _ = run_git(["branch", "--list", worktree.branch], cwd=repo)
    assert rc == 0
    assert worktree.branch not in branches


def test_confine_denies_absolute_path_shell_write(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    gate = confine_permission_gate(_allow, child)
    decision = gate(
        PermissionRequest(
            tool_name="bash",
            is_read_only=True,
            command=f"echo leaked > {tmp_path / 'escape.txt'}",
        )
    )
    assert decision.outcome is Outcome.DENY
    assert decision.rule == "subagent_worktree_confine"
    assert "unconfinable" in decision.reason


def test_worktree_manifest_drops_unconfinable_commands() -> None:
    agent = Subagent(
        name="isolated",
        description="writes in a scratch tree",
        tools=("read_file", "bash", "execute_code", "write_file"),
        isolation=IsolationMode.WORKTREE,
    )
    manifest = _build_subagent_manifest(agent, parent_tools=None)
    assert "bash" not in manifest.tools
    assert "execute_code" not in manifest.tools
    assert "read_file" in manifest.tools
    assert "write_file" in manifest.tools


def test_forget_worktree_prunes_git_metadata(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    scratch = tmp_path / "scratch"
    factory = SubagentWorktreeFactory(scratch_dir=scratch, parent_cwd=repo)
    worktree = factory.create("explore")
    listed_before = run_git(["worktree", "list", "--porcelain"], cwd=repo)[1]
    assert str(worktree.path) in listed_before

    forget_worktree(worktree.repo_root, worktree.path, branch=worktree.branch)
    assert not worktree.path.exists()
    listed_after = run_git(["worktree", "list", "--porcelain"], cwd=repo)[1]
    assert str(worktree.path) not in listed_after
    rc, branches, _ = run_git(["branch", "--list", worktree.branch], cwd=repo)
    assert rc == 0
    assert worktree.branch not in branches


def test_failed_add_prunes_before_rmtree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "repo")
    factory = SubagentWorktreeFactory(scratch_dir=tmp_path / "scratch", parent_cwd=repo)
    calls: list[list[str]] = []
    original = run_git

    def tracking_run_git(
        args: list[str], *, cwd: Path, env: object = None, timeout: float | None = None
    ) -> tuple[int, str, str]:
        calls.append(list(args))
        if args[:2] == ["worktree", "add"]:
            return 1, "", "simulated add failure"
        if args[0] == "rev-parse":
            return original(args, cwd=cwd)
        return 0, "", ""

    from dream.subagents import _worktree as worktree_mod

    monkeypatch.setattr(worktree_mod, "run_git", tracking_run_git)
    with pytest.raises(RuntimeError, match="git worktree add failed"):
        factory.create("explore")
    assert any(item[:2] == ["worktree", "remove"] for item in calls)
    assert any(item[:2] == ["worktree", "prune"] for item in calls)
    remove_at = next(i for i, item in enumerate(calls) if item[:2] == ["worktree", "remove"])
    prune_at = next(i for i, item in enumerate(calls) if item[:2] == ["worktree", "prune"])
    assert remove_at < prune_at


def test_ephemeral_workspace_is_documented_in_prompt() -> None:
    prompt = build_child_prompt(
        "map src",
        workspace_path="/tmp/child",
        ephemeral_workspace=True,
    )
    assert "ephemeral git worktree" in prompt
    assert IsolationMode.WORKTREE.value == "worktree"
