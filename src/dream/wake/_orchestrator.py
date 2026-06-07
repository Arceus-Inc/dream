"""Spec 06.5 slice 2 — ``run_wake_cycle`` orchestrator.

This is the one place where the slice 2 pieces compose:

- per-agent **lock** (non-blocking) to dedup overlapping wakes,
- persistent **HeartbeatState** (skip-streak + last-decided-at),
- **forced mode** when the streak hits :class:`HeartbeatConfig.max_consecutive_skips`,
- single-turn :func:`run_background_turn` call, and
- typed event emission (``heartbeat.decision.{run,skip,forced}``,
  ``heartbeat.missing``, ``wake.dropped``) through an optional callback.

The orchestrator does NOT spawn the downstream work session — slice 3
(or the REPL ``/wake`` integration) decides what to do with a ``run``
decision. The lock is released as soon as the decision is committed.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dream.engine._loop import TurnStreamer
from dream.utils.file_lock import try_exclusive_file_lock
from dream.wake._decision import HeartbeatDecision
from dream.wake._runner import run_background_turn
from dream.wake._source import WakeSource, wake_source_to_dict
from dream.wake._state import (
    HeartbeatState,
    read_state,
    state_path_for,
    write_state,
)

EventEmitter = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class HeartbeatConfig:
    """Tunables for the wake-cycle orchestrator.

    Slice 2 surfaces a single knob: how many consecutive skips before the
    anti-coma guard forces a wake. The default of ``5`` matches the spec.
    """

    max_consecutive_skips: int = 5


_DEFAULT_CONFIG = HeartbeatConfig()


@dataclass(frozen=True)
class WakeOutcome:
    """What a single ``run_wake_cycle`` call produced.

    Either a :class:`HeartbeatDecision` (the lock was free and the model
    ran) OR a ``dropped_reason`` string (the lock was held by another
    wake — the decision was deferred). Exactly one of the two fields is
    populated.
    """

    decision: HeartbeatDecision | None
    dropped_reason: str | None = None


def _default_now() -> datetime:
    return datetime.now(UTC)


def _lock_path_for(coordination_dir: Path, *, agent_id: str) -> Path:
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if "/" in agent_id or "\\" in agent_id:
        raise ValueError(f"agent_id must not contain path separators: {agent_id!r}")
    return coordination_dir / f"heartbeat-{agent_id}.lock"


def _emit(
    sink: EventEmitter | None, event_type: str, payload: dict[str, Any]
) -> None:
    # The observer is untrusted from the orchestrator's perspective: a faulty
    # callback must not abort the wake cycle *after* the decision and state
    # update have already been committed (which would risk partial-commit and
    # duplicate decisions on a retry). Trap observer failures the same way the
    # engine's TransitionBus traps listener exceptions.
    if sink is None:
        return
    with contextlib.suppress(Exception):
        sink(event_type, payload)


def _decision_event_type(decision: HeartbeatDecision) -> str:
    if decision.forced:
        return "heartbeat.decision.forced"
    if decision.outcome == "missing":
        return "heartbeat.missing"
    if decision.action == "run":
        return "heartbeat.decision.run"
    return "heartbeat.decision.skip"


def _decision_payload(
    decision: HeartbeatDecision, *, agent_id: str
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "action": decision.action,
        "tasks": list(decision.tasks),
        "reason": decision.reason,
        "forced": decision.forced,
        "outcome": decision.outcome,
        "decided_at": decision.decided_at.isoformat(),
        "wake_source": (
            wake_source_to_dict(decision.wake_source)
            if decision.wake_source is not None
            else None
        ),
    }


async def run_wake_cycle(
    streamer: TurnStreamer,
    *,
    agent_id: str,
    wake_source: WakeSource,
    coordination_dir: Path,
    config: HeartbeatConfig = _DEFAULT_CONFIG,
    prompt_override_path: Path | None = None,
    on_event: EventEmitter | None = None,
    now: Callable[[], datetime] = _default_now,
) -> WakeOutcome:
    """Execute one full wake cycle for ``agent_id``.

    The flow:

    1. Try to acquire ``heartbeat-{agent_id}.lock`` non-blocking. If held,
       emit ``wake.dropped`` and return.
    2. Read the persistent ``HeartbeatState`` for this agent.
    3. Decide if forced mode applies (``skip_streak >= max_consecutive_skips``).
    4. Drive one ``run_background_turn`` (forced or not).
    5. Update the persistent state — skip bumps the streak, run resets
       it, missing leaves it.
    6. Emit the decision event and release the lock.
    """
    coordination_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(coordination_dir, agent_id=agent_id)
    state_path = state_path_for(coordination_dir, agent_id=agent_id)

    with try_exclusive_file_lock(lock_path) as acquired:
        if not acquired:
            _emit(
                on_event,
                "wake.dropped",
                {
                    "agent_id": agent_id,
                    "reason": "heartbeat_in_flight",
                    "wake_source": wake_source_to_dict(wake_source),
                },
            )
            return WakeOutcome(decision=None, dropped_reason="heartbeat_in_flight")

        prior_state = read_state(state_path)
        is_forced = prior_state.skip_streak >= config.max_consecutive_skips

        decision = await run_background_turn(
            streamer,
            wake_source=wake_source,
            prompt_override_path=prompt_override_path,
            forced=is_forced,
            forced_skip_streak=prior_state.skip_streak,
            now=now,
        )

        next_state = _advance_state(prior_state, decision, now=now)
        if next_state != prior_state:
            write_state(state_path, next_state)

        _emit(
            on_event,
            _decision_event_type(decision),
            _decision_payload(decision, agent_id=agent_id),
        )
        return WakeOutcome(decision=decision)


def _advance_state(
    prior: HeartbeatState,
    decision: HeartbeatDecision,
    *,
    now: Callable[[], datetime],
) -> HeartbeatState:
    """Compute the next state from the decision.

    Rules (spec 06.5):

    - ``missing`` outcome: streak unchanged, ``last_decided_at`` unchanged
      (no decision was actually made).
    - ``decided`` + ``skip``: streak += 1, ``last_decided_at`` = now.
    - ``decided`` + ``run`` (forced or not): streak = 0, ``last_decided_at`` = now.
    """
    if decision.outcome == "missing":
        return prior
    decided_at = now()
    if decision.action == "run":
        return HeartbeatState(skip_streak=0, last_decided_at=decided_at)
    return HeartbeatState(
        skip_streak=prior.skip_streak + 1, last_decided_at=decided_at
    )


__all__ = [
    "EventEmitter",
    "HeartbeatConfig",
    "WakeOutcome",
    "run_wake_cycle",
]
