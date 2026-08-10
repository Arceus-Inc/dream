"""Task-level composition over planner + sprint primitives.

The public entry is :func:`run_task`. Everything here is a thin glue
layer: artefacts and outcome rules live in :mod:`dream.planner` and
:mod:`dream.sprint`; this module only sequences them and emits the
cross-role handoff events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from dream.engine._cost import UsageSnapshot
from dream.errors import RunPhase, RunTaskError, TaskCancelled
from dream.planner import (
    LedgerStep,
    PlannerCallable,
    PlannerLedger,
    PlannerStreamEvent,
    planner_ledger_path,
    planner_spec_path,
    run_planner,
)
from dream.runner.events import (
    ContractWritten,
    EvaluatorCompleted,
    EvaluatorStarted,
    GeneratorCompleted,
    GeneratorStarted,
    PlannerCompleted,
    PlannerSkipped,
    PlannerStarted,
    RunTaskEvent,
    RunTaskObserver,
    SprintCompleted,
    SprintEscalated,
    SprintStarted,
    TaskCompleted,
    TaskStarted,
)
from dream.sprint import (
    EvaluationOutcome,
    SprintContract,
    acquire_role_lock,
    append_tech_debt,
    apply_outcome,
    build_contract_from_step,
    is_evaluator_enabled_for_sprint,
    load_pending_carry_items,
    next_sprint_number,
    pick_next_pending_step,
    record_evaluation,
    sprint_contract_path,
    transition_step_to_in_progress,
)
from dream.sprint._evaluation import EvaluationRecord
from dream.sprint._outcome import NEEDS_CHANGES_LIMIT
from dream.swarm._handoff import HandoffArtefact, handoff_event

__all__ = [
    "EvaluatorRun",
    "GeneratorExecute",
    "PlanAdmission",
    "RunTaskResult",
    "SprintGoalProvider",
    "SprintRunResult",
    "run_task",
]


class PlanAdmission(StrEnum):
    """Admission policy for the planner phase of :func:`run_task`.

    Using a str Enum (not free-form strings) keeps call sites typed and
    serialisable without a parallel stringly vocabulary.
    """

    FRESH = "fresh"
    """Always invoke the planner. Raises ``PlannerAlreadyRan`` if artefacts exist."""

    RESUME = "resume"
    """Skip the planner when a ledger already exists; otherwise plan once."""


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
    events: tuple[PlannerStreamEvent, ...] = field(default_factory=tuple)
    evaluation: EvaluationRecord | None = None


@dataclass(frozen=True)
class RunTaskResult:
    """End-of-task summary."""

    task_id: str
    spec_path: Path
    ledger_path: Path
    final_ledger: PlannerLedger
    sprints: tuple[SprintRunResult, ...]
    events: tuple[PlannerStreamEvent, ...] = field(default_factory=tuple)
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
    """Outcome of the lock-protected generator phase (2a + 2b) of one sprint."""

    step: LedgerStep | None
    enabled: bool
    contract: SprintContract | None
    contract_path: Path | None
    ledger: PlannerLedger
    events: list[PlannerStreamEvent]


@dataclass(frozen=True)
class _EvaluatorPhaseResult:
    """Outcome of the evaluator phase (2c) or the disabled-evaluator branch (2d)."""

    eval_path: Path | None
    outcome: EvaluationOutcome | None
    evaluation: EvaluationRecord | None
    ledger: PlannerLedger
    events: list[PlannerStreamEvent]


async def _run_generator_phase(
    *,
    root: Path,
    task_id: str,
    ledger_path: Path,
    sprint_number: int,
    ledger: PlannerLedger,
    generator_execute: GeneratorExecute,
    verification_steps: tuple[Mapping[str, str], ...],
    goal_provider: SprintGoalProvider,
    emit: Callable[[RunTaskEvent], None],
    rubric: str = "",
) -> _GeneratorPhaseResult:
    """Phase 2a+2b: claim a step under the generator lock, commit the contract
    the plan already implies (if the evaluator is enabled), and run the
    generator seam.
    """
    events: list[PlannerStreamEvent] = []
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
            SprintStarted(
                sprint_number=sprint_number,
                step_id=step.id,
                step_description=step.description,
            )
        )

        if step.status == "pending":
            ledger = transition_step_to_in_progress(ledger, step.id)
            ledger.save(ledger_path)

        if enabled:
            contract = build_contract_from_step(
                step,
                task_id=task_id,
                sprint_number=sprint_number,
                goal=goal_provider(step, sprint_number),
                verification_steps=tuple(dict(s) for s in verification_steps),
                carry_items=load_pending_carry_items(
                    root, task_id=task_id, step_id=step.id
                ),
                evaluator_enabled=True,
                rubric=rubric,
            )
            contract_path = sprint_contract_path(
                root, task_id=task_id, sprint_number=sprint_number
            )
            # Criterion #7: contract committed BEFORE generator touches sources.
            contract.save(contract_path)
            emit(
                ContractWritten(
                    sprint_number=sprint_number,
                    path=str(contract_path),
                )
            )

        emit(
            GeneratorStarted(
                sprint_number=sprint_number,
                step_id=step.id,
                has_contract=contract is not None,
            )
        )
        await generator_execute(task_id, sprint_number, contract, step)
        emit(
            GeneratorCompleted(
                sprint_number=sprint_number,
                step_id=step.id,
            )
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
    emit: Callable[[RunTaskEvent], None],
) -> _EvaluatorPhaseResult:
    """Phase 2c (lock-protected evaluator) or 2d (disabled → implicit pass)."""
    events: list[PlannerStreamEvent] = []
    if not enabled:
        ledger = _mark_step_done(ledger, step.id)
        ledger.save(ledger_path)
        return _EvaluatorPhaseResult(
            eval_path=None,
            outcome=None,
            evaluation=None,
            ledger=ledger,
            events=events,
        )

    assert contract is not None
    emit(
        EvaluatorStarted(
            sprint_number=sprint_number,
            step_id=step.id,
        )
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
        EvaluatorCompleted(
            sprint_number=sprint_number,
            outcome=record.outcome,
            score=record.score,
            notes=record.notes,
        )
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
        eval_path=eval_path,
        outcome=outcome,
        evaluation=record,
        ledger=ledger,
        events=events,
    )


@contextmanager
def _phase(phase: RunPhase) -> Iterator[None]:
    """Surface any loop fault as a typed :class:`RunTaskError` naming ``phase``."""
    try:
        yield
    except (asyncio.CancelledError, TaskCancelled, RunTaskError):
        raise
    except Exception as exc:
        raise RunTaskError(str(exc) or repr(exc), phase=phase, cause=exc) from exc


async def run_task(
    *,
    task_id: str,
    intent: str,
    worktree_root: str | Path,
    planner: PlannerCallable,
    generator_execute: GeneratorExecute,
    evaluator_run: EvaluatorRun,
    max_sprints: int = 10,
    verification_steps: tuple[Mapping[str, str], ...] = (),
    goal_for_step: SprintGoalProvider | None = None,
    observer: RunTaskObserver | None = None,
    rubric: str = "",
    plan_admission: PlanAdmission = PlanAdmission.FRESH,
) -> RunTaskResult:
    """Compose a full task: planner → bounded sprint loop → done/blocked.

    When ``observer`` is supplied, a progress event is dispatched at every
    boundary. See :mod:`dream.runner.events`.

    ``plan_admission`` controls whether the planner may run:

    - :attr:`PlanAdmission.FRESH` — always plan (default; raises if artefacts exist).
    - :attr:`PlanAdmission.RESUME` — skip planning when a ledger already exists so
      a later call with the same ``task_id`` can continue needs-changes repair
      without minting a new Dream identity.
    """
    if max_sprints < 0:
        raise ValueError(f"max_sprints must be >= 0, got {max_sprints}")
    if not isinstance(plan_admission, PlanAdmission):
        raise TypeError(
            f"plan_admission must be PlanAdmission, got {type(plan_admission).__name__}"
        )

    root = Path(worktree_root)
    goal_provider = goal_for_step or (lambda step, _sprint_number: step.description)

    def _emit(event: RunTaskEvent) -> None:
        if observer is not None:
            observer.on_event(event)

    _emit(TaskStarted(task_id=task_id, intent=intent))

    ledger_path = planner_ledger_path(root, task_id)
    spec_path = planner_spec_path(root, task_id)
    events: list[PlannerStreamEvent] = []
    resume = plan_admission is PlanAdmission.RESUME and ledger_path.is_file()

    if resume:
        _emit(
            PlannerSkipped(
                task_id=task_id,
                reason="resume",
                ledger_path=str(ledger_path),
            )
        )
        ledger = PlannerLedger.load(ledger_path)
        if not spec_path.is_file():
            raise RunTaskError(
                f"resume requested but planner spec missing: {spec_path}",
                phase="plan",
            )
    else:
        _emit(PlannerStarted(task_id=task_id, intent=intent))
        with _phase("plan"):
            planner_result = await run_planner(
                task_id=task_id,
                intent=intent,
                worktree_root=root,
                planner=planner,
            )
        events.extend(planner_result.events)
        ledger_path = planner_result.ledger_path
        spec_path = planner_result.spec_path
        ledger = PlannerLedger.load(ledger_path)
        _emit(
            PlannerCompleted(
                task_id=task_id,
                spec_path=str(spec_path),
                ledger_path=str(ledger_path),
                step_count=len(ledger.steps),
            )
        )

    sprints: list[SprintRunResult] = []
    # Per-invocation only — durable status stays in_progress so RESUME continues.
    nc_strikes_this_run: dict[str, int] = {}

    first_sprint = next_sprint_number(root, task_id=task_id) if resume else 1
    for sprint_offset in range(max_sprints):
        sprint_number = first_sprint + sprint_offset
        with _phase("sprint"):
            gen = await _run_generator_phase(
                root=root,
                task_id=task_id,
                ledger_path=ledger_path,
                sprint_number=sprint_number,
                ledger=ledger,
                generator_execute=generator_execute,
                verification_steps=verification_steps,
                goal_provider=goal_provider,
                emit=_emit,
                rubric=rubric,
            )
        ledger = gen.ledger
        if gen.step is None:
            break
        step = gen.step

        with _phase("evaluate"):
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
                evaluation=evl.evaluation,
            )
        )
        events.extend(sprint_events)
        _emit(
            SprintCompleted(
                sprint_number=sprint_number,
                step_id=step.id,
                outcome=evl.outcome,
            )
        )

        if evl.outcome == "needs-changes":
            strikes = nc_strikes_this_run.get(step.id, 0) + 1
            nc_strikes_this_run[step.id] = strikes
            if strikes >= NEEDS_CHANGES_LIMIT:
                updated = next((s for s in ledger.steps if s.id == step.id), None)
                _emit(
                    SprintEscalated(
                        task_id=task_id,
                        step_id=step.id,
                        sprint_number=sprint_number,
                        needs_changes_count=(
                            updated.needs_changes_count if updated is not None else strikes
                        ),
                        strikes_this_run=strikes,
                        reason=f"needs-changes strike limit ({strikes})",
                    )
                )
                break

    _emit(
        TaskCompleted(
            task_id=task_id,
            sprint_count=len(sprints),
        )
    )
    return RunTaskResult(
        task_id=task_id,
        spec_path=spec_path,
        ledger_path=ledger_path,
        final_ledger=ledger,
        sprints=tuple(sprints),
        events=tuple(events),
    )
