"""Spec 05 slice B — default ``git`` tool (read-only subcommand allowlist)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.git import GitTool


@pytest.fixture
def tool() -> GitTool:
    return GitTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    # We don't need a real repo for allowlist tests; tests that actually run git
    # init one explicitly.
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_git")


def test_declaration_is_safe_tier0(tool: GitTool) -> None:
    # The allowlist is intentionally read-only, so the tool itself is safe.
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_name(tool: GitTool) -> None:
    assert tool.name == "git"


@pytest.mark.parametrize(
    "subcommand",
    ["status", "diff", "log", "show", "branch", "rev-parse", "ls-files"],
)
def test_subcommand_allowlist_membership(tool: GitTool, subcommand: str) -> None:
    assert subcommand in tool.ALLOWED_SUBCOMMANDS


async def test_disallowed_subcommand_is_error_without_running(
    tool: GitTool, ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def spy_run_git(args, cwd):  # type: ignore[no-untyped-def]
        calls.append(args)
        return (0, "", "")

    import dream.tools.builtin.git as mod

    monkeypatch.setattr(mod, "run_git", spy_run_git)
    result = await tool.execute({"args": ["push", "origin", "main"]}, ctx)
    assert result.is_error is True
    assert calls == []  # never invoked git
    assert "push" in result.metadata["root_cause"]
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_empty_args_is_error(
    tool: GitTool, ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dream.tools.builtin.git as mod

    monkeypatch.setattr(mod, "run_git", lambda *a, **kw: (0, "", ""))
    result = await tool.execute({"args": []}, ctx)
    assert result.is_error is True


async def test_allowed_subcommand_invokes_run_git(
    tool: GitTool, ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run_git(args, cwd):  # type: ignore[no-untyped-def]
        captured["args"] = args
        captured["cwd"] = cwd
        return (0, "On branch main\nnothing to commit", "")

    import dream.tools.builtin.git as mod

    monkeypatch.setattr(mod, "run_git", fake_run_git)
    result = await tool.execute({"args": ["status"]}, ctx)
    assert result.is_error is False
    assert captured["args"] == ["status"]
    assert "On branch main" in result.content
    assert result.metadata["returncode"] == 0
    assert result.metadata["subcommand"] == "status"


async def test_nonzero_returncode_is_error_with_3part(
    tool: GitTool, ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_git(args, cwd):  # type: ignore[no-untyped-def]
        return (128, "", "fatal: not a git repository")

    import dream.tools.builtin.git as mod

    monkeypatch.setattr(mod, "run_git", fake_run_git)
    result = await tool.execute({"args": ["log"]}, ctx)
    assert result.is_error is True
    assert result.metadata["returncode"] == 128
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata
    assert "not a git repository" in result.content
