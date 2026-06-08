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
from dream.tools.builtin.bash import BashTool
from dream.tools.builtin.file_edit import FileEditTool
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


def test_edit_tool_reports_target_path() -> None:
    eff = FileEditTool().effects_for({"path": "a.py", "old_str": "x", "new_str": "y"})
    assert eff.target_paths == (Path("a.py"),)


def test_read_tool_reports_target_path() -> None:
    eff = FileReadTool().effects_for({"path": "secret/.env"})
    assert eff.target_paths == (Path("secret/.env"),)


def test_bash_tool_reports_command() -> None:
    eff = BashTool().effects_for({"command": "ls -la"})
    assert eff.command == "ls -la"
    assert eff.target_paths == ()
    assert eff.network_host is None
