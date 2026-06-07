"""CodeAnt #13/#15 lock-in — run_subprocess error + timeout-race handling.

#13: spawn-time failures (missing executable, invalid cwd) must return a
structured ``is_error=True`` ToolResult, not raise.
#15: timeout cleanup must suppress the already-exited race (ProcessLookupError)
so the structured timeout result is always returned.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dream.contracts.tool import ToolResult
from dream.tools._context import ToolExecutionContext


@pytest.fixture
def ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_subproc")


async def test_missing_executable_returns_structured_error(ctx: ToolExecutionContext) -> None:
    result = await ctx.run_subprocess(["this_executable_does_not_exist_xyz", "--help"])
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.metadata["returncode"] is None
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_invalid_cwd_returns_structured_error(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_subproc")
    missing = tmp_path / "does" / "not" / "exist"
    result = await ctx.run_subprocess(["echo", "hi"], cwd=missing)
    assert result.is_error is True
    assert "root_cause" in result.metadata


async def test_timeout_returns_structured_result(ctx: ToolExecutionContext) -> None:
    result = await ctx.run_subprocess(["sleep", "30"], timeout=0.2)
    assert result.is_error is True
    assert result.metadata["timeout_seconds"] == 0.2
    assert "timed out" in result.content.lower()
    assert "root_cause" in result.metadata


async def test_timeout_suppresses_already_exited_race(
    ctx: ToolExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the child exiting between the timeout firing and kill()."""

    class _FakeProc:
        returncode = 0

        async def communicate(self):  # type: ignore[no-untyped-def]
            # Sleep long enough to be cancelled by wait_for's timeout.
            await asyncio.sleep(10)
            return (b"", b"")

        def kill(self) -> None:
            # The child already exited -> kill() raises ProcessLookupError.
            raise ProcessLookupError

        async def wait(self) -> int:
            return 0

    async def fake_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # Must not raise ProcessLookupError; must return the structured timeout.
    result = await ctx.run_subprocess(["whatever"], timeout=0.05)
    assert result.is_error is True
    assert result.metadata["timeout_seconds"] == 0.05
