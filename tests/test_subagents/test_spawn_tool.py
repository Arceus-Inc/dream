"""Tests for the spawn_subagent tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dream.subagents._declaration import Subagent, SubagentSet
from dream.subagents._projection import SubagentResult
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.spawn_subagent import (
    HARNESS_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
    SpawnSubagentTool,
)


def _make_ctx(
    subagent_set: SubagentSet | None = None,
    harness: Any = None,
) -> ToolExecutionContext:
    """Build a ToolExecutionContext with subagent context wired."""
    metadata: dict[str, Any] = {}
    if subagent_set is not None:
        metadata[SUBAGENT_SET_CONTEXT_KEY] = subagent_set
    if harness is not None:
        metadata[HARNESS_KEY] = harness
    return ToolExecutionContext(
        working_dir=Path("/tmp/test"),
        session_id="test-session",
        metadata=metadata,
    )


def _simple_subagent_set() -> SubagentSet:
    return SubagentSet(
        agents={
            "reviewer": Subagent(
                name="reviewer",
                description="Reviews code for correctness",
                tools=("read_file", "grep"),
            ),
        }
    )


class TestSpawnSubagentTool:
    async def test_no_subagent_set(self) -> None:
        tool = SpawnSubagentTool()
        ctx = _make_ctx(subagent_set=None)
        result = await tool.execute({"name": "reviewer", "prompt": "review this"}, ctx)
        assert result.is_error
        assert "configured" in result.content

    async def test_unknown_subagent_name(self) -> None:
        tool = SpawnSubagentTool()
        ctx = _make_ctx(subagent_set=_simple_subagent_set())
        result = await tool.execute({"name": "unknown_agent", "prompt": "do stuff"}, ctx)
        assert result.is_error
        assert "not found" in result.content
        assert "reviewer" in result.content

    async def test_no_harness_returns_error(self) -> None:
        """Without a harness wired, the tool returns a structured error."""
        tool = SpawnSubagentTool()
        ctx = _make_ctx(
            subagent_set=_simple_subagent_set(),
            harness=None,
        )
        result = await tool.execute({"name": "reviewer", "prompt": "review this code"}, ctx)
        assert result.is_error
        assert "harness" in result.content.lower()

    async def test_valid_spawn_with_harness(self) -> None:
        """With a harness wired, the tool creates a real bounded session."""
        tool = SpawnSubagentTool()
        mock_harness = AsyncMock()

        mock_result = SubagentResult(
            name="reviewer",
            output="LGTM — no issues found.",
            success=True,
            turns_used=3,
            tool_calls=2,
            tool_errors=0,
        )

        with patch(
            "dream.subagents._inline_executor.run_subagent_inline",
            return_value=mock_result,
        ):
            ctx = _make_ctx(
                subagent_set=_simple_subagent_set(),
                harness=mock_harness,
            )
            result = await tool.execute(
                {"name": "reviewer", "prompt": "review this code"}, ctx
            )

        assert not result.is_error
        assert "LGTM" in result.content
        assert result.metadata["subagent_name"] == "reviewer"
        assert result.metadata["turns_used"] == 3
        assert result.metadata["tool_calls"] == 2

    def test_tool_declaration(self) -> None:
        tool = SpawnSubagentTool()
        assert tool.name == "spawn_subagent"
        assert tool.declaration.risk == "safe"
        assert tool.declaration.tier_required == 0
