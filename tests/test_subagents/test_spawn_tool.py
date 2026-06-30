"""Tests for the spawn_subagent tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dream.subagents._declaration import Subagent, SubagentSet
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.spawn_subagent import (
    PARENT_PERMISSIONS_KEY,
    PARENT_SESSION_KEY,
    PARENT_TOOLS_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
    TEAM_KEY,
    SpawnSubagentTool,
)


def _make_ctx(
    subagent_set: SubagentSet | None = None,
    parent_tools: frozenset[str] | None = None,
) -> ToolExecutionContext:
    """Build a ToolExecutionContext with subagent context wired."""
    metadata: dict[str, Any] = {}
    if subagent_set is not None:
        metadata[SUBAGENT_SET_CONTEXT_KEY] = subagent_set
    metadata[PARENT_SESSION_KEY] = "test-session-id"
    metadata[PARENT_TOOLS_KEY] = parent_tools or frozenset({"read_file", "grep"})
    metadata[PARENT_PERMISSIONS_KEY] = ("read",)
    metadata[TEAM_KEY] = "test-team"
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

    async def test_valid_spawn_no_executor(self) -> None:
        """Without an engine/LLM, the tool returns a structured error."""
        tool = SpawnSubagentTool()
        ctx = _make_ctx(
            subagent_set=_simple_subagent_set(),
            parent_tools=frozenset({"read_file", "grep", "bash"}),
        )
        result = await tool.execute({"name": "reviewer", "prompt": "review this code"}, ctx)
        assert result.is_error
        assert "No engine or LLM" in result.content

    async def test_valid_spawn_with_llm(self) -> None:
        """With an LLM callable, the tool executes the subagent."""
        tool = SpawnSubagentTool()
        ctx = _make_ctx(
            subagent_set=_simple_subagent_set(),
            parent_tools=frozenset({"read_file", "grep", "bash"}),
        )

        async def mock_llm(messages: list[dict], model: str | None = None) -> str:
            return "LGTM — no issues found in this code."

        ctx.metadata["dream.llm_callable"] = mock_llm

        result = await tool.execute({"name": "reviewer", "prompt": "review this code"}, ctx)
        assert not result.is_error
        assert "LGTM" in result.content
        assert result.metadata["subagent_name"] == "reviewer"
        assert result.metadata["turns_used"] == 1

    def test_tool_declaration(self) -> None:
        tool = SpawnSubagentTool()
        assert tool.name == "spawn_subagent"
        assert tool.declaration.risk == "safe"
        assert tool.declaration.tier_required == 0
