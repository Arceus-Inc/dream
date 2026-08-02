"""Wave A — powered hooks (allow_block / allow_continue). Hermes-style opt-in veto."""

from __future__ import annotations

from typing import Any

import pytest

from dream.contracts.hook import HookEvent, HookResult, HookSpec
from dream.hooks import HookExecutor


class _Hook:
    def __init__(
        self,
        events: tuple[HookEvent, ...],
        *,
        allow_block: bool = False,
        allow_continue: bool = False,
        result: HookResult | None = None,
        priority: int = 0,
    ) -> None:
        self.spec = HookSpec(
            events=events,
            priority=priority,
            allow_block=allow_block,
            allow_continue=allow_continue,
        )
        self._result = result or HookResult()

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        return self._result


class _Emit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> None:
        self.events.append((event_type, payload))


@pytest.mark.asyncio
async def test_allow_block_honors_veto() -> None:
    """Hermes pre_tool_call block — allow_block subscriber may veto."""
    emit = _Emit()
    hook = _Hook(
        (HookEvent.PRE_TOOL_USE,),
        allow_block=True,
        result=HookResult(blocked=True, feedback="dangerous"),
    )
    outcome = await HookExecutor(hooks=[hook], emit=emit).fire(
        HookEvent.PRE_TOOL_USE, {"tool_name": "bash"}
    )
    assert outcome.blocked is True
    assert outcome.feedback == ("dangerous",)
    assert not any(t == "hook.blocked.ignored" for t, _ in emit.events)
    assert any(t == "hook.blocked" for t, _ in emit.events)


@pytest.mark.asyncio
async def test_block_without_allow_block_still_ignored() -> None:
    """Observers without allow_block cannot veto (Hermes + Dream fail-open)."""
    emit = _Emit()
    hook = _Hook(
        (HookEvent.PRE_TOOL_USE,),
        allow_block=False,
        result=HookResult(blocked=True, feedback="nope"),
    )
    outcome = await HookExecutor(hooks=[hook], emit=emit).fire(
        HookEvent.PRE_TOOL_USE, {"tool_name": "bash"}
    )
    assert outcome.blocked is False
    assert any(t == "hook.blocked.ignored" for t, _ in emit.events)


@pytest.mark.asyncio
async def test_allow_continue_collects_message() -> None:
    """STOP continue nudge — first allow_continue message wins."""
    emit = _Emit()
    later = _Hook(
        (HookEvent.STOP,),
        allow_continue=True,
        priority=1,
        result=HookResult(continue_message="second"),
    )
    first = _Hook(
        (HookEvent.STOP,),
        allow_continue=True,
        priority=10,
        result=HookResult(continue_message="spawn code_reviewer"),
    )
    outcome = await HookExecutor(hooks=[later, first], emit=emit).fire(HookEvent.STOP, {})
    assert outcome.continue_message == "spawn code_reviewer"


@pytest.mark.asyncio
async def test_continue_without_allow_continue_ignored() -> None:
    emit = _Emit()
    hook = _Hook(
        (HookEvent.STOP,),
        allow_continue=False,
        result=HookResult(continue_message="keep going"),
    )
    outcome = await HookExecutor(hooks=[hook], emit=emit).fire(HookEvent.STOP, {})
    assert outcome.continue_message is None
    assert any(t == "hook.continue.ignored" for t, _ in emit.events)


@pytest.mark.asyncio
async def test_replacement_input_first_wins() -> None:
    low = _Hook(
        (HookEvent.PRE_TOOL_USE,),
        priority=1,
        result=HookResult(replacement_input={"path": "b"}),
    )
    high = _Hook(
        (HookEvent.PRE_TOOL_USE,),
        priority=10,
        result=HookResult(replacement_input={"path": "a"}),
    )
    outcome = await HookExecutor(hooks=[low, high]).fire(HookEvent.PRE_TOOL_USE, {})
    assert outcome.replacement_input == {"path": "a"}
