"""CodeAnt #25 lock-in — bash must not deadlock on large output.

If the process is awaited before stdout is consumed, a command that writes
more than the OS pipe buffer (~64 KiB) blocks on write while we block on exit,
surfacing as a spurious timeout. Reading concurrently via communicate() fixes
it. This test writes ~1 MiB and must complete well within the timeout.
"""

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
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_bash_big")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell command")
async def test_large_stdout_does_not_deadlock(
    tool: BashTool, ctx: ToolExecutionContext
) -> None:
    # ~1 MiB of output, far exceeding the pipe buffer. With a generous timeout,
    # the only way this times out is the read-after-exit deadlock.
    cmd = "yes ABCDEFGHIJ | head -c 1000000"
    result = await tool.execute({"command": cmd, "timeout_seconds": 30}, ctx)
    assert result.metadata["timed_out"] is False
    assert result.is_error is False
    assert result.metadata["returncode"] == 0
    # Output is capped for display, but it must be non-trivial (not empty).
    assert len(result.content) > 1000
