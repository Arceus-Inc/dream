"""Runner: end-to-end task composition (spec 10 slice G).

Stitches the planner-runs-once primitive (slice E) and the sprint
primitives (slice F) into a complete ``run_task`` loop:

1. Run the planner once → narrative spec + JSON ledger committed.
2. Loop sprints up to ``max_sprints``:

   a. Resume an ``in_progress`` step (needs-changes retry) or claim
      the next ``pending`` one. Stop when neither exists.
   b. Under the per-task generator role lock (criterion #14):

      - If the evaluator is enabled (ledger-level), run a bounded
        negotiation (≤ 3 rounds, criterion #9) seeded with the prior
        sprint's ``needs-changes`` carry items; commit the sprint
        contract **before** invoking the generator (criterion #7).
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

from dream.runner._evaluator_head import (
    EvaluatorHeadParseError,
    make_evaluator_head,
)
from dream.runner._generator_head import make_generator_head
from dream.runner._negotiator_heads import (
    EvaluatorProposeHeadParseError,
    GeneratorRespondHeadParseError,
    make_evaluator_propose_head,
    make_generator_respond_head,
)
from dream.runner._observer import RunTaskObserver, StdioObserver
from dream.runner._plan_admission import PlanAdmission
from dream.runner._planner_head import (
    PlannerHeadParseError,
    make_planner_head,
)
from dream.runner._role_session import (
    RoleSessionError,
    RunRoleResult,
    resolve_role_manifest,
    run_role,
)
from dream.runner._run import (
    EvaluatorRun,
    GeneratorExecute,
    RunTaskResult,
    SprintGoalProvider,
    SprintRunResult,
    run_task,
)

__all__ = [
    "EvaluatorHeadParseError",
    "EvaluatorProposeHeadParseError",
    "EvaluatorRun",
    "GeneratorExecute",
    "GeneratorRespondHeadParseError",
    "PlanAdmission",
    "PlannerHeadParseError",
    "RoleSessionError",
    "RunRoleResult",
    "RunTaskObserver",
    "RunTaskResult",
    "SprintGoalProvider",
    "SprintRunResult",
    "StdioObserver",
    "make_evaluator_head",
    "make_evaluator_propose_head",
    "make_generator_head",
    "make_generator_respond_head",
    "make_planner_head",
    "resolve_role_manifest",
    "run_role",
    "run_task",
]
