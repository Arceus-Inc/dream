"""Runner: end-to-end task composition (spec 10 slice G).

Stitches the planner-runs-once primitive (slice E) and the sprint
primitives (slice F) into a complete ``run_task`` loop:

1. Run the planner once → narrative spec + JSON ledger committed.
2. Loop sprints up to ``max_sprints``:

   a. Resume an ``in_progress`` step (needs-changes retry) or claim
      the next ``pending`` one. Stop when neither exists.
   b. Under the per-task generator role lock (criterion #14):

      - If the evaluator is enabled (ledger-level), build the sprint
        contract from the step's plan-time acceptance criteria plus the
        prior sprint's ``needs-changes`` carry items, and commit it
        **before** invoking the generator (criterion #7).
      - Invoke the caller-supplied ``generator_execute`` callable.
      - Emit ``handoff.generator_to_evaluator`` with the contract
        pointer (criterion #20).

   c. If the evaluator is enabled, under the per-task evaluator lock:

      - Invoke ``evaluator_run``; persist its record (criterion #10).
      - Apply the outcome to the ledger (pass→done, needs-changes→
        stays in_progress, fail→blocked + tech-debt append).
      - Emit ``handoff.evaluator_to_generator`` with the eval pointer.

   d. If the evaluator is disabled, mark the step ``done`` implicitly
      (spec §"Disabling the evaluator").

This slice ships *pure composition*: the LLM-driven seams (planner,
generator, evaluator) are injected as awaitable callables. The subagent
spawn integration, leader-inbox draining, and the ``harness session``
CLI surface follow in later slices; nothing here forces a subprocess
or a remote backend.
"""

from __future__ import annotations

from dream.runner.envelopes import make_generator_head
from dream.runner.evaluator import (
    EvaluatorHeadParseError,
    make_evaluator_head,
)
from dream.runner.events import RunTaskObserver
from dream.runner.observe import StdioObserver, UsageMeter
from dream.runner.planner import (
    PlannerHeadParseError,
    make_planner_head,
)
from dream.runner.role import (
    RoleSessionError,
    RunRoleResult,
    resolve_role_manifest,
    role_session_id,
    run_role,
)
from dream.runner.task import (
    EvaluatorRun,
    GeneratorExecute,
    PlanAdmission,
    RunTaskResult,
    SprintGoalProvider,
    SprintRunResult,
    run_task,
)

__all__ = [
    "EvaluatorHeadParseError",
    "EvaluatorRun",
    "GeneratorExecute",
    "PlanAdmission",
    "PlannerHeadParseError",
    "RoleSessionError",
    "RunRoleResult",
    "RunTaskObserver",
    "RunTaskResult",
    "SprintGoalProvider",
    "SprintRunResult",
    "StdioObserver",
    "UsageMeter",
    "make_evaluator_head",
    "make_generator_head",
    "make_planner_head",
    "resolve_role_manifest",
    "role_session_id",
    "run_role",
    "run_task",
]
