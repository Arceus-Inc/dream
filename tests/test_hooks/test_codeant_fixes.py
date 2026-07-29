"""Regression tests for CodeAnt AI review findings on PR #83."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pydantic import BaseModel

from dream.contracts.tool import ToolResult
from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.engine._messages import ConversationMessage, TextBlock
from dream.engine._session import _select_turn_driver
from dream.engine._tool_dispatch import EngineToolDispatcher
from dream.hooks import HookExecutor
from dream.subagents._delegate import _safe_spill_basename, _write_spill_file, apply_summary_budget
from dream.tools._base import BaseTool, ToolDeclaration
from dream.tools._context import ToolExecutionContext
from dream.tools._registry import ToolRegistry, ToolSource
from dream.tools.builtin.spawn_subagent import resolve_spawn_goal


class _Hook:
    def __init__(
        self,
        events: tuple[HookEvent, ...],
        *,
        allow_block: bool = False,
        result: HookResult | None = None,
        on_call: Any = None,
    ) -> None:
        self.spec = HookSpec(events=events, allow_block=allow_block)
        self._result = result or HookResult()
        self._on_call = on_call
        self.calls: list[tuple[HookEvent, dict[str, Any]]] = []

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        self.calls.append((event, dict(payload)))
        if self._on_call is not None:
            return self._on_call(event, payload)
        return self._result


class _SpawnEchoInput(BaseModel):
    subagent_type: str | None = None
    name: str | None = None
    goal: str | None = None


class _SpawnEchoTool(BaseTool):
    name = "spawn_subagent"
    description = "test stub"
    declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=5.0)
    input_model = _SpawnEchoInput

    async def execute(self, input: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        label = input.get("subagent_type") or input.get("name") or ""
        return ToolResult(content=f"spawned:{label}")


def _registry(*tools: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool, source=ToolSource.DEFAULT)
    return reg


@pytest.mark.asyncio
async def test_empty_replacement_input_is_ignored() -> None:
    hook = _Hook(
        (HookEvent.PRE_TOOL_USE,),
        result=HookResult(replacement_input={}),
    )
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.PRE_TOOL_USE, {"tool_name": "write_file"}
    )
    assert outcome.replacement_input is None


def test_resolve_spawn_goal_falls_back_from_whitespace_goal() -> None:
    assert resolve_spawn_goal("   ", "legacy prompt") == "legacy prompt"
    assert resolve_spawn_goal(" real goal ", "legacy") == "real goal"


def test_apply_summary_budget_rejects_zero_max_chars() -> None:
    long = "A" * 30_000
    out = apply_summary_budget(long, max_chars=0)
    assert len(out) <= 24_000
    assert "truncated" in out


def test_spill_basename_strips_path_traversal(tmp_path: Path) -> None:
    assert _safe_spill_basename("../../etc/passwd") == "_etc_passwd"
    spill_dir = tmp_path / "delegation"
    path = _write_spill_file(spill_dir, "../../../evil", "full text")
    assert path.is_file()
    assert path.resolve().is_relative_to(spill_dir.resolve())


@pytest.mark.asyncio
async def test_stop_nudge_appended_once_via_turn_driver() -> None:
    transcript: list[ConversationMessage] = []
    user_messages = [
        ConversationMessage(role="user", content=[TextBlock(text="first")]),
        ConversationMessage(role="user", content=[TextBlock(text="nudge")]),
    ]
    decision, idx = await _select_turn_driver(
        transcript,
        user_messages,
        user_idx=0,
        turn_number=1,
        reviewer=None,
        review_rounds=0,
    )
    assert decision.action == "drive"
    assert len(transcript) == 1
    assert transcript[0].text == "first"

    decision2, idx2 = await _select_turn_driver(
        transcript,
        user_messages,
        user_idx=idx,
        turn_number=2,
        reviewer=None,
        review_rounds=0,
    )
    assert decision2.action == "drive"
    assert len(transcript) == 2
    assert transcript[1].text == "nudge"

    decision3, _ = await _select_turn_driver(
        transcript,
        user_messages,
        user_idx=idx2,
        turn_number=3,
        reviewer=None,
        review_rounds=0,
    )
    assert decision3.action == "seal"
    assert len(transcript) == 2


@pytest.mark.asyncio
async def test_subagent_start_uses_post_pre_replacement_label(tmp_path: Path) -> None:
    events: list[tuple[HookEvent, dict[str, Any]]] = []

    def on_pre(event: HookEvent, payload: dict[str, Any]) -> HookResult:
        if event == HookEvent.PRE_TOOL_USE:
            return HookResult(
                replacement_input={
                    **payload["tool_input"],
                    "subagent_type": "reviewer",
                    "name": "ignored_alias",
                }
            )
        return HookResult()

    recorder = _Hook(
        (
            HookEvent.PRE_TOOL_USE,
            HookEvent.SUBAGENT_START,
            HookEvent.SUBAGENT_STOP,
        ),
        on_call=on_pre,
    )
    executor = HookExecutor(hooks=[recorder])
    disp = EngineToolDispatcher(
        registry=_registry(_SpawnEchoTool()),
        working_dir=tmp_path,
        session_id="s",
        hook_executor=executor,
    )

    content, is_error = await disp.dispatch(
        "spawn_subagent",
        {"subagent_type": "generalPurpose", "goal": "do work"},
    )
    assert not is_error
    assert content == "spawned:reviewer"

    start_calls = [p for ev, p in recorder.calls if ev == HookEvent.SUBAGENT_START]
    stop_calls = [p for ev, p in recorder.calls if ev == HookEvent.SUBAGENT_STOP]
    assert start_calls[0]["subagent_name"] == "reviewer"
    assert stop_calls[0]["subagent_name"] == "reviewer"


@pytest.mark.asyncio
async def test_user_prompt_submit_inject_context() -> None:
    hook = _Hook(
        (HookEvent.USER_PROMPT_SUBMIT,),
        result=HookResult(inject_context="extra policy"),
    )
    msg = ConversationMessage(role="user", content=[TextBlock(text="hello")])
    outcome = await HookExecutor(hooks=[hook]).fire(
        HookEvent.USER_PROMPT_SUBMIT, {"prompt": msg.text}
    )
    assert outcome.inject_context == "extra policy"
    msg.content = [*msg.content, TextBlock(text="\n\n" + outcome.inject_context)]
    assert "hello" in msg.text
    assert "extra policy" in msg.text
