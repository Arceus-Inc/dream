"""TDD: Cursor-style subagent_type enum + generalPurpose (lean W1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dream.subagents._declaration import Subagent, SubagentSet
from dream.subagents._projection import SubagentResult
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.spawn_subagent import (
    GENERAL_PURPOSE,
    HARNESS_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
    SpawnSubagentTool,
    build_spawn_parameters,
    spawn_type_names,
)


def _ctx(subagent_set: SubagentSet | None, harness: Any = None) -> ToolExecutionContext:
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


def _set() -> SubagentSet:
    return SubagentSet(
        agents={
            "reviewer": Subagent(
                name="reviewer",
                description="Reviews code",
                tools=("read_file", "grep"),
            ),
        }
    )


def test_spawn_type_names_includes_general_purpose_first() -> None:
    assert spawn_type_names(_set()) == [GENERAL_PURPOSE, "reviewer"]
    assert spawn_type_names(SubagentSet()) == [GENERAL_PURPOSE]
    assert spawn_type_names(None) == [GENERAL_PURPOSE]


def test_build_spawn_parameters_sets_enum() -> None:
    base = SpawnSubagentTool().input_schema()
    patched = build_spawn_parameters(base, _set())
    prop = patched["properties"]["subagent_type"]
    assert prop["enum"] == [GENERAL_PURPOSE, "reviewer"]
    assert "generalPurpose" in patched.get("description", prop.get("description", "")) or (
        "generalPurpose" in prop["description"]
    )


async def test_unknown_type_fails_with_available_enum() -> None:
    tool = SpawnSubagentTool()
    result = await tool.execute(
        {"subagent_type": "test_writer", "goal": "write tests"},
        _ctx(_set(), harness=AsyncMock()),
    )
    assert result.is_error
    assert result.metadata["root_cause"].startswith("unknown_subagent")
    assert GENERAL_PURPOSE in result.content
    assert "reviewer" in result.content
    assert "safe_retry" in result.metadata
    assert "stop_condition" in result.metadata


async def test_name_alias_still_works() -> None:
    tool = SpawnSubagentTool()
    mock_result = SubagentResult(name="reviewer", output="ok", success=True)
    with patch(
        "dream.subagents._delegate.run_subagent_inline",
        return_value=mock_result,
    ):
        result = await tool.execute(
            {"name": "reviewer", "prompt": "review"},
            _ctx(_set(), harness=AsyncMock()),
        )
    assert not result.is_error
    assert result.metadata["subagent_name"] == "reviewer"


async def test_general_purpose_uses_delegate_path() -> None:
    tool = SpawnSubagentTool()
    mock_result = SubagentResult(name=GENERAL_PURPOSE, output="summary", success=True)
    with patch(
        "dream.subagents._delegate.run_subagent_delegate",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_delegate:
        result = await tool.execute(
            {
                "subagent_type": GENERAL_PURPOSE,
                "goal": "Summarize README",
                "context": "path=README.md",
            },
            _ctx(_set(), harness=AsyncMock()),
        )
    assert not result.is_error
    assert "summary" in result.content
    assert result.metadata["mode"] == "delegate"
    assert result.metadata["subagent_name"] == GENERAL_PURPOSE
    mock_delegate.assert_awaited_once()
    agent = mock_delegate.await_args.args[0]
    call_kwargs = mock_delegate.await_args.kwargs
    assert call_kwargs["goal"] == "Summarize README"
    assert call_kwargs["context"] == "path=README.md"
    assert "spawn_subagent" not in agent.tools


async def test_general_purpose_works_with_empty_set() -> None:
    tool = SpawnSubagentTool()
    mock_result = SubagentResult(name=GENERAL_PURPOSE, output="done", success=True)
    with patch(
        "dream.subagents._delegate.run_subagent_delegate",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        result = await tool.execute(
            {"subagent_type": GENERAL_PURPOSE, "goal": "scout the tree"},
            _ctx(SubagentSet(), harness=AsyncMock()),
        )
    assert not result.is_error
