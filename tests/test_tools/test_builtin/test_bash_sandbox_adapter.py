"""Spec 13B — the ``bash`` tool routes through the selected SandboxAdapter.

When a session wires a :class:`SandboxAdapter` into ``ctx.metadata`` under
``SANDBOX_CONTEXT_KEY``, the tool must execute through ``adapter.run`` (the one
execution mechanism) rather than spawning a subprocess itself. The
returncode/stdout/stderr/timeout the adapter reports map back into the
``ToolResult``; cwd-confinement is still enforced *before* the adapter is
called so a backend swap can't reopen the worktree-escape hole.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dream.sandbox import SANDBOX_CONTEXT_KEY, SandboxResult
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.bash import BashTool


@dataclass
class _FakeAdapter:
    """Records the args of the single ``run`` call and returns a canned result."""

    result: SandboxResult
    calls: list[dict[str, object]] = field(default_factory=list)

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
    ) -> SandboxResult:
        self.calls.append(
            {"command": command, "cwd": cwd, "env": env, "timeout_seconds": timeout_seconds}
        )
        return self.result


@pytest.fixture
def tool() -> BashTool:
    return BashTool()


def _ctx(working_dir: Path, adapter: _FakeAdapter) -> ToolExecutionContext:
    return ToolExecutionContext(
        working_dir=working_dir,
        session_id="s_bash_sbx",
        metadata={SANDBOX_CONTEXT_KEY: adapter},
    )


async def test_routes_through_adapter_with_command_cwd_and_timeout(
    tool: BashTool, tmp_path: Path
) -> None:
    adapter = _FakeAdapter(SandboxResult(returncode=0, stdout="hello\n"))
    ctx = _ctx(tmp_path, adapter)

    result = await tool.execute(
        {"command": "echo hello", "timeout_seconds": 42}, ctx
    )

    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call["command"] == "echo hello"
    assert call["cwd"] == tmp_path
    assert call["timeout_seconds"] == 42
    assert result.is_error is False
    assert "hello" in result.content
    assert result.metadata["returncode"] == 0
    assert result.metadata["timed_out"] is False


async def test_adapter_stderr_and_nonzero_exit_map_back(
    tool: BashTool, tmp_path: Path
) -> None:
    adapter = _FakeAdapter(SandboxResult(returncode=3, stdout="out", stderr="boom"))
    ctx = _ctx(tmp_path, adapter)

    result = await tool.execute({"command": "false"}, ctx)

    assert result.is_error is True
    assert result.metadata["returncode"] == 3
    assert "boom" in result.content
    # 3-part error contract preserved on the adapter path.
    assert "root_cause" in result.metadata
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_adapter_timeout_maps_back_to_timeout_contract(
    tool: BashTool, tmp_path: Path
) -> None:
    adapter = _FakeAdapter(
        SandboxResult(returncode=None, stderr="timed out after 1.0s", timed_out=True)
    )
    ctx = _ctx(tmp_path, adapter)

    result = await tool.execute({"command": "sleep 30", "timeout_seconds": 1}, ctx)

    assert result.is_error is True
    assert result.metadata["timed_out"] is True
    assert "timed out" in result.metadata["root_cause"].lower()
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_relative_cwd_confined_under_working_dir_before_adapter(
    tool: BashTool, tmp_path: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    adapter = _FakeAdapter(SandboxResult(returncode=0, stdout="ok"))
    ctx = _ctx(tmp_path, adapter)

    await tool.execute({"command": "pwd", "cwd": "sub"}, ctx)

    assert adapter.calls[0]["cwd"] == sub


async def test_cwd_escape_refused_without_calling_adapter(
    tool: BashTool, tmp_path: Path
) -> None:
    # An absolute cwd outside working_dir must be refused *before* the backend
    # runs — the confinement is the security boundary, not the backend.
    adapter = _FakeAdapter(SandboxResult(returncode=0))
    ctx = _ctx(tmp_path, adapter)

    result = await tool.execute({"command": "echo hi", "cwd": "/"}, ctx)

    assert result.is_error is True
    assert "working directory" in result.content.lower()
    assert adapter.calls == []


async def test_no_adapter_in_context_falls_back_to_own_execution(
    tool: BashTool, tmp_path: Path
) -> None:
    # Bare engine / older caller: no adapter wired → tool runs the command
    # itself, behavior unchanged.
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_bare")
    result = await tool.execute({"command": "echo bare"}, ctx)
    assert result.is_error is False
    assert "bare" in result.content
