"""Spec 05 slice B — default ``bash`` tool."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.bash import BashTool


@pytest.fixture
def tool() -> BashTool:
    return BashTool()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_bash")


def test_declaration_is_mutating_tier_high(tool: BashTool) -> None:
    assert tool.declaration.risk == "mutating"
    assert tool.declaration.tier_required >= 1
    # Tool itself is worst-case mutating.
    assert tool.is_read_only() is False


def test_name(tool: BashTool) -> None:
    assert tool.name == "bash"


async def test_runs_simple_command_and_captures_stdout(
    tool: BashTool, ctx: ToolExecutionContext
) -> None:
    result = await tool.execute({"command": "echo hello"}, ctx)
    assert result.is_error is False
    assert "hello" in result.content
    assert result.metadata["returncode"] == 0
    assert result.metadata["timed_out"] is False


async def test_nonzero_exit_is_error(tool: BashTool, ctx: ToolExecutionContext) -> None:
    if sys.platform == "win32":
        cmd = "exit 7"
    else:
        cmd = "exit 7"
    result = await tool.execute({"command": cmd}, ctx)
    assert result.is_error is True
    assert result.metadata["returncode"] == 7
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_timeout_kills_process(tool: BashTool, ctx: ToolExecutionContext) -> None:
    if sys.platform == "win32":
        # `ping -n N 127.0.0.1` sleeps roughly N seconds without needing a tty,
        # which `timeout /T` does (it errors immediately with stdin redirected).
        cmd = "ping -n 30 127.0.0.1"
    else:
        cmd = "sleep 30"
    result = await tool.execute({"command": cmd, "timeout_seconds": 1}, ctx)
    assert result.is_error is True
    assert result.metadata["timed_out"] is True
    assert "timed out" in result.metadata["root_cause"].lower()


async def test_respects_cwd(tool: BashTool, ctx: ToolExecutionContext, tmp_path: Path) -> None:
    if sys.platform == "win32":
        cmd = "cd"  # cmd.exe prints cwd
    else:
        cmd = "pwd"
    result = await tool.execute({"command": cmd}, ctx)
    assert result.is_error is False
    # tmp_path may have a short-name representation on Windows; check by suffix.
    assert tmp_path.name in result.content or str(tmp_path) in result.content


async def test_cwd_override(tool: BashTool, ctx: ToolExecutionContext, tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    cmd = "cd" if sys.platform == "win32" else "pwd"
    result = await tool.execute({"command": cmd, "cwd": str(sub)}, ctx)
    assert result.is_error is False
    assert "sub" in result.content


async def test_interactive_scaffold_preflight_blocks(
    tool: BashTool, ctx: ToolExecutionContext
) -> None:
    result = await tool.execute({"command": "npx create-next-app my-app"}, ctx)
    assert result.is_error is True
    assert result.metadata.get("interactive_required") is True
    # 3-part error contract.
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_interactive_scaffold_with_non_interactive_flag_passes_preflight(
    tool: BashTool, ctx: ToolExecutionContext
) -> None:
    # We don't actually want to run npx here -- just verify preflight allows it
    # by using a benign command that contains both markers.
    result = await tool.execute({"command": "echo npx create-next-app --yes"}, ctx)
    assert result.is_error is False
    assert result.metadata.get("interactive_required", False) is False


async def test_is_read_only_for_downclassifies_safe_invocations(
    tool: BashTool,
) -> None:
    # Per-call read-only refinement so 'cat foo' / 'ls' can be treated as safe.
    assert tool.is_read_only_for({"command": "ls"}) is True
    assert tool.is_read_only_for({"command": "cat README.md"}) is True
    assert tool.is_read_only_for({"command": "rm -rf /"}) is False
    assert tool.is_read_only_for({"command": "git status"}) is True


async def test_metadata_carries_argv_summary(tool: BashTool, ctx: ToolExecutionContext) -> None:
    result = await tool.execute({"command": "echo ok"}, ctx)
    assert "command" in result.metadata
    assert "echo ok" in result.metadata["command"]


# --- cwd confinement (worktree-escape regression) ---------------------------


async def test_dot_cwd_means_working_dir_not_process_cwd(
    tool: BashTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    # Regression: a relative cwd="." must resolve to the harness working_dir,
    # not the process cwd. A worker passing cwd="." was escaping its worktree
    # and operating on the host repo.
    (tmp_path / "marker.txt").write_text("MARKER", encoding="utf-8")
    result = await tool.execute({"command": "cat marker.txt", "cwd": "."}, ctx)
    assert result.is_error is False
    assert "MARKER" in result.content


async def test_relative_subdir_cwd_resolves_under_working_dir(
    tool: BashTool, ctx: ToolExecutionContext, tmp_path: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inner.txt").write_text("INSUB", encoding="utf-8")
    result = await tool.execute({"command": "cat inner.txt", "cwd": "sub"}, ctx)
    assert result.is_error is False
    assert "INSUB" in result.content


async def test_absolute_cwd_outside_working_dir_is_rejected(
    tool: BashTool, ctx: ToolExecutionContext
) -> None:
    # An absolute cwd that escapes the working_dir must be refused with a
    # structured error, not silently executed in the host filesystem.
    result = await tool.execute({"command": "echo hi", "cwd": "/"}, ctx)
    assert result.is_error is True
    assert "working directory" in result.content.lower()
    assert result.metadata.get("root_cause")
