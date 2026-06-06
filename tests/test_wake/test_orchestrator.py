"""Spec 06.5 slice 2 — ``run_wake_cycle`` orchestrator.

The orchestrator ties the slice 2 pieces together:

- acquires the per-agent heartbeat lock (so two concurrent wakes don't
  trample the skip counter),
- reads the persistent ``HeartbeatState``,
- decides whether the next turn should be **forced** based on
  ``skip_streak >= max_consecutive_skips``,
- drives a single background turn via slice 1's ``run_background_turn``,
- updates the persistent state (skip +1 on honest skip, reset on run,
  no change on missing),
- emits typed events (``heartbeat.decision.run``,
  ``heartbeat.decision.skip``, ``heartbeat.decision.forced``,
  ``heartbeat.missing``, ``wake.dropped``) through an optional sink,
- and returns a ``WakeOutcome`` so the caller can branch on it.

The orchestrator stops at the decision — spawning the downstream work
session is the caller's job (slice 3 / Spec 07).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dream.engine._cost import UsageSnapshot
from dream.engine._events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
)
from dream.engine._messages import (
    ContentBlock,
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from dream.utils.file_lock import exclusive_file_lock
from dream.wake import (
    HeartbeatConfig,
    WakeOutcome,
    run_wake_cycle,
)
from dream.wake._source import (
    CronWake,
    IdleTimerWake,
    ManualWake,
)
from dream.wake._state import (
    HeartbeatState,
    read_state,
    state_path_for,
)

# --- shared scripted streamer ---------------------------------------------


@dataclass
class _ScriptedTurn:
    text_chunks: list[str] = field(default_factory=list)
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)


class _ScriptedStreamer:
    def __init__(self, turn: _ScriptedTurn) -> None:
        self._turn = turn
        self.calls: list[list[ConversationMessage]] = []

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        for chunk in self._turn.text_chunks:
            yield AssistantTextDelta(text=chunk)
        blocks: list[ContentBlock] = []
        joined = "".join(self._turn.text_chunks)
        if joined:
            blocks.append(TextBlock(text=joined))
        blocks.extend(self._turn.tool_uses)
        yield AssistantTurnComplete(blocks=blocks, usage=self._turn.usage)


def _run_streamer() -> _ScriptedStreamer:
    return _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={
                        "action": "run",
                        "tasks": ["a", "b"],
                        "reason": "queue ready",
                    },
                )
            ]
        )
    )


def _skip_streamer() -> _ScriptedStreamer:
    return _ScriptedStreamer(
        _ScriptedTurn(
            tool_uses=[
                ToolUseBlock(
                    id="tu_1",
                    name="heartbeat",
                    input={"action": "skip", "reason": "nothing pending"},
                )
            ]
        )
    )


def _missing_streamer() -> _ScriptedStreamer:
    return _ScriptedStreamer(_ScriptedTurn(text_chunks=["mm"]))


class _EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


def _now() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


# --- happy path: skip ------------------------------------------------------


async def test_skip_increments_streak_and_emits_decision_skip(tmp_path: Path) -> None:
    rec = _EventRecorder()
    outcome = await run_wake_cycle(
        _skip_streamer(),
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden"),
        coordination_dir=tmp_path,
        on_event=rec,
        now=_now,
    )
    assert isinstance(outcome, WakeOutcome)
    assert outcome.decision is not None
    assert outcome.decision.action == "skip"
    assert outcome.dropped_reason is None
    # State bumped, persisted, last_decided_at recorded.
    state = read_state(state_path_for(tmp_path, agent_id="curator"))
    assert state.skip_streak == 1
    assert state.last_decided_at == _now()
    # One decision event fired.
    assert "heartbeat.decision.skip" in rec.types()


async def test_consecutive_skips_keep_incrementing(tmp_path: Path) -> None:
    for expected in (1, 2, 3):
        await run_wake_cycle(
            _skip_streamer(),
            agent_id="curator",
            wake_source=CronWake(cron_kind="doc-garden"),
            coordination_dir=tmp_path,
            now=_now,
        )
        assert (
            read_state(state_path_for(tmp_path, agent_id="curator")).skip_streak
            == expected
        )


# --- happy path: run -------------------------------------------------------


async def test_run_resets_streak_and_emits_decision_run(tmp_path: Path) -> None:
    # Seed a non-zero streak so we can prove "reset to 0".
    from dream.wake._state import write_state

    write_state(state_path_for(tmp_path, agent_id="curator"), HeartbeatState(skip_streak=3))

    rec = _EventRecorder()
    outcome = await run_wake_cycle(
        _run_streamer(),
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden"),
        coordination_dir=tmp_path,
        on_event=rec,
        now=_now,
    )
    assert outcome.decision is not None
    assert outcome.decision.action == "run"
    state = read_state(state_path_for(tmp_path, agent_id="curator"))
    assert state.skip_streak == 0
    assert "heartbeat.decision.run" in rec.types()


# --- missing decision: streak unchanged ------------------------------------


async def test_missing_decision_does_not_advance_streak(tmp_path: Path) -> None:
    """Spec criterion 16: ``heartbeat_missing_decision`` MUST NOT change
    ``skip_streak`` — otherwise a flaky model could falsely trigger anti-coma."""
    from dream.wake._state import write_state

    write_state(state_path_for(tmp_path, agent_id="curator"), HeartbeatState(skip_streak=2))

    rec = _EventRecorder()
    outcome = await run_wake_cycle(
        _missing_streamer(),
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden"),
        coordination_dir=tmp_path,
        on_event=rec,
        now=_now,
    )
    assert outcome.decision is not None
    assert outcome.decision.outcome == "missing"
    # Streak unchanged.
    assert read_state(state_path_for(tmp_path, agent_id="curator")).skip_streak == 2
    # Different event name for missing.
    assert "heartbeat.missing" in rec.types()
    # And NO decision.{skip,run} event for a missing.
    assert "heartbeat.decision.skip" not in rec.types()
    assert "heartbeat.decision.run" not in rec.types()


# --- forced mode -----------------------------------------------------------


async def test_forced_mode_kicks_in_at_threshold(tmp_path: Path) -> None:
    """Spec criterion 12: when ``skip_streak >= max_consecutive_skips``, the
    next background turn is constructed in **forced** mode."""
    from dream.wake._state import write_state

    config = HeartbeatConfig(max_consecutive_skips=3)
    write_state(state_path_for(tmp_path, agent_id="curator"), HeartbeatState(skip_streak=3))

    rec = _EventRecorder()
    outcome = await run_wake_cycle(
        _missing_streamer(),  # forced + silent => synthesised run
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden"),
        coordination_dir=tmp_path,
        config=config,
        on_event=rec,
        now=_now,
    )
    assert outcome.decision is not None
    assert outcome.decision.forced is True
    assert outcome.decision.action == "run"
    # Streak reset because a run was made.
    assert read_state(state_path_for(tmp_path, agent_id="curator")).skip_streak == 0
    assert "heartbeat.decision.forced" in rec.types()


async def test_below_threshold_uses_non_forced_mode(tmp_path: Path) -> None:
    from dream.wake._state import write_state

    config = HeartbeatConfig(max_consecutive_skips=5)
    write_state(state_path_for(tmp_path, agent_id="curator"), HeartbeatState(skip_streak=4))

    outcome = await run_wake_cycle(
        _skip_streamer(),
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden"),
        coordination_dir=tmp_path,
        config=config,
        now=_now,
    )
    assert outcome.decision is not None
    assert outcome.decision.forced is False
    # Below threshold means a skip is honoured.
    assert outcome.decision.action == "skip"
    assert read_state(state_path_for(tmp_path, agent_id="curator")).skip_streak == 5


async def test_forced_voluntary_run_still_marked_forced(tmp_path: Path) -> None:
    """When forced + the model genuinely calls run, the record still
    reflects that the *gate* was forced (audit trail)."""
    from dream.wake._state import write_state

    config = HeartbeatConfig(max_consecutive_skips=2)
    write_state(state_path_for(tmp_path, agent_id="curator"), HeartbeatState(skip_streak=2))

    rec = _EventRecorder()
    outcome = await run_wake_cycle(
        _run_streamer(),
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden"),
        coordination_dir=tmp_path,
        config=config,
        on_event=rec,
        now=_now,
    )
    assert outcome.decision is not None
    assert outcome.decision.action == "run"
    assert outcome.decision.forced is True
    # Forced event takes precedence over plain decision.run.
    assert "heartbeat.decision.forced" in rec.types()


# --- concurrency: per-agent lock dedup -------------------------------------


async def test_concurrent_wake_for_same_agent_is_dropped(tmp_path: Path) -> None:
    """Spec criterion 21: a wake firing while another holds the lock is
    recorded as ``wake.dropped(reason=heartbeat_in_flight)`` and exits."""
    coord = tmp_path
    lock_path = coord / "heartbeat-curator.lock"
    holder_acquired = threading.Event()
    holder_release = threading.Event()

    def hold_lock() -> None:
        with exclusive_file_lock(lock_path):
            holder_acquired.set()
            holder_release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    assert holder_acquired.wait(timeout=5)
    try:
        rec = _EventRecorder()
        outcome = await run_wake_cycle(
            _skip_streamer(),
            agent_id="curator",
            wake_source=CronWake(cron_kind="doc-garden"),
            coordination_dir=coord,
            on_event=rec,
            now=_now,
        )
        assert outcome.decision is None
        assert outcome.dropped_reason == "heartbeat_in_flight"
        assert "wake.dropped" in rec.types()
    finally:
        holder_release.set()
        thread.join(timeout=5)


async def test_concurrent_wake_does_not_change_state(tmp_path: Path) -> None:
    """A dropped wake doesn't touch the skip counter — there was no decision."""
    coord = tmp_path
    from dream.wake._state import write_state

    write_state(state_path_for(coord, agent_id="curator"), HeartbeatState(skip_streak=2))

    lock_path = coord / "heartbeat-curator.lock"
    holder_acquired = threading.Event()
    holder_release = threading.Event()

    def hold_lock() -> None:
        with exclusive_file_lock(lock_path):
            holder_acquired.set()
            holder_release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    assert holder_acquired.wait(timeout=5)
    try:
        await run_wake_cycle(
            _skip_streamer(),
            agent_id="curator",
            wake_source=CronWake(cron_kind="doc-garden"),
            coordination_dir=coord,
            now=_now,
        )
        # Streak preserved.
        assert (
            read_state(state_path_for(coord, agent_id="curator")).skip_streak == 2
        )
    finally:
        holder_release.set()
        thread.join(timeout=5)


async def test_lock_released_after_decision(tmp_path: Path) -> None:
    """Spec criterion 22: the lock guards the *decision*, not the work.
    After ``run_wake_cycle`` returns, a fresh acquire must succeed
    immediately."""
    await run_wake_cycle(
        _run_streamer(),
        agent_id="curator",
        wake_source=ManualWake(),
        coordination_dir=tmp_path,
        now=_now,
    )
    # If the orchestrator hadn't released the lock, this would block forever.
    # Bound it to a short window via a thread so the test can't hang.
    lock_path = tmp_path / "heartbeat-curator.lock"
    acquired = threading.Event()

    def try_acquire() -> None:
        with exclusive_file_lock(lock_path):
            acquired.set()

    thread = threading.Thread(target=try_acquire, daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert acquired.is_set(), "lock not released by orchestrator"


# --- different agents independent ------------------------------------------


async def test_two_agents_have_independent_state_and_locks(tmp_path: Path) -> None:
    await run_wake_cycle(
        _skip_streamer(),
        agent_id="curator",
        wake_source=ManualWake(),
        coordination_dir=tmp_path,
        now=_now,
    )
    await run_wake_cycle(
        _run_streamer(),
        agent_id="reviewer",
        wake_source=ManualWake(),
        coordination_dir=tmp_path,
        now=_now,
    )
    assert (
        read_state(state_path_for(tmp_path, agent_id="curator")).skip_streak == 1
    )
    assert (
        read_state(state_path_for(tmp_path, agent_id="reviewer")).skip_streak == 0
    )


# --- event payload shape ---------------------------------------------------


async def test_decision_event_payload_carries_agent_and_wake_source(tmp_path: Path) -> None:
    rec = _EventRecorder()
    await run_wake_cycle(
        _run_streamer(),
        agent_id="curator",
        wake_source=CronWake(cron_kind="doc-garden", run_id="r-1"),
        coordination_dir=tmp_path,
        on_event=rec,
        now=_now,
    )
    # Find the decision.run event and inspect its payload.
    run_events = [p for t, p in rec.events if t == "heartbeat.decision.run"]
    assert len(run_events) == 1
    payload = run_events[0]
    assert payload["agent_id"] == "curator"
    assert payload["action"] == "run"
    # wake_source serialised structurally.
    assert payload["wake_source"]["kind"] == "cron"
    assert payload["wake_source"]["cron_kind"] == "doc-garden"


async def test_wake_dropped_event_payload_carries_agent(tmp_path: Path) -> None:
    coord = tmp_path
    lock_path = coord / "heartbeat-curator.lock"
    holder_acquired = threading.Event()
    holder_release = threading.Event()

    def hold_lock() -> None:
        with exclusive_file_lock(lock_path):
            holder_acquired.set()
            holder_release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    assert holder_acquired.wait(timeout=5)
    try:
        rec = _EventRecorder()
        await run_wake_cycle(
            _skip_streamer(),
            agent_id="curator",
            wake_source=IdleTimerWake(idle_minutes=10),
            coordination_dir=coord,
            on_event=rec,
            now=_now,
        )
        dropped = [p for t, p in rec.events if t == "wake.dropped"]
        assert len(dropped) == 1
        assert dropped[0]["agent_id"] == "curator"
        assert dropped[0]["reason"] == "heartbeat_in_flight"
    finally:
        holder_release.set()
        thread.join(timeout=5)


# --- config defaults -------------------------------------------------------


def test_heartbeat_config_default_max_consecutive_skips_is_five() -> None:
    """Spec: default ``max_consecutive_skips = 5``."""
    assert HeartbeatConfig().max_consecutive_skips == 5


def test_heartbeat_config_is_frozen() -> None:
    cfg = HeartbeatConfig()
    with pytest.raises((AttributeError, TypeError)):
        setattr(cfg, "max_consecutive_skips", 99)


# --- API smoke -------------------------------------------------------------


def test_run_wake_cycle_is_async() -> None:
    import inspect

    assert inspect.iscoroutinefunction(run_wake_cycle)
