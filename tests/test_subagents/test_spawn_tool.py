"""Tests for the spawn_subagent tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from dream.subagents._async_delegation import AsyncDelegationManager
from dream.subagents._declaration import Subagent, SubagentSet
from dream.subagents._projection import SubagentResult
from dream.tools._context import ToolExecutionContext
from dream.tools.builtin.spawn_subagent import (
    HARNESS_KEY,
    SUBAGENT_SET_CONTEXT_KEY,
    SpawnLedger,
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


def test_spawn_ledger_allows_repeatable_general_purpose_name() -> None:
    ledger = SpawnLedger()
    assert ledger.claim(()) is None
    assert ledger.claim(()) is None
    assert ledger.claim(("reviewer",)) is None
    assert ledger.claim(("reviewer",)) == "reviewer"


def _two_subagent_set() -> SubagentSet:
    return SubagentSet(
        agents={
            "reviewer": Subagent(
                name="reviewer",
                description="Reviews code",
                tools=("read_file",),
            ),
            "critic": Subagent(
                name="critic",
                description="Challenges the design",
                tools=("read_file",),
            ),
        }
    )


class TestSpawnSubagentTool:
    def test_input_requires_exactly_one_shape_and_bounded_batch(self) -> None:
        model = SpawnSubagentTool.input_model
        with pytest.raises(ValidationError):
            model.model_validate({"subagent_type": "reviewer"})
        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    "subagent_type": "reviewer",
                    "goal": "one",
                    "tasks": [{"subagent_type": "critic", "goal": "two"}],
                }
            )
        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    "tasks": [
                        {"subagent_type": f"agent-{index}", "goal": "work"} for index in range(4)
                    ]
                }
            )

    async def test_no_subagent_set_rejects_specialist(self) -> None:
        tool = SpawnSubagentTool()
        ctx = _make_ctx(subagent_set=None)
        result = await tool.execute({"name": "reviewer", "prompt": "review this"}, ctx)
        assert result.is_error
        assert "not found" in result.content
        assert "generalPurpose" in result.content

    async def test_unknown_subagent_name(self) -> None:
        tool = SpawnSubagentTool()
        ctx = _make_ctx(subagent_set=_simple_subagent_set())
        result = await tool.execute({"name": "unknown_agent", "prompt": "do stuff"}, ctx)
        assert result.is_error
        assert "not found" in result.content
        assert "reviewer" in result.content
        assert "generalPurpose" in result.content

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
            "dream.subagents._delegate.run_subagent_delegate",
            return_value=mock_result,
        ):
            ctx = _make_ctx(
                subagent_set=_simple_subagent_set(),
                harness=mock_harness,
            )
            result = await tool.execute({"name": "reviewer", "prompt": "review this code"}, ctx)

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

    async def test_same_specialist_can_spawn_only_once_per_session(self) -> None:
        tool = SpawnSubagentTool()  # the shared singleton
        mock_result = SubagentResult(name="reviewer", output="ok", success=True)

        with patch(
            "dream.subagents._delegate.run_subagent_delegate",
            return_value=mock_result,
        ):
            ctx1 = _make_ctx(subagent_set=_simple_subagent_set(), harness=AsyncMock())
            first = await tool.execute({"name": "reviewer", "prompt": "x"}, ctx1)
            assert not first.is_error
            capped = await tool.execute({"name": "reviewer", "prompt": "x"}, ctx1)
            assert capped.is_error
            assert "already spawned" in capped.content.lower()

            ctx2 = _make_ctx(subagent_set=_simple_subagent_set(), harness=AsyncMock())
            fresh = await tool.execute({"name": "reviewer", "prompt": "x"}, ctx2)
            assert not fresh.is_error, "fresh session must not inherit the prior cap"

    async def test_sync_tasks_run_concurrently_and_return_input_order(self) -> None:
        tool = SpawnSubagentTool()
        active = 0
        peak = 0

        async def run(agent: Subagent, **_: object) -> SubagentResult:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return SubagentResult(name=agent.name, output=f"{agent.name}-done")

        with patch("dream.subagents._delegate.run_subagent_delegate", side_effect=run):
            result = await tool.execute(
                {
                    "tasks": [
                        {"subagent_type": "reviewer", "goal": "review"},
                        {"subagent_type": "critic", "goal": "challenge"},
                    ]
                },
                _make_ctx(subagent_set=_two_subagent_set(), harness=AsyncMock()),
            )

        payload = json.loads(result.content)
        assert not result.is_error
        assert peak == 2
        assert [item["subagent_type"] for item in payload["results"]] == [
            "reviewer",
            "critic",
        ]

    async def test_batch_preserves_success_and_failure_results(self) -> None:
        async def run(agent: Subagent, **_: object) -> SubagentResult:
            return SubagentResult(
                name=agent.name,
                output=f"{agent.name}-done" if agent.name == "reviewer" else "",
                success=agent.name == "reviewer",
                error=None if agent.name == "reviewer" else "critic failed",
            )

        with patch("dream.subagents._delegate.run_subagent_delegate", side_effect=run):
            result = await SpawnSubagentTool().execute(
                {
                    "tasks": [
                        {"subagent_type": "reviewer", "goal": "review"},
                        {"subagent_type": "critic", "goal": "challenge"},
                    ]
                },
                _make_ctx(subagent_set=_two_subagent_set(), harness=AsyncMock()),
            )

        payload = json.loads(result.content)
        assert result.is_error
        assert [item["status"] for item in payload["results"]] == [
            "completed",
            "failed",
        ]
        assert payload["results"][0]["output"] == "reviewer-done"
        assert payload["results"][1]["error"] == "critic failed"

    async def test_background_returns_handle_then_queues_completion(self) -> None:
        tool = SpawnSubagentTool()
        manager = AsyncDelegationManager(max_active=1)
        release = asyncio.Event()

        async def run(agent: Subagent, **_: object) -> SubagentResult:
            await release.wait()
            return SubagentResult(name=agent.name, output="review complete")

        ctx = _make_ctx(subagent_set=_simple_subagent_set(), harness=AsyncMock())
        ctx.delegations = manager
        with patch("dream.subagents._delegate.run_subagent_delegate", side_effect=run):
            result = await tool.execute(
                {"subagent_type": "reviewer", "goal": "review", "background": True},
                ctx,
            )
            payload = json.loads(result.content)
            assert payload["status"] == "dispatched"
            assert manager.active("test-session") == 1
            release.set()
            completion = await manager.wait_next("test-session")

        assert completion.results[0].output == "review complete"
        await manager.close()

    async def test_background_batch_queues_one_ordered_completion(self) -> None:
        manager = AsyncDelegationManager(max_active=1)

        async def run(agent: Subagent, **_: object) -> SubagentResult:
            await asyncio.sleep(0)
            return SubagentResult(name=agent.name, output=f"{agent.name}-done")

        ctx = _make_ctx(subagent_set=_two_subagent_set(), harness=AsyncMock())
        ctx.delegations = manager
        with patch("dream.subagents._delegate.run_subagent_delegate", side_effect=run):
            result = await SpawnSubagentTool().execute(
                {
                    "background": True,
                    "tasks": [
                        {"subagent_type": "reviewer", "goal": "review"},
                        {"subagent_type": "critic", "goal": "challenge"},
                    ],
                },
                ctx,
            )
            completion = await manager.wait_next("test-session")

        assert json.loads(result.content)["count"] == 2
        assert [item.name for item in completion.results] == ["reviewer", "critic"]
        assert manager.drain("test-session") == ()
        await manager.close()

    async def test_background_capacity_forces_sync_with_note(self) -> None:
        manager = AsyncDelegationManager(max_active=1)
        release = asyncio.Event()

        async def occupied() -> tuple[SubagentResult, ...]:
            await release.wait()
            return (SubagentResult(name="occupied", output="done"),)

        assert manager.start("other-session", ("occupied",), occupied) is not None
        ctx = _make_ctx(subagent_set=_simple_subagent_set(), harness=AsyncMock())
        ctx.delegations = manager
        expected = SubagentResult(name="reviewer", output="reviewed")
        with patch("dream.subagents._delegate.run_subagent_delegate", return_value=expected):
            result = await SpawnSubagentTool().execute(
                {"subagent_type": "reviewer", "goal": "review", "background": True},
                ctx,
            )

        assert not result.is_error
        assert result.metadata["background_forced_sync"] is True
        assert "capacity unavailable" in result.content
        release.set()
        await manager.wait_next("other-session")
        await manager.close()

    async def test_background_forces_sync_when_delivery_is_unavailable(self) -> None:
        tool = SpawnSubagentTool()
        mock_result = SubagentResult(name="reviewer", output="ok", success=True)
        with patch("dream.subagents._delegate.run_subagent_delegate", return_value=mock_result):
            result = await tool.execute(
                {"subagent_type": "reviewer", "goal": "review", "background": True},
                _make_ctx(subagent_set=_simple_subagent_set(), harness=AsyncMock()),
            )
        assert not result.is_error
        assert "forced sync" in result.content.lower()
