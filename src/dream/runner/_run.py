"""Task-level composition over planner + sprint primitives.

The public entry is :func:`run_task`. Everything here is a thin glue
layer: artefacts and outcome rules live in :mod:`dream.planner` and
:mod:`dream.sprint`; this module only sequences them and emits the
cross-role handoff events.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from dream.engine._cost import UsageSnapshot
from dream.planner import (
    LedgerStep,
    PlannerCallable,
    PlannerLedger,
    run_planner,
)
from dream.runner._observer import RunTaskObserver
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
    negotiate_contract_async,
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
    usage_by_model: Mapping[str, UsageSnapshot] = field(default_factory=dict)


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


@dataclass(frozen=True)
class _GeneratorPhaseResult:
    """Outcome of the lock-protected generator phase (2a + 2b) of one sprint.

    ``step is None`` signals the loop should stop (no pending work). Otherwise
    the claimed step, whether the evaluator is enabled, the (optionally written)
    contract, the re-loaded+saved ledger, and the events accumulated so far.
    """

    step: LedgerStep | None
    enabled: bool
    contract: SprintContract | None
    contract_path: Path | None
    ledger: PlannerLedger
    events: list[dict[str, Any]]


@dataclass(frozen=True)
class _EvaluatorPhaseResult:
    """Outcome of the evaluator phase (2c) or the disabled-evaluator branch (2d)."""

    eval_path: Path | None
    outcome: EvaluationOutcome | None
    ledger: PlannerLedger
    events: list[dict[str, Any]]


async def _run_generator_phase(
    *,
    root: Path,
    task_id: str,
    ledger_path: Path,
    sprint_number: int,
    ledger: PlannerLedger,
    evaluator_propose: EvaluatorPropose,
    generator_respond: GeneratorRespond,
    generator_execute: GeneratorExecute,
    verification_steps: tuple[dict[str, str], ...],
    goal_provider: SprintGoalProvider,
    emit: Callable[[dict[str, Any]], None],
) -> _GeneratorPhaseResult:
    """Phase 2a+2b: claim a step under the generator lock, negotiate (if the
    evaluator is enabled), commit the contract, and run the generator seam.

    Selection + claim of the next step happen *inside* the lock on a freshly
    re-loaded ledger (criterion #14), so two overlapping ``run_task`` calls
    can't both pick the same pending step from a stale snapshot.
    """
    events: list[dict[str, Any]] = []
    contract: SprintContract | None = None
    contract_path: Path | None = None
    with acquire_role_lock(root, task_id=task_id, role="generator"):
        ledger = PlannerLedger.load(ledger_path)
        step = _find_next_work(ledger)
        if step is None:
            return _GeneratorPhaseResult(
                step=None,
                enabled=False,
                contract=None,
                contract_path=None,
                ledger=ledger,
                events=events,
            )
        enabled = is_evaluator_enabled_for_sprint(ledger, sprint_override=None)

        emit(
            {
                "kind": "sprint.started",
                "sprint_number": sprint_number,
                "step_id": step.id,
                "step_description": step.description,
            }
        )

        if step.status == "pending":
            ledger = transition_step_to_in_progress(ledger, step.id)
            ledger.save(ledger_path)

        if enabled:
            carry = load_pending_carry_items(root, task_id=task_id, step_id=step.id)
            negotiation = await negotiate_contract_async(
                evaluator_propose=evaluator_propose,
                generator_respond=generator_respond,
                carry_items=carry,
            )
            if negotiation.warning_event is not None:
                events.append(negotiation.warning_event)
                emit(
                    {
                        "kind": "negotiation.imposed",
                        "sprint_number": sprint_number,
                        "rounds": negotiation.warning_event.get("rounds"),
                    }
                )

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
            emit(
                {
                    "kind": "contract.written",
                    "sprint_number": sprint_number,
                    "path": str(contract_path),
                }
            )

        # 2b. Generator execute (caller-supplied seam).
        emit(
            {
                "kind": "generator.started",
                "sprint_number": sprint_number,
                "step_id": step.id,
                "has_contract": contract is not None,
            }
        )
        await generator_execute(task_id, sprint_number, contract, step)
        emit(
            {
                "kind": "generator.completed",
                "sprint_number": sprint_number,
                "step_id": step.id,
            }
        )

        if enabled and contract_path is not None:
            events.append(
                handoff_event(
                    from_role="generator",
                    to_role="evaluator",
                    artefacts=[
                        HandoffArtefact(
                            kind="contract",
                            path=contract_path.relative_to(root).as_posix(),
                        ),
                    ],
                )
            )

    return _GeneratorPhaseResult(
        step=step,
        enabled=enabled,
        contract=contract,
        contract_path=contract_path,
        ledger=ledger,
        events=events,
    )


async def _run_evaluator_phase(
    *,
    root: Path,
    task_id: str,
    ledger_path: Path,
    sprint_number: int,
    step: LedgerStep,
    enabled: bool,
    contract: SprintContract | None,
    ledger: PlannerLedger,
    evaluator_run: EvaluatorRun,
    emit: Callable[[dict[str, Any]], None],
) -> _EvaluatorPhaseResult:
    """Phase 2c (lock-protected evaluator) or 2d (disabled → implicit pass).

    Independent of the generator lock. Returns the eval-artefact path + outcome
    (both ``None`` in the disabled branch) and the saved ledger.
    """
    events: list[dict[str, Any]] = []
    if not enabled:
        # 2d. Disabled-evaluator branch: implicit pass advances the step.
        ledger = _mark_step_done(ledger, step.id)
        ledger.save(ledger_path)
        return _EvaluatorPhaseResult(
            eval_path=None, outcome=None, ledger=ledger, events=events
        )

    assert contract is not None
    emit(
        {
            "kind": "evaluator.started",
            "sprint_number": sprint_number,
            "step_id": step.id,
        }
    )
    with acquire_role_lock(root, task_id=task_id, role="evaluator"):
        record = await evaluator_run(task_id, sprint_number, contract, step)
        eval_path = record_evaluation(root, record)
        if record.outcome == "fail":
            append_tech_debt(root, record)
        ledger = apply_outcome(ledger, record)
        ledger.save(ledger_path)
        outcome = record.outcome

    emit(
        {
            "kind": "evaluator.completed",
            "sprint_number": sprint_number,
            "outcome": record.outcome,
            "score": record.score,
            "notes": record.notes,
        }
    )

    # Emit a sprint.escalated event when a needs-changes evaluation pushed the
    # step to blocked (i.e. the N-strikes limit was reached).  We read the
    # post-apply step state from the updated ledger so apply_outcome itself
    # stays pure (no event coupling).
    if record.outcome == "needs-changes":
        updated_step = next(
            (s for s in ledger.steps if s.id == record.step_id), None
        )
        if updated_step is not None and updated_step.status == "blocked":
            emit(
                {
                    "kind": "sprint.escalated",
                    "task_id": task_id,
                    "step_id": record.step_id,
                    "needs_changes_count": updated_step.needs_changes_count,
                }
            )

    events.append(
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
    return _EvaluatorPhaseResult(
        eval_path=eval_path, outcome=outcome, ledger=ledger, events=events
    )


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
    observer: RunTaskObserver | None = None,
) -> RunTaskResult:
    """Compose a full task: planner → bounded sprint loop → done/blocked.

    See module docstring for the per-sprint algorithm. When ``observer``
    is supplied, a progress event is dispatched at every boundary
    (planner start/end, sprint start/end, contract written, generator /
    evaluator start/end, negotiation cap, role-session opened/closed +
    streamed text & tool calls). See :mod:`dream.runner._observer`.
    """
    if max_sprints < 0:
        raise ValueError(f"max_sprints must be >= 0, got {max_sprints}")

    root = Path(worktree_root)
    # Default goal provider: the step's own description (#5 inline fallback).
    goal_provider = goal_for_step or (lambda step, _sprint_number: step.description)

    def _emit(event: dict[str, Any]) -> None:
        if observer is not None:
            observer.on_event(event)

    _emit({"kind": "task.started", "task_id": task_id, "intent": intent})
    _emit({"kind": "planner.started", "task_id": task_id})

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
    _emit(
        {
            "kind": "planner.completed",
            "task_id": task_id,
            "spec_path": str(spec_path),
            "ledger_path": str(ledger_path),
            "step_count": len(ledger.steps),
        }
    )
    sprints: list[SprintRunResult] = []

    # 2. Sprint loop — each iteration is a generator phase (2a/2b) then an
    # evaluator phase (2c/2d). The generator phase returns ``step is None``
    # once no pending work remains, which stops the loop.
    for sprint_number in range(1, max_sprints + 1):
        gen = await _run_generator_phase(
            root=root,
            task_id=task_id,
            ledger_path=ledger_path,
            sprint_number=sprint_number,
            ledger=ledger,
            evaluator_propose=evaluator_propose,
            generator_respond=generator_respond,
            generator_execute=generator_execute,
            verification_steps=verification_steps,
            goal_provider=goal_provider,
            emit=_emit,
        )
        ledger = gen.ledger
        if gen.step is None:
            break
        step = gen.step

        evl = await _run_evaluator_phase(
            root=root,
            task_id=task_id,
            ledger_path=ledger_path,
            sprint_number=sprint_number,
            step=step,
            enabled=gen.enabled,
            contract=gen.contract,
            ledger=ledger,
            evaluator_run=evaluator_run,
            emit=_emit,
        )
        ledger = evl.ledger

        sprint_events = gen.events + evl.events
        sprints.append(
            SprintRunResult(
                sprint_number=sprint_number,
                step_id=step.id,
                contract_path=gen.contract_path,
                eval_path=evl.eval_path,
                outcome=evl.outcome,
                events=tuple(sprint_events),
            )
        )
        events.extend(sprint_events)
        _emit(
            {
                "kind": "sprint.completed",
                "sprint_number": sprint_number,
                "step_id": step.id,
                "outcome": evl.outcome,
            }
        )

    _emit(
        {
            "kind": "task.completed",
            "task_id": task_id,
            "sprint_count": len(sprints),
        }
    )
    return RunTaskResult(
        task_id=task_id,
        spec_path=spec_path,
        ledger_path=ledger_path,
        final_ledger=ledger,
        sprints=tuple(sprints),
        events=tuple(events),
    )
