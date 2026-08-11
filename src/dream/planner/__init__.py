"""Planner role end-to-end (spec 10 slice E).

The planner is the first role to run on a new task. It reads intent + repo
state, drafts a narrative spec and a JSON ledger of pending steps, commits
both into the worktree under ``docs/exec-plans/active/{task-id}.{md,json}``,
and hands off to the generator.

This package owns the runs-once orchestration shell + the artefact shapes.
The role's tool restrictions (read-only outside the exec-plan folder) come
from the role manifest in :mod:`dream.roles`; the handoff event helper
comes from :mod:`dream.swarm._handoff`. The actual LLM call is supplied by
the caller as a :data:`PlannerCallable` — slice 10-G will wire the
production one through the REPL session entry point.
"""

from __future__ import annotations

from dream.planner._artefacts import (
    LedgerStep,
    PlannerLedger,
    StepStatus,
    planner_ledger_path,
    planner_spec_path,
)
from dream.planner._run import (
    PlannerAlreadyRan,
    PlannerCallable,
    PlannerOutput,
    PlannerResult,
    PlannerRunCompleted,
    PlannerStreamEvent,
    run_planner,
)

__all__ = [
    "LedgerStep",
    "PlannerAlreadyRan",
    "PlannerCallable",
    "PlannerLedger",
    "PlannerOutput",
    "PlannerResult",
    "PlannerRunCompleted",
    "PlannerStreamEvent",
    "StepStatus",
    "planner_ledger_path",
    "planner_spec_path",
    "run_planner",
]
