"""Eval: VerifyOnStopHook (Hermes pre_verify / verify-on-stop)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.contracts.hook import HookEvent
from dream.hooks import HookExecutor
from dream.hooks._verify_on_stop import VerifyOnStopConfig, VerifyOnStopHook


@pytest.mark.asyncio
async def test_nudge_when_mutated_without_evidence() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is not None
    assert "verif" in result.continue_message.lower() or "test" in result.continue_message.lower()


@pytest.mark.asyncio
async def test_no_nudge_when_evidence_ran_after_mutate() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "apply_patch", "is_error": False},
    )
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "bash", "is_error": False},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_no_nudge_when_nothing_mutated() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "read_file", "is_error": False},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_terminal_stop_does_not_nudge() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    result = await hook(HookEvent.STOP, {"session_id": "s1", "phase": "terminal"})
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_session_start_resets_tracking() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    await hook(HookEvent.SESSION_START, {"session_id": "s1"})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_executor_honors_allow_continue() -> None:
    hook = VerifyOnStopHook(
        config=VerifyOnStopConfig(
            nudge_template="Run pytest before finishing.",
        )
    )
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": False},
    )
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert outcome.continue_message == "Run pytest before finishing."


@pytest.mark.asyncio
async def test_failed_mutate_does_not_count() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file", "is_error": True},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is None


@pytest.mark.asyncio
async def test_later_mutation_requires_new_evidence() -> None:
    hook = VerifyOnStopHook()
    await hook(HookEvent.POST_TOOL_USE, {"session_id": "s1", "tool_name": "write_file"})
    await hook(HookEvent.POST_TOOL_USE, {"session_id": "s1", "tool_name": "bash"})
    await hook(HookEvent.POST_TOOL_USE, {"session_id": "s1", "tool_name": "task_create"})
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s1", "phase": "pre_seal"},
    )
    assert result.continue_message is not None


@pytest.mark.asyncio
async def test_sessions_do_not_share_verify_state() -> None:
    hook = VerifyOnStopHook()
    await hook(
        HookEvent.POST_TOOL_USE,
        {"session_id": "s1", "tool_name": "write_file"},
    )
    result = await hook(
        HookEvent.STOP,
        {"session_id": "s2", "phase": "pre_seal"},
    )
    assert result.continue_message is None


def test_spec_declares_allow_continue() -> None:
    hook = VerifyOnStopHook()
    assert hook.spec.allow_continue is True
    assert HookEvent.STOP in hook.spec.events
    assert HookEvent.POST_TOOL_USE in hook.spec.events


@pytest.mark.asyncio
async def test_dispatch_post_carries_session_id_so_stop_can_nudge(tmp_path: Path) -> None:
    """Production path: POST_TOOL_USE must key verify state by session id."""
    from typing import Any

    from pydantic import BaseModel

    from dream.contracts.tool import ToolResult
    from dream.engine._tool_dispatch import EngineToolDispatcher
    from dream.tools._base import BaseTool, ToolDeclaration
    from dream.tools._context import ToolExecutionContext
    from dream.tools._registry import ToolRegistry, ToolSource

    class _WriteInput(BaseModel):
        path: str = "a.txt"
        content: str = "x"

    class _WriteStub(BaseTool):
        name = "write_file"
        description = "stub write"
        declaration = ToolDeclaration(risk="mutating", tier_required=1, timeout_seconds=5.0)
        input_model = _WriteInput

        async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(content="written")

    registry = ToolRegistry()
    registry.register(_WriteStub(), source=ToolSource.DEFAULT)
    hook = VerifyOnStopHook()
    dispatcher = EngineToolDispatcher(
        registry=registry,
        working_dir=tmp_path,
        session_id="beat-1",
        hook_executor=HookExecutor(hooks=[hook]),
    )

    _, err = await dispatcher.dispatch("write_file", {"path": "a.txt", "content": "x"})
    assert err is False

    result = await hook(
        HookEvent.STOP,
        {"session_id": "beat-1", "phase": "pre_seal", "verify_nudges": 0},
    )
    assert result.continue_message is not None
