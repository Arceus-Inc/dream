"""Tests for the runner composition primitive (spec 10 slice G).

The runner stitches planner + sprint primitives into a complete task
loop. This slice ships pure composition only: the LLM-driven seams are
injected as callables. Subagent spawn, leader-inbox draining, and the
``harness session`` CLI follow in later slices.

Spec 10 acceptance criteria exercised here:

- #1/#2 planner runs once and writes both artefacts (delegated to slice E).
- #5/#6 generator picks next pending step and transitions it once per sprint.
- #7 contract written **before** the generator touches sources for a sprint,
  assembled from the step's plan-time acceptance criteria.
- #10 evaluator writes exactly one record per sprint.
- #14 generator + evaluator lock-protected per task.
- #20 cross-role handoff events emitted with artefact pointers.
- §"Disabling the evaluator" — task-level off skips contract + record.
- §"Generator + evaluator loop" outcome rules: pass→done, needs-changes→
  stays in_progress with carry items, fail→blocked + tech-debt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# --- helpers ------------------------------------------------------------


def _make_planner(*, steps: int, evaluator_enabled: bool = True):
    """Return a PlannerCallable that emits a ledger with ``steps`` pending steps."""
    from dream.planner import LedgerStep, PlannerLedger, PlannerOutput

    async def planner(task_id: str, intent: str) -> PlannerOutput:
        return PlannerOutput(
            spec_markdown=f"# Plan: {intent}\n",
            ledger=PlannerLedger(
                task_id=task_id,
                intent=intent,
                created_at=1.0,
                steps=tuple(
                    LedgerStep(
                        id=f"s{i}",
                        description=f"do step {i}",
                        acceptance_criteria=(f"step {i} is done",),
                    )
                    for i in range(1, steps + 1)
                ),
                evaluator_enabled=evaluator_enabled,
            ),
        )

    return planner


def _make_evaluator_run(*, outcome: str, items: tuple[str, ...] = ()):
    """Return an EvaluatorRun that always returns the same outcome."""
    from dream.sprint import EvaluationRecord

    async def evaluator_run(task_id, sprint_n, contract, step):
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_n,
            step_id=step.id,
            outcome=outcome,  # type: ignore[arg-type]
            notes=f"verdict for {step.id} sprint {sprint_n}",
            items=items,
        )

    return evaluator_run


async def _noop_execute(task_id, sprint_n, contract, step) -> None:
    return None


# --- happy path ---------------------------------------------------------


async def test_run_task_writes_planner_artefacts_then_completes(tmp_path: Path) -> None:
    from dream.planner import planner_ledger_path, planner_spec_path
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="ship a thing",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    assert planner_spec_path(tmp_path, "t1").exists()
    assert planner_ledger_path(tmp_path, "t1").exists()
    assert result.task_id == "t1"
    assert result.spec_path == planner_spec_path(tmp_path, "t1")
    assert result.ledger_path == planner_ledger_path(tmp_path, "t1")
    assert len(result.sprints) == 1
    assert result.sprints[0].outcome == "pass"


async def test_run_task_event_stream_starts_with_planner_events(tmp_path: Path) -> None:
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    types = [e.type for e in result.events]
    assert types[0] == "planner.run.completed"
    assert types[1] == "handoff.planner_to_generator"


async def test_run_task_exposes_the_evaluator_record(tmp_path: Path) -> None:
    from dream.planner import LedgerStep
    from dream.runner import run_task
    from dream.sprint import EvaluationRecord, SprintContract

    record = EvaluationRecord(
        task_id="t1",
        sprint_number=1,
        step_id="s1",
        outcome="pass",
        score=0.9,
    )

    async def evaluator_run(
        task_id: str,
        sprint_number: int,
        contract: SprintContract,
        step: LedgerStep,
    ) -> EvaluationRecord:
        assert (task_id, sprint_number, contract.goal, step.id) == (
            "t1",
            1,
            "do step 1",
            "s1",
        )
        return record

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=evaluator_run,
    )

    assert result.sprints[0].evaluation is record


# --- criterion #7: contract before code ---------------------------------


async def test_run_task_writes_contract_before_generator_executes(tmp_path: Path) -> None:
    """Criterion #7: no generator-side work may predate the sprint contract."""
    from dream.runner import run_task
    from dream.sprint import sprint_contract_path

    saw_contract_at_execute: list[bool] = []

    async def execute(task_id, sprint_n, contract, step) -> None:
        path = sprint_contract_path(tmp_path, task_id=task_id, sprint_number=sprint_n)
        saw_contract_at_execute.append(path.exists())

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    assert saw_contract_at_execute == [True]


# --- criterion #20: handoff events -------------------------------------


async def test_run_task_emits_generator_then_evaluator_handoffs_per_sprint(
    tmp_path: Path,
) -> None:
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    types = [e.type for e in result.events]
    g2e = types.index("handoff.generator_to_evaluator")
    e2g = types.index("handoff.evaluator_to_generator")
    assert g2e < e2g


async def test_run_task_handoff_events_carry_artefact_pointers(tmp_path: Path) -> None:
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )
    for e in result.events:
        typ = e.type
        if typ.startswith("handoff."):
            arts = e.artefacts
            assert arts, f"empty artefacts in handoff: {e}"


# --- outcome rules ------------------------------------------------------


async def test_run_task_pass_marks_step_done_and_advances(tmp_path: Path) -> None:
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=2),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t1"))
    assert [s.status for s in final.steps] == ["done", "done"]
    assert result.final_ledger == final
    assert len(result.sprints) == 2
    assert [sp.step_id for sp in result.sprints] == ["s1", "s2"]


async def test_run_task_fail_blocks_step_and_appends_tech_debt(tmp_path: Path) -> None:
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import run_task
    from dream.sprint import tech_debt_path

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="fail", items=("redo X",)),
    )

    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t1"))
    assert final.steps[0].status == "blocked"
    debt = tech_debt_path(tmp_path)
    assert debt.exists()
    assert "redo X" in debt.read_text(encoding="utf-8")
    assert result.sprints[0].outcome == "fail"


async def test_run_task_needs_changes_resumes_same_step_with_carry_items(
    tmp_path: Path,
) -> None:
    """The next sprint MUST re-attempt the same in_progress step, and the
    contract it builds MUST carry the prior eval's unresolved items."""
    from dream.runner import run_task
    from dream.sprint import EvaluationRecord

    seen_criteria: list[tuple[str, ...]] = []
    outcomes = iter(["needs-changes", "pass"])

    async def evaluator_run(task_id, sprint_n, contract, step):
        seen_criteria.append(contract.acceptance_criteria)
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_n,
            step_id=step.id,
            outcome=next(outcomes),  # type: ignore[arg-type]
            items=("carry-item-A",) if sprint_n == 1 else (),
        )

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=evaluator_run,
        max_sprints=5,
    )

    assert len(result.sprints) == 2
    assert [sp.step_id for sp in result.sprints] == ["s1", "s1"]
    assert [sp.outcome for sp in result.sprints] == ["needs-changes", "pass"]

    # The retry's contract is the plan's bar plus what sprint 1 left open.
    assert seen_criteria == [
        ("step 1 is done",),
        ("step 1 is done", "carry-item-A"),
    ]


# --- evaluator disabling ------------------------------------------------


async def test_run_task_evaluator_disabled_skips_contract_and_record(
    tmp_path: Path,
) -> None:
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import run_task
    from dream.sprint import evaluation_record_path, sprint_contract_path

    async def evaluator_run(task_id, sprint_n, contract, step):
        raise AssertionError("evaluator should not run when disabled")

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1, evaluator_enabled=False),
        generator_execute=_noop_execute,
        evaluator_run=evaluator_run,
    )

    assert not sprint_contract_path(tmp_path, task_id="t1", sprint_number=1).exists()
    assert not evaluation_record_path(tmp_path, task_id="t1", sprint_number=1).exists()

    # The disabled path still needs to advance the step (criterion #6) so
    # the task can complete. Spec §"Disabling": "pass" is implicit.
    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t1"))
    assert final.steps[0].status == "done"
    assert result.sprints[0].outcome is None
    assert result.sprints[0].contract_path is None
    assert result.sprints[0].eval_path is None
    assert result.sprints[0].evaluation is None


# --- termination --------------------------------------------------------


async def test_run_task_stops_when_no_more_pending_steps(tmp_path: Path) -> None:
    """A planner ledger with zero steps yields zero sprints."""
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=0),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )
    assert result.sprints == ()


async def test_run_task_respects_max_sprints_cap(tmp_path: Path) -> None:
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=10),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        max_sprints=2,
    )
    assert len(result.sprints) == 2


# --- criterion #14: per-role lock --------------------------------------


async def test_run_task_holds_generator_lock_during_execute(tmp_path: Path) -> None:
    """While the generator callable runs, a concurrent attempt to acquire
    the same per-task generator lock MUST be refused (#14)."""
    from dream.runner import run_task
    from dream.sprint import RoleAlreadyActive, acquire_role_lock

    contention: list[type[BaseException] | None] = []

    async def execute(task_id, sprint_n, contract, step) -> None:
        try:
            with acquire_role_lock(tmp_path, task_id=task_id, role="generator"):
                contention.append(None)  # acquired => bug
        except RoleAlreadyActive as exc:
            contention.append(type(exc))

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )
    assert contention == [RoleAlreadyActive]


async def test_run_task_selects_step_after_acquiring_generator_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selection + claim of the next step must happen *after* the generator
    lock is acquired, on a freshly re-loaded ledger.

    Regression for the select-before-lock race: picking the step from a stale
    snapshot before the lock lets two overlapping run_task calls claim the
    same pending step. We assert the ordering structurally — every in-loop
    ledger re-load is bracketed by a held generator lock.
    """
    from dream.runner import task as _run

    lock_depth = 0
    loads_under_lock: list[bool] = []

    real_acquire = _run.acquire_role_lock
    real_load = _run.PlannerLedger.load

    import contextlib

    @contextlib.contextmanager
    def tracking_acquire(root, *, task_id, role):  # type: ignore[no-untyped-def]
        nonlocal lock_depth
        with real_acquire(root, task_id=task_id, role=role) as p:
            if role == "generator":
                lock_depth += 1
            try:
                yield p
            finally:
                if role == "generator":
                    lock_depth -= 1

    def tracking_load(path):  # type: ignore[no-untyped-def]
        loads_under_lock.append(lock_depth > 0)
        return real_load(path)

    monkeypatch.setattr(_run, "acquire_role_lock", tracking_acquire)
    monkeypatch.setattr(_run.PlannerLedger, "load", staticmethod(tracking_load))

    await _run.run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=2),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    # The initial post-planner load happens before the loop (no lock); every
    # in-loop selection load must occur while the generator lock is held.
    assert loads_under_lock.count(True) >= 2  # one per sprint, under lock
    # And there is at least one in-loop load under the lock (the selection).
    assert any(loads_under_lock)


# --- 10-I: observer dispatch -------------------------------------------


async def test_run_task_dispatches_macro_events_in_order(tmp_path: Path) -> None:
    """A capturing observer sees task/planner/sprint/contract/generator/
    evaluator lifecycle events in the order they fire."""
    from dream.runner import run_task
    from dream.runner.observe import CapturingObserver

    observer = CapturingObserver()

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    names = [type(e).__name__ for e in observer.events]
    assert names == [
        "TaskStarted",
        "PlannerStarted",
        "PlannerCompleted",
        "SprintStarted",
        "ContractWritten",
        "GeneratorStarted",
        "GeneratorCompleted",
        "EvaluatorStarted",
        "EvaluatorCompleted",
        "SprintCompleted",
        "TaskCompleted",
    ]


async def test_run_task_observer_carries_payloads(tmp_path: Path) -> None:
    from dream.runner import run_task
    from dream.runner.observe import CapturingObserver

    observer = CapturingObserver()

    await run_task(
        task_id="t1",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    by_type = {type(e): e for e in observer.events}
    from dream.runner.events import (
        ContractWritten,
        EvaluatorCompleted,
        GeneratorStarted,
        PlannerCompleted,
        SprintCompleted,
        SprintStarted,
        TaskCompleted,
        TaskStarted,
    )

    assert by_type[TaskStarted].task_id == "t1"
    assert by_type[TaskStarted].intent == "ship"
    assert by_type[PlannerCompleted].step_count == 1
    assert by_type[SprintStarted].sprint_number == 1
    assert by_type[SprintStarted].step_id == "s1"
    assert by_type[ContractWritten].path
    assert by_type[GeneratorStarted].has_contract is True
    assert by_type[EvaluatorCompleted].outcome == "pass"
    assert by_type[SprintCompleted].outcome == "pass"
    assert by_type[TaskCompleted].sprint_count == 1


async def test_run_task_observer_marks_generator_without_contract_when_eval_off(
    tmp_path: Path,
) -> None:
    from dream.runner import run_task
    from dream.runner.observe import CapturingObserver

    observer = CapturingObserver()

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1, evaluator_enabled=False),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    from dream.runner.events import (
        ContractWritten,
        EvaluatorCompleted,
        EvaluatorStarted,
        GeneratorStarted,
    )

    types = {type(e) for e in observer.events}
    assert ContractWritten not in types
    assert EvaluatorStarted not in types
    assert EvaluatorCompleted not in types
    gen_start = next(e for e in observer.events if isinstance(e, GeneratorStarted))
    assert gen_start.has_contract is False


async def test_run_task_with_no_observer_does_not_break(tmp_path: Path) -> None:
    """observer=None is the default — runner stays silent and works."""
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    assert result.sprints[0].outcome == "pass"


# --- Fix 2: N-strikes escalation + sprint.escalated event -----------------


def _make_evaluator_run_with_notes(*, outcome: str, notes: str = "") -> Any:
    """Return an EvaluatorRun that always uses the given outcome and notes."""
    from dream.sprint import EvaluationRecord

    async def evaluator_run(task_id, sprint_n, contract, step):
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_n,
            step_id=step.id,
            outcome=outcome,  # type: ignore[arg-type]
            notes=notes,
        )

    return evaluator_run


async def test_two_needs_changes_stops_run_without_burning_max_sprints(
    tmp_path: Path,
) -> None:
    """After NEEDS_CHANGES_LIMIT needs-changes in one run_task the loop stops
    while the step stays in_progress for a later RESUME."""
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run_with_notes(
            outcome="needs-changes", notes="structural issue"
        ),
        max_sprints=20,
    )

    assert len(result.sprints) == 2
    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t1"))
    assert final.steps[0].status == "in_progress"
    assert final.steps[0].needs_changes_count == 2


async def test_two_needs_changes_emits_sprint_escalated_event(
    tmp_path: Path,
) -> None:
    """When the per-invocation strike limit is hit, emit sprint.escalated."""
    from dream.runner import run_task
    from dream.runner.observe import CapturingObserver

    observer = CapturingObserver()

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run_with_notes(
            outcome="needs-changes", notes="bad"
        ),
        max_sprints=20,
        observer=observer,
    )

    from dream.runner.events import SprintEscalated
    escalated = [e for e in observer.events if isinstance(e, SprintEscalated)]
    assert len(escalated) == 1
    assert escalated[0].task_id == "t1"
    assert escalated[0].step_id == "s1"
    assert escalated[0].needs_changes_count == 2
    assert escalated[0].strikes_this_run == 2


async def test_sprint_escalated_not_emitted_on_first_needs_changes(
    tmp_path: Path,
) -> None:
    """Escalation fires only at the per-invocation limit — not on first NC."""
    from dream.runner import run_task
    from dream.runner.observe import CapturingObserver
    from dream.sprint import EvaluationRecord

    observer = CapturingObserver()
    outcomes = iter(["needs-changes", "pass"])

    async def evaluator_run(task_id, sprint_n, contract, step):
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_n,
            step_id=step.id,
            outcome=next(outcomes),  # type: ignore[arg-type]
            notes="note" if sprint_n == 1 else "",
        )

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=evaluator_run,
        max_sprints=10,
        observer=observer,
    )

    from dream.runner.events import SprintEscalated
    escalated = [e for e in observer.events if isinstance(e, SprintEscalated)]
    assert escalated == []


# --- Hermes-simple resume (PlanAdmission.RESUME) -------------------------


async def test_resume_skips_planner_and_continues_in_progress_step(
    tmp_path: Path,
) -> None:
    """Same task_id + RESUME must not re-plan; it continues needs-changes repair."""
    from dream.planner import planner_ledger_path
    from dream.runner import PlanAdmission, run_task
    from dream.runner.observe import CapturingObserver
    from dream.sprint import EvaluationRecord

    outcomes = iter(["needs-changes", "pass"])

    async def evaluator_run(task_id, sprint_n, contract, step):
        outcome = next(outcomes)
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_n,
            step_id=step.id,
            outcome=outcome,  # type: ignore[arg-type]
            notes=f"{outcome} for {step.id}",
            items=("fix the gap",) if outcome == "needs-changes" else (),
        )

    first = await run_task(
        task_id="stable-t1",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=evaluator_run,
        max_sprints=1,
        plan_admission=PlanAdmission.FRESH,
    )
    assert first.final_ledger.steps[0].status == "in_progress"
    assert first.sprints[0].outcome == "needs-changes"

    observer = CapturingObserver()
    second = await run_task(
        task_id="stable-t1",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=evaluator_run,
        max_sprints=3,
        plan_admission=PlanAdmission.RESUME,
        observer=observer,
    )
    from dream.runner.events import PlannerSkipped
    skipped = [e for e in observer.events if isinstance(e, PlannerSkipped)]
    assert len(skipped) == 1
    assert skipped[0].reason == "resume"
    assert second.final_ledger.steps[0].status == "done"
    assert any(s.outcome == "pass" for s in second.sprints)
    assert planner_ledger_path(tmp_path, "stable-t1").is_file()


async def test_resume_after_strike_limit_continues_naturally(
    tmp_path: Path,
) -> None:
    """After the strike limit stops a beat, RESUME continues in_progress — no reopen."""
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import PlanAdmission, run_task

    first = await run_task(
        task_id="t-resume-after-limit",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run_with_notes(
            outcome="needs-changes", notes="stdlib queue shim"
        ),
        max_sprints=6,
        plan_admission=PlanAdmission.FRESH,
    )
    assert len(first.sprints) == 2
    assert first.final_ledger.steps[0].status == "in_progress"

    second = await run_task(
        task_id="t-resume-after-limit",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        max_sprints=3,
        plan_admission=PlanAdmission.RESUME,
    )
    assert len(second.sprints) >= 1
    assert any(s.outcome == "pass" for s in second.sprints)
    assert second.final_ledger.steps[0].status == "done"
    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t-resume-after-limit"))
    assert "stdlib queue shim" in (final.steps[0].notes or "")


async def test_resume_without_ledger_plans_fresh(tmp_path: Path) -> None:
    from dream.runner import PlanAdmission, run_task

    result = await run_task(
        task_id="new-t",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        plan_admission=PlanAdmission.RESUME,
    )
    assert result.final_ledger.steps[0].status == "done"


async def test_plan_admission_rejects_stringly_values(tmp_path: Path) -> None:
    from dream.runner import run_task

    with pytest.raises(TypeError, match="PlanAdmission"):
        await run_task(
            task_id="t",
            intent="x",
            worktree_root=tmp_path,
            planner=_make_planner(steps=1),
            generator_execute=_noop_execute,
            evaluator_run=_make_evaluator_run(outcome="pass"),
            plan_admission="resume",  # type: ignore[arg-type]
        )
