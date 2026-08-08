"""Spec 13C.1 — per-tool side-effect surface for the permission gate.

``effects_for(input)`` reports the paths / command / network host a specific
invocation touches, so the dispatcher can build a PermissionRequest. Default is
no effects; path/command tools override. Read tools still report their target
path so the credential guard can block reading a secret.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.tools._base import BaseTool, ToolDeclaration, ToolEffects
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.apply_patch import ApplyPatchTool
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.file_read import FileReadTool
from dream.tools.builtin.file_write import FileWriteTool


class _EmptyInput(BaseModel):
    pass


class _PureTool(BaseTool):
    name = "pure"
    description = "a tool with no external effects"
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
    input_model = _EmptyInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        raise NotImplementedError


def test_default_effects_are_empty() -> None:
    assert _PureTool().effects_for({}) == ToolEffects()


def test_write_tool_reports_target_path() -> None:
    eff = FileWriteTool().effects_for({"path": "src/x.py", "content": "hi"})
    assert eff.target_paths == (Path("src/x.py"),)
    assert eff.command is None
    assert eff.network_host is None


def test_apply_patch_reports_target_paths() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@\n"
        "-x\n"
        "+y\n"
        "*** End Patch"
    )
    eff = ApplyPatchTool().effects_for({"patch": patch})
    assert Path("a.py") in eff.target_paths


def test_read_tool_reports_target_path() -> None:
    eff = FileReadTool().effects_for({"path": "secret/.env"})
    assert eff.target_paths == (Path("secret/.env"),)


def test_bash_tool_reports_command() -> None:
    eff = BashTool().effects_for({"command": "ls -la"})
    assert eff.command == "ls -la"
    assert eff.target_paths == ()
    assert eff.network_host is None


def test_task_create_reports_command_form() -> None:
    from dream.tools.builtin.task_create import TaskCreateTool

    eff = TaskCreateTool().effects_for(
        {"description": "d", "command": "rm -rf /tmp/x"}
    )
    # The spawned shell command must reach the gate's command-deny step.
    assert eff.command == "rm -rf /tmp/x"
    assert eff.target_paths == ()
    assert eff.network_host is None


def test_task_create_reports_argv_as_command() -> None:
    from dream.tools.builtin.task_create import TaskCreateTool

    eff = TaskCreateTool().effects_for(
        {"description": "d", "argv": ["git", "push", "--force"]}
    )
    # argv has no shell string, so it is surfaced as a shell-equivalent command
    # the deny patterns can screen.
    assert eff.command is not None
    assert "git" in eff.command and "push" in eff.command


def test_task_create_empty_invocation_reports_no_command() -> None:
    from dream.tools.builtin.task_create import TaskCreateTool

    # Neither command nor argv: the call will error inside execute(); the gate
    # has nothing to screen, so no synthetic command is invented.
    eff = TaskCreateTool().effects_for({"description": "d"})
    assert eff.command is None


def test_mcp_auth_reports_network_host() -> None:
    from pathlib import Path as _P

    from dream.mcp._types import AllowlistEntry
    from dream.tools.builtin.mcp_auth import McpAuthTool

    entry = AllowlistEntry(
        name="pw", endpoint="https://pw.example/mcp", transport="http"
    )
    tool = McpAuthTool(_StubManager({"pw": entry}), _P("creds.toml"))
    eff = tool.effects_for({"server_name": "pw", "mode": "bearer", "value": "s"})
    # Reconnecting an MCP server is a network action — gate as NETWORK.
    assert eff.network_host is not None
    assert "pw.example" in eff.network_host


def test_mcp_adapter_reports_network_host() -> None:
    from dream.mcp._types import AllowlistEntry, McpToolInfo
    from dream.tools.builtin.mcp_tool import McpToolAdapter

    entry = AllowlistEntry(
        name="pw", endpoint="https://pw.example/mcp", transport="http"
    )
    info = McpToolInfo(
        server_name="pw", name="navigate", description="", input_schema={}
    )
    adapter = McpToolAdapter(_StubManager({"pw": entry}), info)
    eff = adapter.effects_for({})
    assert eff.network_host is not None
    assert "pw.example" in eff.network_host


class _StubManager:
    """Minimal manager exposing ``entry_for`` for effect/tier derivation."""

    def __init__(self, entries: dict[str, Any]) -> None:
        self._entries = entries

    def entry_for(self, name: str) -> Any:
        return self._entries.get(name)
