"""Wake-cycle heartbeat (Spec 06.5).

A *background turn* is a single, dedicated model turn that runs at session
boundaries — when a wake source fires (idle timer, cron, REPL ``/wake``).
Its only job is to decide whether the agent should start work, and if so,
what tasks to queue. The decision is captured as a ``HeartbeatDecision``
and appended to the session jsonl.

This package is deliberately separate from ``dream.engine._heartbeat``:

- ``dream.engine._heartbeat`` is the **liveness** heartbeat from Spec 03 —
  it polls a health endpoint *inside* a long LLM call to detect substrate
  coma. Its output is a side-effect (cancel the turn).
- ``dream.wake`` is the **wake-cycle** heartbeat from Spec 06.5 — it runs
  *between* sessions, per wake source, and produces a structured
  ``HeartbeatDecision`` record.

Slice 1 shipped the virtual ``HeartbeatTool``, the ``HeartbeatDecision``
record, the bundled default prompt, and a tiny single-turn runner.
Slice 2 added: typed ``WakeSource`` discriminator, persistent
per-agent ``HeartbeatState`` (skip-streak), ``HeartbeatConfig`` +
anti-coma forced mode, the per-agent ``heartbeat-{agent}.lock`` for
overlap dedup, and the :func:`run_wake_cycle` orchestrator that ties
all of the above together.
"""

from __future__ import annotations

from dream.wake._decision import HeartbeatDecision
from dream.wake._orchestrator import (
    EventEmitter,
    HeartbeatConfig,
    WakeOutcome,
    run_wake_cycle,
)
from dream.wake._prompt import BUNDLED_HEARTBEAT_PROMPT, load_heartbeat_prompt
from dream.wake._runner import run_background_turn
from dream.wake._source import (
    CronWake,
    IdleTimerWake,
    InboundMessageWake,
    ManualWake,
    WakeSource,
)
from dream.wake._tool import ForcedHeartbeatInput, HeartbeatTool

__all__ = [
    "BUNDLED_HEARTBEAT_PROMPT",
    "CronWake",
    "EventEmitter",
    "ForcedHeartbeatInput",
    "HeartbeatConfig",
    "HeartbeatDecision",
    "HeartbeatTool",
    "IdleTimerWake",
    "InboundMessageWake",
    "ManualWake",
    "WakeOutcome",
    "WakeSource",
    "load_heartbeat_prompt",
    "run_background_turn",
    "run_wake_cycle",
]
