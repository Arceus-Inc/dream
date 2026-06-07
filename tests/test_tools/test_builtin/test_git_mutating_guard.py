"""CodeAnt #16/#27 lock-in — GitTool must reject mutating git forms.

GitTool is declared ``risk="safe"`` / read-only, so any invocation that can
mutate repository state MUST be rejected before ``run_git`` is ever called.
These tests fail without the per-subcommand argument validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import dream.tools.builtin.git as git_mod
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.git import GitTool


@pytest.fixture
def tool() -> GitTool:
    return GitTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_git_guard")


@pytest.fixture(autouse=True)
def _spy_run_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every run_git call so we can assert mutating forms never run."""
    calls: list[list[str]] = []

    def spy(args, cwd):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        return (0, "", "")

    monkeypatch.setattr(git_mod, "run_git", spy)
    return calls


@pytest.mark.parametrize(
    "args",
    [
        ["config", "--global", "user.email", "a@b.c"],
        ["config", "user.name", "evil"],
        ["branch", "new-branch"],
        ["branch", "-d", "main"],
        ["branch", "-D", "main"],
        ["branch", "-m", "old", "new"],
        ["tag", "v1.0.0"],
        ["tag", "-d", "v1.0.0"],
        ["tag", "-a", "v1", "-m", "msg"],
        ["remote", "add", "origin", "https://example.com/x.git"],
        ["remote", "remove", "origin"],
        ["remote", "set-url", "origin", "https://evil.example/x.git"],
        ["stash"],
        ["stash", "push"],
        ["stash", "pop"],
        ["stash", "drop"],
        ["stash", "clear"],
    ],
)
async def test_mutating_forms_rejected_without_running(
    tool: GitTool,
    ctx: ToolExecutionContext,
    _spy_run_git: list[list[str]],
    args: list[str],
) -> None:
    result = await tool.execute({"args": args}, ctx)
    assert result.is_error is True, f"{args} should be rejected"
    assert _spy_run_git == [], f"{args} must not reach run_git"
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


@pytest.mark.parametrize(
    "args",
    [
        ["status"],
        ["diff"],
        ["log", "--oneline"],
        ["show", "HEAD"],
        ["branch"],
        ["branch", "--list"],
        ["branch", "-a"],
        ["branch", "--contains", "HEAD"],
        ["tag"],
        ["tag", "--list"],
        ["tag", "-l", "v*"],
        ["config", "--get", "user.email"],
        ["config", "--list"],
        ["remote"],
        ["remote", "-v"],
        ["remote", "show", "origin"],
        ["remote", "get-url", "origin"],
        ["stash", "list"],
        ["stash", "show"],
    ],
)
async def test_read_only_forms_reach_run_git(
    tool: GitTool,
    ctx: ToolExecutionContext,
    _spy_run_git: list[list[str]],
    args: list[str],
) -> None:
    result = await tool.execute({"args": args}, ctx)
    assert result.is_error is False, f"{args} should be allowed"
    assert _spy_run_git == [args], f"{args} should reach run_git verbatim"


def test_mutating_subcommands_not_in_allowlist(tool: GitTool) -> None:
    # Genuinely-mutating-only subcommands are not even on the allowlist.
    for sub in ("push", "commit", "reset", "checkout", "merge", "rebase", "clean", "pull"):
        assert sub not in tool.ALLOWED_SUBCOMMANDS
