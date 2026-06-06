"""Spec 05 slice B — default ``read_offloaded`` tool.

Wraps ``dream.services.tool_outputs.read_offloaded`` so the agent can pull
back arbitrary slices of a sidecar-spilled tool output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.read_offloaded import ReadOffloadedTool


@pytest.fixture
def tool() -> ReadOffloadedTool:
    return ReadOffloadedTool()


@pytest.fixture
def ctx_with_scratch(tmp_path: Path) -> ToolExecutionContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return ToolExecutionContext(working_dir=tmp_path, session_id="s_offload", scratch_dir=scratch)


def test_declaration_is_safe_tier0(tool: ReadOffloadedTool) -> None:
    assert tool.declaration.risk == "safe"
    assert tool.declaration.tier_required == 0
    assert tool.is_read_only() is True


def test_name(tool: ReadOffloadedTool) -> None:
    assert tool.name == "read_offloaded"


async def test_reads_full_offloaded_file(
    tool: ReadOffloadedTool, ctx_with_scratch: ToolExecutionContext
) -> None:
    scratch = ctx_with_scratch.scratch_dir
    assert scratch is not None
    (scratch / "abc.txt").write_text("hello world", encoding="utf-8")
    result = await tool.execute({"path": "abc.txt"}, ctx_with_scratch)
    assert result.is_error is False
    assert result.content == "hello world"
    assert result.metadata["bytes_read"] == len("hello world")


async def test_reads_slice(tool: ReadOffloadedTool, ctx_with_scratch: ToolExecutionContext) -> None:
    scratch = ctx_with_scratch.scratch_dir
    assert scratch is not None
    (scratch / "abc.txt").write_text("0123456789", encoding="utf-8")
    result = await tool.execute({"path": "abc.txt", "start": 2, "end": 6}, ctx_with_scratch)
    assert result.is_error is False
    assert result.content == "2345"


async def test_path_traversal_rejected(
    tool: ReadOffloadedTool, ctx_with_scratch: ToolExecutionContext
) -> None:
    result = await tool.execute({"path": "../escape.txt"}, ctx_with_scratch)
    assert result.is_error is True
    assert "traversal" in result.metadata["root_cause"].lower()
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_missing_file_is_error(
    tool: ReadOffloadedTool, ctx_with_scratch: ToolExecutionContext
) -> None:
    result = await tool.execute({"path": "ghost.txt"}, ctx_with_scratch)
    assert result.is_error is True
    assert "not found" in result.metadata["root_cause"].lower()


async def test_missing_scratch_dir_is_error(tool: ReadOffloadedTool, tmp_path: Path) -> None:
    ctx = ToolExecutionContext(working_dir=tmp_path, session_id="s_off", scratch_dir=None)
    result = await tool.execute({"path": "abc.txt"}, ctx)
    assert result.is_error is True
    assert "scratch" in result.metadata["root_cause"].lower()
