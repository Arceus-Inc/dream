"""Spec 13C.2 — the permission gate inside EngineToolDispatcher.

Before executing a tool, the dispatcher builds a PermissionRequest from the
tool's per-call effects and runs it through the injected gate. A non-allow
decision returns a typed 3-part error and skips execute; with no gate wired,
nothing is gated (backward compatible).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.permissions import (
    PermissionDecision,
    PermissionRequest,
    Policy,
    SandboxTier,
    evaluate,
)
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool


class _EmptyInput(BaseModel):
    pass


class MutatingNoEffectsTool(BaseTool):
    """A mutating tier-1 tool that does NOT override ``effects_for`` — like
    ``task_stop`` / ``mcp_auth`` whose side effect is not a path/command/host."""

    name = "mutate_noeffects"
    description = "mutating, no path/command/network effects"
    declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content="mutated")


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in (FileReadTool(), FileWriteTool(), BashTool(), MutatingNoEffectsTool()):
        reg.register(tool, source=ToolSource.DEFAULT)
    return reg


def _dispatcher(tmp_path: Path, policy: Policy) -> EngineToolDispatcher:
    def gate(req: PermissionRequest) -> PermissionDecision:
        return evaluate(req, policy)

    return EngineToolDispatcher(
        registry=_registry(),
        working_dir=tmp_path,
        session_id="s",
        permission_gate=gate,
    )


async def test_credential_read_is_denied_without_executing(tmp_path: Path) -> None:
    policy = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"read_file": SandboxTier.READ_ONLY},
    )
    disp = _dispatcher(tmp_path, policy)
    content, is_error = await disp.dispatch(
        "read_file", {"path": str(Path.home() / ".ssh" / "id_rsa")}
    )
    assert is_error
    assert "Permission denied" in content
    assert "credential_guard" in content


async def test_dangerous_command_is_denied(tmp_path: Path) -> None:
    policy = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"bash": SandboxTier.REPO_WRITE},
    )
    disp = _dispatcher(tmp_path, policy)
    content, is_error = await disp.dispatch("bash", {"command": "rm -rf /"})
    assert is_error
    assert "command_deny" in content


async def test_in_repo_write_is_allowed_and_executes(tmp_path: Path) -> None:
    policy = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"write_file": SandboxTier.REPO_WRITE},
    )
    disp = _dispatcher(tmp_path, policy)
    _content, is_error = await disp.dispatch("write_file", {"path": "out.txt", "content": "hi"})
    assert not is_error
    assert (tmp_path / "out.txt").read_text() == "hi"


async def test_unpromoted_write_asks_with_promote_hint_and_skips_execute(tmp_path: Path) -> None:
    policy = Policy(tier=SandboxTier.REPO_WRITE, cwd=tmp_path)  # write_file not promoted
    disp = _dispatcher(tmp_path, policy)
    content, is_error = await disp.dispatch("write_file", {"path": "out.txt", "content": "hi"})
    assert is_error
    assert "tool-tier-overrides" in content
    assert not (tmp_path / "out.txt").exists()


async def test_mutating_tool_without_effects_is_usable_under_default_tier(
    tmp_path: Path,
) -> None:
    """A trusted, mutating tier-1 tool that reports no path/command/network
    effects (e.g. task_stop) must still run under the default repo-write
    policy: it is tier-gated as a WRITE, not ASK-denied."""
    policy = Policy(
        tier=SandboxTier.REPO_WRITE,
        cwd=tmp_path,
        required_tier={"mutate_noeffects": SandboxTier.REPO_WRITE},
    )
    disp = _dispatcher(tmp_path, policy)
    content, is_error = await disp.dispatch("mutate_noeffects", {})
    assert not is_error, content
    assert content == "mutated"


async def test_mutating_tool_without_effects_still_tier_gated(tmp_path: Path) -> None:
    """The fallback keeps the tool tier-gated: an untrusted mutating tool with
    no effects is asked (promotable), never silently allowed."""
    policy = Policy(tier=SandboxTier.REPO_WRITE, cwd=tmp_path)  # not promoted
    disp = _dispatcher(tmp_path, policy)
    content, is_error = await disp.dispatch("mutate_noeffects", {})
    assert is_error
    assert "tool-tier-overrides" in content


async def test_no_gate_does_not_block(tmp_path: Path) -> None:
    disp = EngineToolDispatcher(registry=_registry(), working_dir=tmp_path, session_id="s")
    _content, is_error = await disp.dispatch("write_file", {"path": "out.txt", "content": "hi"})
    assert not is_error
    assert (tmp_path / "out.txt").read_text() == "hi"
