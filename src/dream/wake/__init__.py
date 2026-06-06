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

Slice 1 (this slice) ships the virtual ``HeartbeatTool``, the
``HeartbeatDecision`` record, the bundled default prompt, and a tiny
single-turn runner. The skip-streak counter, anti-coma forced run, wake
source registry, and per-agent lock file ship in slice 2.
"""

from __future__ import annotations

from dream.wake._decision import HeartbeatDecision
from dream.wake._prompt import load_heartbeat_prompt
from dream.wake._runner import run_background_turn
from dream.wake._tool import HeartbeatTool

__all__ = [
    "HeartbeatDecision",
    "HeartbeatTool",
    "load_heartbeat_prompt",
    "run_background_turn",
]
