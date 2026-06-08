"""Task-level composition over planner + sprint primitives.

The public entry is :func:`run_task`. Everything here is a thin glue
layer: artefacts and outcome rules live in :mod:`dream.planner` and
:mod:`dream.sprint`; this module only sequences them and emits the
cross-role handoff events.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dream.planner import (
    LedgerStep,
    PlannerCallable,
    PlannerLedger,
    planner_ledger_path,
    planner_spec_path,
    run_planner,
)
from dream.sprint import (
    EvaluationOutcome,
    EvaluatorPropose,
    GeneratorRespond,
    SprintContract,
    acquire_role_lock,
    append_tech_debt,
    apply_outcome,
    build_contract_from_negotiation,
    is_evaluator_enabled_for_sprint,
    load_pending_carry_items,
    negotiate_contract,
    pick_next_pending_step,
    record_evaluation,
    sprint_contract_path,
    transition_step_to_in_progress,
)
from dream.sprint._evaluation import EvaluationRecord
from dream.swarm._handoff import HandoffArtefact, handoff_event

__all__ = [
    "EvaluatorRun",
    "GeneratorExecute",
    "RunTaskResult",
    "SprintGoalProvider",
    "SprintRunResult",
    "run_task",
]


GeneratorExecute = Callable[
    [str, int, "SprintContract | None", LedgerStep],
    Awaitable[None],
]
"""``(task_id, sprint_number, contract|None, step) -> None``.

Receives ``None`` for the contract when the evaluator is disabled
(no contract is written in that branch).
"""

EvaluatorRun = Callable[
    [str, int, SprintContract, LedgerStep],
    Awaitable[EvaluationRecord],
]
"""``(task_id, sprint_number, contract, step) -> EvaluationRecord``."""

SprintGoalProvider = Callable[[LedgerStep, int], str]
"""``(step, sprint_number) -> goal``. Defaults to ``step.description``."""


@dataclass(frozen=True)
class SprintRunResult:
    """One sprint's outcome surfaced for caller observation."""

    sprint_number: int
    step_id: str
    contract_path: Path | None
    eval_path: Path | None
    outcome: EvaluationOutcome | None
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RunTaskResult:
    """End-of-task summary."""

    task_id: str
    spec_path: Path
    ledger_path: Path
    final_ledger: PlannerLedger
    sprints: tuple[SprintRunResult, ...]
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def _default_goal(step: LedgerStep, sprint_number: int) -> str:
    return step.description


def _find_next_work(ledger: PlannerLedger) -> LedgerStep | None:
    """Resume an in-progress step (needs-changes retry) or claim next pending.

    The runner keeps stepping until both lookups return ``None``.
    """
    for step in ledger.steps:
        if step.status == "in_progress":
            return step
    return pick_next_pending_step(ledger)


def _mark_step_done(ledger: PlannerLedger, step_id: str) -> PlannerLedger:
    """Used by the evaluator-disabled branch — no contract, implicit pass."""
    new_steps = list(ledger.steps)
    for i, step in enumerate(new_steps):
        if step.id == step_id:
            new_steps[i] = replace(step, status="done")
            return replace(ledger, steps=tuple(new_steps))
    raise KeyError(f"step id not in ledger: {step_id!r}")


async def run_task(
    *,
    task_id: str,
    intent: str,
    worktree_root: str | Path,
    planner: PlannerCallable,
    generator_execute: GeneratorExecute,
    evaluator_propose: EvaluatorPropose,
    generator_respond: GeneratorRespond,
    evaluator_run: EvaluatorRun,
    max_sprints: int = 10,
    verification_steps: tuple[dict[str, str], ...] = (),
    goal_for_step: SprintGoalProvider | None = None,
) -> RunTaskResult:
    """Compose a full task: planner → bounded sprint loop → done/blocked.

    See module docstring for the per-sprint algorithm.
    """
    if max_sprints < 0:
        raise ValueError(f"max_sprints must be >= 0, got {max_sprints}")

    root = Path(worktree_root)
    goal_provider = goal_for_step or _default_goal

    # 1. Planner — runs once, writes both artefacts.
    planner_result = await run_planner(
        task_id=task_id,
        intent=intent,
        worktree_root=root,
        planner=planner,
    )

    events: list[dict[str, Any]] = list(planner_result.events)
    ledger_path = planner_result.ledger_path
    spec_path = planner_result.spec_path

    ledger = PlannerLedger.load(ledger_path)
    sprints: list[SprintRunResult] = []

    # 2. Sprint loop.
    for sprint_number in range(1, max_sprints + 1):
        step = _find_next_work(ledger)
        if step is None:
            break

        sprint_events: list[dict[str, Any]] = []
        enabled = is_evaluator_enabled_for_sprint(ledger, sprint_override=None)
        contract: SprintContract | None = None
        contract_path: Path | None = None
        eval_path: Path | None = None
        outcome: EvaluationOutcome | None = None

        # 2a. Generator: lock-protected (criterion #14).
        with acquire_role_lock(root, task_id=task_id, role="generator"):
            if step.status == "pending":
                ledger = transition_step_to_in_progress(ledger, step.id)
                ledger.save(ledger_path)

            if enabled:
                carry = load_pending_carry_items(
                    root, task_id=task_id, step_id=step.id
                )
                negotiation = negotiate_contract(
                    evaluator_propose=evaluator_propose,
                    generator_respond=generator_respond,
                    carry_items=carry,
                )
                if negotiation.warning_event is not None:
                    sprint_events.append(negotiation.warning_event)

                contract = build_contract_from_negotiation(
                    negotiation,
                    task_id=task_id,
                    sprint_number=sprint_number,
                    goal=goal_provider(step, sprint_number),
                    verification_steps=verification_steps,
                    evaluator_enabled=True,
                )
                contract_path = sprint_contract_path(
                    root, task_id=task_id, sprint_number=sprint_number
                )
                # Criterion #7: contract committed BEFORE generator touches sources.
                contract.save(contract_path)

            # 2b. Generator execute (caller-supplied seam).
            await generator_execute(task_id, sprint_number, contract, step)

            if enabled and contract_path is not None:
                handoff_g2e = handoff_event(
                    from_role="generator",
                    to_role="evaluator",
                    artefacts=[
                        HandoffArtefact(
                            kind="contract",
                            path=contract_path.relative_to(root).as_posix(),
                        ),
                    ],
                )
                sprint_events.append(handoff_g2e)

        # 2c. Evaluator branch (lock-protected, independent of generator lock).
        if enabled:
            assert contract is not None  # noqa: S101 — invariant guaranteed above
            with acquire_role_lock(root, task_id=task_id, role="evaluator"):
                record = await evaluator_run(task_id, sprint_number, contract, step)
                eval_path = record_evaluation(root, record)
                if record.outcome == "fail":
                    append_tech_debt(root, record)
                ledger = apply_outcome(ledger, record)
                ledger.save(ledger_path)
                outcome = record.outcome

            sprint_events.append(
                handoff_event(
                    from_role="evaluator",
                    to_role="generator",
                    artefacts=[
                        HandoffArtefact(
                            kind="eval",
                            path=eval_path.relative_to(root).as_posix(),
                        ),
                    ],
                )
            )
        else:
            # 2d. Disabled-evaluator branch: implicit pass advances the step.
            ledger = _mark_step_done(ledger, step.id)
            ledger.save(ledger_path)

        sprints.append(
            SprintRunResult(
                sprint_number=sprint_number,
                step_id=step.id,
                contract_path=contract_path,
                eval_path=eval_path,
                outcome=outcome,
                events=tuple(sprint_events),
            )
        )
        events.extend(sprint_events)

    return RunTaskResult(
        task_id=task_id,
        spec_path=spec_path,
        ledger_path=ledger_path,
        final_ledger=ledger,
        sprints=tuple(sprints),
        events=tuple(events),
    )
