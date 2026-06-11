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
async def test_prompt_override_forwarded_to_cycle(tmp_path: Any) -> None:
    # A consumer agent (e.g. a persona daemon) ships its own heartbeat
    # prompt; the scheduler must hand the override path to every cycle.
    seen_paths: list[Any] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        seen_paths.append(kwargs.get("prompt_override_path"))
        return WakeOutcome(decision=_decision("skip"))

    emit = _Recorder()
    override = tmp_path / "heartbeat.md"
    with pytest.raises(_StopLoop):
        await wake_scheduler_loop(
            streamer_factory=lambda: object(),
            agent_id="default",
            coordination_dir=tmp_path,
            idle_minutes=1,
            heartbeat_config=HeartbeatConfig(),
            emit=emit,
            on_run=None,
            prompt_override_path=override,
            sleep=_sleeper(max_ticks=1),
            run_cycle=fake_cycle,
        )
    assert seen_paths == [override]


@pytest.mark.asyncio
async def test_empty_checklist_skips_model_entirely(tmp_path: Any) -> None:
    # The zero-cost skip: an empty (or whitespace-only) heartbeat checklist
    # means there is nothing to decide about — the scheduler must not spend
    # a model call discovering that. OpenClaw analog: reason=empty-heartbeat-file.
    cycles: list[Any] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        cycles.append(kwargs)
        return WakeOutcome(decision=_decision("run"))

    override = tmp_path / "heartbeat.md"
    override.write_text("   \n\n  ", encoding="utf-8")
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
            prompt_override_path=override,
            sleep=_sleeper(max_ticks=2),
            run_cycle=fake_cycle,
        )
    assert cycles == []  # no model turn happened
    skipped = [p for t, p in emit.events if t == "wake.skipped"]
    assert len(skipped) == 2  # one per tick, zero-cost
    assert skipped[0]["reason"] == "empty-checklist"


@pytest.mark.asyncio
async def test_missing_checklist_file_still_wakes(tmp_path: Any) -> None:
    # A *missing* override file falls back to the bundled prompt (the agent
    # has no operator checklist but still has its default mission) — only an
    # EXISTING-but-empty file is the explicit "nothing to check" signal.
    cycles: list[Any] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        cycles.append(kwargs)
        return WakeOutcome(decision=_decision("skip"))

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
            prompt_override_path=tmp_path / "missing.md",
            sleep=_sleeper(max_ticks=1),
            run_cycle=fake_cycle,
        )
    assert len(cycles) == 1


@pytest.mark.asyncio
async def test_pending_notes_wake_with_cron_source_and_context(tmp_path: Any) -> None:
    # The timed-note pattern's read side: notes queued by cron firings are
    # drained into the next wake — the source becomes CronWake and the note
    # texts ride into the cycle as extra context.
    from dream.runtime._wake_notes import WakeNoteStore
    from dream.wake import CronWake

    notes = WakeNoteStore(tmp_path / "notes")
    notes.add("review the inbox backlog", source="nudge")
    notes.add("weekly report due", source="weekly-report")
    cycles: list[dict[str, Any]] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        cycles.append(kwargs)
        return WakeOutcome(decision=_decision("skip"))

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
            notes=notes,
            sleep=_sleeper(max_ticks=2),
            run_cycle=fake_cycle,
        )
    assert len(cycles) == 2
    first = cycles[0]
    assert isinstance(first["wake_source"], CronWake)
    assert first["wake_source"].cron_kind == "nudge"
    context = first["extra_context"]
    assert "review the inbox backlog" in context
    assert "weekly report due" in context
    # Second tick: notes were consumed — back to the idle timer source.
    assert isinstance(cycles[1]["wake_source"], IdleTimerWake)
    assert cycles[1]["extra_context"] is None


@pytest.mark.asyncio
async def test_pending_notes_override_empty_checklist_skip(tmp_path: Any) -> None:
    # An empty checklist normally means zero-cost skip — but queued notes
    # ARE content; the wake must fire to deliver them.
    from dream.runtime._wake_notes import WakeNoteStore

    notes = WakeNoteStore(tmp_path / "notes")
    notes.add("the digest is due", source="rolling-digest")
    override = tmp_path / "heartbeat.md"
    override.write_text("", encoding="utf-8")
    cycles: list[dict[str, Any]] = []

    async def fake_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
        cycles.append(kwargs)
        return WakeOutcome(decision=_decision("skip"))

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
            prompt_override_path=override,
            notes=notes,
            sleep=_sleeper(max_ticks=2),
            run_cycle=fake_cycle,
        )
    assert len(cycles) == 1  # note tick fired; the empty tick after skipped
    skipped = [p for t, p in emit.events if t == "wake.skipped"]
    assert len(skipped) == 1


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
