"""Wake scheduler loop (spec 15 P1 §2).

Drives ``wake.run_wake_cycle`` from an idle timer — today nothing fires
wake except a REPL command. ``run`` decisions are surfaced as a
``runtime.wake.run`` event plus an optional async handler so the policy
of *what to do with the tasks* stays outside the SDK (Model A).
"""

from __future__ import annotations

from typing import Any

import pytest

from dream.runtime._wake_scheduler import wake_scheduler_loop
from dream.wake import HeartbeatConfig, HeartbeatDecision, IdleTimerWake, WakeOutcome
from dream.wake._source import WakeSource


def _decision(action: str, *, tasks: tuple[str, ...] = ()) -> HeartbeatDecision:
    from datetime import UTC, datetime

    return HeartbeatDecision(
        decided_at=datetime.now(UTC),
        action=action,  # type: ignore[arg-type]
        tasks=tasks,
        reason="test",
        wake_source=IdleTimerWake(idle_minutes=1),
        forced=False,
        outcome="decided",
    )


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return payload


class _StopLoop(Exception):
    """Raised by the fake sleep to end the otherwise-infinite loop."""


def _sleeper(max_ticks: int) -> Any:
    ticks = 0

    async def sleep(seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks > max_ticks:
            raise _StopLoop

    return sleep


@pytest.mark.asyncio
async def test_fires_cycle_after_idle_and_invokes_run_handler(tmp_path: Any) -> None:
    seen_sources: list[WakeSource] = []
    handled: list[HeartbeatDecision] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        seen_sources.append(kwargs["wake_source"])
        return WakeOutcome(decision=_decision("run", tasks=("fix CI",)))

    async def on_run(decision: HeartbeatDecision) -> None:
        handled.append(decision)

    emit = _Recorder()
    with pytest.raises(_StopLoop):
        await wake_scheduler_loop(
            streamer_factory=lambda: object(),
            agent_id="default",
            coordination_dir=tmp_path,
            idle_minutes=7,
            heartbeat_config=HeartbeatConfig(),
            emit=emit,
            on_run=on_run,
            sleep=_sleeper(max_ticks=1),
            run_cycle=fake_cycle,
        )
    assert len(seen_sources) == 1
    assert isinstance(seen_sources[0], IdleTimerWake)
    assert seen_sources[0].idle_minutes == 7
    assert [d.tasks for d in handled] == [("fix CI",)]
    run_events = [p for t, p in emit.events if t == "runtime.wake.run"]
    assert run_events and run_events[0]["tasks"] == ["fix CI"]


@pytest.mark.asyncio
async def test_skip_decision_does_not_invoke_handler(tmp_path: Any) -> None:
    handled: list[HeartbeatDecision] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        return WakeOutcome(decision=_decision("skip"))

    async def on_run(decision: HeartbeatDecision) -> None:
        handled.append(decision)

    emit = _Recorder()
    with pytest.raises(_StopLoop):
        await wake_scheduler_loop(
            streamer_factory=lambda: object(),
            agent_id="default",
            coordination_dir=tmp_path,
            idle_minutes=1,
            heartbeat_config=HeartbeatConfig(),
            emit=emit,
            on_run=on_run,
            sleep=_sleeper(max_ticks=2),
            run_cycle=fake_cycle,
        )
    assert handled == []
    assert not any(t == "runtime.wake.run" for t, _ in emit.events)


@pytest.mark.asyncio
async def test_dropped_cycle_is_tolerated(tmp_path: Any) -> None:
    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        return WakeOutcome(decision=None, dropped_reason="heartbeat_in_flight")

    emit = _Recorder()
    with pytest.raises(_StopLoop):
        await wake_scheduler_loop(
            streamer_factory=lambda: object(),
            agent_id="default",
            coordination_dir=tmp_path,
            idle_minutes=1,
            heartbeat_config=HeartbeatConfig(),
            emit=emit,
            on_run=None,
            sleep=_sleeper(max_ticks=2),
            run_cycle=fake_cycle,
        )
    # No run event, loop kept ticking (2 sleeps before stop).
    assert not any(t == "runtime.wake.run" for t, _ in emit.events)


@pytest.mark.asyncio
async def test_wake_events_forwarded_to_emit(tmp_path: Any) -> None:
    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        kwargs["on_event"]("heartbeat.decision.run", {"agent_id": "default"})
        return WakeOutcome(decision=_decision("run"))

    emit = _Recorder()
    with pytest.raises(_StopLoop):
        await wake_scheduler_loop(
            streamer_factory=lambda: object(),
            agent_id="default",
            coordination_dir=tmp_path,
            idle_minutes=1,
            heartbeat_config=HeartbeatConfig(),
            emit=emit,
            on_run=None,
            sleep=_sleeper(max_ticks=1),
            run_cycle=fake_cycle,
        )
    assert ("heartbeat.decision.run", {"agent_id": "default"}) in emit.events
