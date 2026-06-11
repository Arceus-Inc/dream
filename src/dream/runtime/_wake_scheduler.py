"""Idle-timer wake scheduler (spec 15 P1 §2).

Drives :func:`dream.wake.run_wake_cycle` on a loop — the wake-cycle
heartbeat finally gets a scheduler instead of waiting for a human to
type ``/wake``. A ``run`` decision is surfaced two ways:

- a ``runtime.wake.run`` event on the runtime stream, and
- an optional async ``on_run`` handler.

What to *do* with the decided tasks is deliberately not decided here:
queueing policy belongs to the consumer (an employee in the business
repo — Model A), not the SDK.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from dream.runtime._supervisor import EmitFn
from dream.runtime._wake_notes import WakeNote, WakeNoteStore
from dream.wake import (
    CronWake,
    HeartbeatConfig,
    HeartbeatDecision,
    IdleTimerWake,
    WakeOutcome,
    WakeSource,
    run_wake_cycle,
)

__all__ = ["RunCycleFn", "wake_scheduler_loop"]

_SECONDS_PER_MINUTE = 60


class RunCycleFn(Protocol):
    """Shape of :func:`dream.wake.run_wake_cycle` (injectable for tests)."""

    def __call__(self, streamer: Any, **kwargs: Any) -> Awaitable[WakeOutcome]: ...


async def _default_run_cycle(streamer: Any, **kwargs: Any) -> WakeOutcome:
    # kwargs-adapter so the concrete run_wake_cycle signature satisfies the
    # injectable Protocol without widening its own typing.
    return await run_wake_cycle(streamer, **kwargs)


def _wake_source(pending: list[WakeNote], *, idle_minutes: int) -> WakeSource:
    """Cron notes make the wake a ``CronWake``; otherwise the idle timer."""
    if pending:
        return CronWake(cron_kind=pending[0].source)
    return IdleTimerWake(idle_minutes=idle_minutes)


def _notes_context(pending: list[WakeNote]) -> str | None:
    """Render queued notes for the heartbeat stimulus, oldest-first."""
    if not pending:
        return None
    lines = [f"- [{note.source}] {note.text}" for note in pending]
    return (
        "PENDING CRON NOTES (queued since the last wake — factor these "
        "into your decision):\n" + "\n".join(lines)
    )


def _checklist_is_empty(prompt_override_path: Path | None) -> bool:
    """True only for an existing override file with no non-whitespace content."""
    if prompt_override_path is None:
        return False
    try:
        return prompt_override_path.read_text(encoding="utf-8").strip() == ""
    except OSError:
        return False


async def wake_scheduler_loop(
    *,
    streamer_factory: Callable[[], Any],
    agent_id: str,
    coordination_dir: Path,
    idle_minutes: int,
    heartbeat_config: HeartbeatConfig,
    emit: EmitFn,
    on_run: Callable[[HeartbeatDecision], Awaitable[None]] | None = None,
    prompt_override_path: Path | None = None,
    notes: WakeNoteStore | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    run_cycle: RunCycleFn = _default_run_cycle,
) -> None:
    """Sleep ``idle_minutes``, fire one wake cycle, repeat forever.

    Heartbeat events from the orchestrator (``heartbeat.decision.*``,
    ``wake.dropped``) are forwarded onto the runtime event stream. The
    per-agent lock inside ``run_wake_cycle`` already dedups overlap, so
    a dropped cycle is just a tick that produced no decision.
    """

    def _forward(event_type: str, payload: dict[str, Any]) -> None:
        emit(event_type, **payload)

    while True:
        await sleep(idle_minutes * _SECONDS_PER_MINUTE)
        pending = notes.drain() if notes is not None else []
        if not pending and _checklist_is_empty(prompt_override_path):
            # Zero-cost skip: an existing-but-empty checklist is the
            # operator's explicit "nothing to check" — don't spend a model
            # call discovering that. (A *missing* file falls back to the
            # bundled default prompt and still wakes; queued notes ARE
            # content and always wake.)
            emit("wake.skipped", agent_id=agent_id, reason="empty-checklist")
            continue
        outcome = await run_cycle(
            streamer_factory(),
            agent_id=agent_id,
            wake_source=_wake_source(pending, idle_minutes=idle_minutes),
            coordination_dir=coordination_dir,
            config=heartbeat_config,
            prompt_override_path=prompt_override_path,
            extra_context=_notes_context(pending),
            on_event=_forward,
        )
        decision = outcome.decision
        if decision is None or decision.action != "run":
            continue
        emit(
            "runtime.wake.run",
            agent_id=agent_id,
            tasks=list(decision.tasks),
            reason=decision.reason,
            forced=decision.forced,
        )
        if on_run is not None:
            await on_run(decision)
