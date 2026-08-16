"""RoleToolResult.structured contract for Chorus typed observer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dream.events import ToolUseResult
from dream.runner.events import RoleToolResult
from dream.runner.observe import CapturingObserver
from dream.runner.role import run_role
from dream.session import SessionCost


def test_role_tool_result_structured_defaults_to_none() -> None:
    result = RoleToolResult(
        role="generator",
        tool="recall",
        is_error=False,
        content="ok",
    )
    assert result.structured is None


@pytest.mark.asyncio
async def test_run_role_observer_receives_copied_tool_structured() -> None:
    structured = {"hits": [{"run_id": "r1"}], "mode": "recency"}
    observer = CapturingObserver()

    session = MagicMock()
    session.id = "sess-test"
    session.model = "test-model"
    session.cost = SessionCost()

    async def fake_send(_intent: str):
        yield ToolUseResult(
            tool_use_id="tool-1",
            name="recall",
            content="found 1 hit",
            is_error=False,
            structured=structured,
        )

    session.send = fake_send
    session.close = AsyncMock()

    harness = MagicMock()
    harness.start_session = AsyncMock(return_value=session)
    harness.save_session = AsyncMock(return_value=None)

    await run_role(harness, "planner", "find runs", observer=observer)

    result = next(event for event in observer.events if isinstance(event, RoleToolResult))
    assert result.tool == "recall"
    assert result.structured == structured
    assert result.structured is not structured


@pytest.mark.asyncio
async def test_run_role_observer_tool_structured_defaults_to_none() -> None:
    observer = CapturingObserver()

    session = MagicMock()
    session.id = "sess-test"
    session.model = "test-model"
    session.cost = SessionCost()

    async def fake_send(_intent: str):
        yield ToolUseResult(
            tool_use_id="tool-1",
            name="echo",
            content="ok",
            is_error=False,
        )

    session.send = fake_send
    session.close = AsyncMock()

    harness = MagicMock()
    harness.start_session = AsyncMock(return_value=session)
    harness.save_session = AsyncMock(return_value=None)

    await run_role(harness, "planner", "ping", observer=observer)

    result = next(event for event in observer.events if isinstance(event, RoleToolResult))
    assert result.structured is None
