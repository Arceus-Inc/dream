"""Spec 13C.2 — the permission gate inside EngineToolDispatcher.

Before executing a tool, the dispatcher builds a PermissionRequest from the
tool's per-call effects and runs it through the injected gate. A non-allow
decision returns a typed 3-part error and skips execute; with no gate wired,
nothing is gated (backward compatible).
"""

from __future__ import annotations

from pathlib import Path

from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.permissions import (
    PermissionDecision,
    PermissionRequest,
    Policy,
    SandboxTier,
    evaluate,
)
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in (FileReadTool(), FileWriteTool(), BashTool()):
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


async def test_no_gate_does_not_block(tmp_path: Path) -> None:
    disp = EngineToolDispatcher(registry=_registry(), working_dir=tmp_path, session_id="s")
    _content, is_error = await disp.dispatch("write_file", {"path": "out.txt", "content": "hi"})
    assert not is_error
    assert (tmp_path / "out.txt").read_text() == "hi"
