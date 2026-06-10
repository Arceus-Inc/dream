"""Hook executor (spec 13 §extension surface; spec 15 P4 §2).

Hooks are fire-and-forget observers — they NEVER veto (divergence #1
from OpenHarness: the blocked return path is stripped and warned about).
Each handler runs under a wall-clock deadline; a crash or overrun is
logged as an event and the loop moves on.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dream.contracts.hook import Hook, HookEvent, HookResult, HookSpec
from dream.hooks import HookExecutor


class _RecordingHook:
    def __init__(
        self,
        events: tuple[HookEvent, ...],
        *,
        priority: int = 0,
        result: HookResult | None = None,
    ) -> None:
        self.spec = HookSpec(events=events, priority=priority)
        self.seen: list[tuple[HookEvent, dict[str, Any]]] = []
        self._result = result or HookResult()

    async def __call__(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        self.seen.append((event, payload))
        return self._result


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return payload


@pytest.mark.asyncio
async def test_fires_only_subscribed_hooks() -> None:
    pre = _RecordingHook((HookEvent.PRE_TOOL_USE,))
    stop = _RecordingHook((HookEvent.STOP,))
    executor = HookExecutor(hooks=[pre, stop])
    await executor.fire(HookEvent.PRE_TOOL_USE, {"tool": "bash"})
    assert len(pre.seen) == 1
    assert stop.seen == []


@pytest.mark.asyncio
async def test_priority_orders_execution() -> None:
    order: list[str] = []

    class _Ordered:
        def __init__(self, name: str, priority: int) -> None:
            self.spec = HookSpec(events=(HookEvent.STOP,), priority=priority)
            self._name = name

        async def __call__(
            self, event: HookEvent, payload: dict[str, Any]
        ) -> HookResult:
            order.append(self._name)
            return HookResult()

    executor = HookExecutor(hooks=[_Ordered("low", 1), _Ordered("high", 10)])
    await executor.fire(HookEvent.STOP, {})
    assert order == ["high", "low"]


@pytest.mark.asyncio
async def test_crash_is_isolated_and_reported() -> None:
    class _Broken:
        spec = HookSpec(events=(HookEvent.STOP,))

        async def __call__(
            self, event: HookEvent, payload: dict[str, Any]
        ) -> HookResult:
            raise RuntimeError("hook bug")

    after = _RecordingHook((HookEvent.STOP,), priority=-1)
    emit = _Recorder()
    executor = HookExecutor(hooks=[_Broken(), after], emit=emit)
    outcome = await executor.fire(HookEvent.STOP, {})
    assert len(after.seen) == 1  # the next hook still ran
    assert any(t == "hook.handler.error" for t, _ in emit.events)
    assert outcome.errors == 1


@pytest.mark.asyncio
async def test_deadline_overrun_is_reported_not_retried() -> None:
    calls = 0

    class _Slow:
        spec = HookSpec(events=(HookEvent.STOP,))

        async def __call__(
            self, event: HookEvent, payload: dict[str, Any]
        ) -> HookResult:
            nonlocal calls
            calls += 1
            await asyncio.sleep(60)
            return HookResult()

    emit = _Recorder()
    executor = HookExecutor(hooks=[_Slow()], emit=emit, deadline_seconds=0.05)
    outcome = await executor.fire(HookEvent.STOP, {})
    assert calls == 1
    assert any(t == "hook.handler.timeout" for t, _ in emit.events)
    assert outcome.timeouts == 1


@pytest.mark.asyncio
async def test_blocked_result_is_stripped_and_warned() -> None:
    # Divergence #1: hooks never veto, even when they ask to.
    blocker = _RecordingHook(
        (HookEvent.PRE_TOOL_USE,), result=HookResult(blocked=True, feedback="no!")
    )
    emit = _Recorder()
    executor = HookExecutor(hooks=[blocker], emit=emit)
    outcome = await executor.fire(HookEvent.PRE_TOOL_USE, {"tool": "bash"})
    assert not outcome.blocked
    assert any(t == "hook.blocked.ignored" for t, _ in emit.events)


@pytest.mark.asyncio
async def test_feedback_collected() -> None:
    one = _RecordingHook((HookEvent.STOP,), result=HookResult(feedback="note a"))
    two = _RecordingHook((HookEvent.STOP,), result=HookResult(feedback="note b"))
    executor = HookExecutor(hooks=[one, two])
    outcome = await executor.fire(HookEvent.STOP, {})
    assert outcome.feedback == ("note a", "note b")


@pytest.mark.asyncio
async def test_register_after_construction() -> None:
    executor = HookExecutor()
    hook = _RecordingHook((HookEvent.SESSION_START,))
    executor.register(hook)
    await executor.fire(HookEvent.SESSION_START, {})
    assert len(hook.seen) == 1


def test_protocol_conformance() -> None:
    assert isinstance(_RecordingHook((HookEvent.STOP,)), Hook)
