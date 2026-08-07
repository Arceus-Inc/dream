"""Eval: ToolDenyListHook (Hermes pre_tool_call veto allowlist)."""

from __future__ import annotations

import pytest

from dream.contracts.hook import HookEvent
from dream.hooks import HookExecutor
from dream.hooks._tool_deny import ToolDenyListConfig, ToolDenyListHook


@pytest.mark.asyncio
async def test_blocks_denied_tool() -> None:
    hook = ToolDenyListHook(config=ToolDenyListConfig(denied=frozenset({"bash"})))
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "bash", "tool_input": {"command": "rm -rf /"}},
    )
    assert outcome.blocked is True
    assert any("bash" in f for f in outcome.feedback)


@pytest.mark.asyncio
async def test_allows_other_tools() -> None:
    hook = ToolDenyListHook(config=ToolDenyListConfig(denied=frozenset({"bash"})))
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "read_file", "tool_input": {"path": "a.py"}},
    )
    assert outcome.blocked is False


@pytest.mark.asyncio
async def test_empty_deny_list_is_noop() -> None:
    hook = ToolDenyListHook()
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "bash", "tool_input": {}},
    )
    assert outcome.blocked is False


def test_spec_requires_allow_block() -> None:
    hook = ToolDenyListHook(config=ToolDenyListConfig(denied=frozenset({"x"})))
    assert hook.spec.allow_block is True
    assert HookEvent.PRE_TOOL_USE in hook.spec.events


@pytest.mark.asyncio
async def test_malformed_feedback_template_still_blocks() -> None:
    hook = ToolDenyListHook(
        config=ToolDenyListConfig(
            denied=frozenset({"bash"}),
            feedback_template="{missing",
        )
    )
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.PRE_TOOL_USE,
        {"tool_name": "bash", "tool_input": {}},
    )
    assert outcome.blocked is True
