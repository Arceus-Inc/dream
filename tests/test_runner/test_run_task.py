"""Tests for the runner composition primitive (spec 10 slice G).

The runner stitches planner + sprint primitives into a complete task
loop. This slice ships pure composition only: the LLM-driven seams are
injected as callables. Subagent spawn, leader-inbox draining, and the
``harness session`` CLI follow in later slices.

Spec 10 acceptance criteria exercised here:

- #1/#2 planner runs once and writes both artefacts (delegated to slice E).
- #5/#6 generator picks next pending step and transitions it once per sprint.
- #7 contract written **before** the generator touches sources for a sprint.
- #9 negotiation bounded at 3 rounds; imposed proposal surfaces a warning.
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
                    LedgerStep(id=f"s{i}", description=f"do step {i}")
                    for i in range(1, steps + 1)
                ),
                evaluator_enabled=evaluator_enabled,
            ),
        )

    return planner


def _accept_first_proposal_propose(criteria: list[str]):
    """Evaluator proposes ``criteria`` on round 1 and never moves."""
    def propose(round_num, log):
        return list(criteria)
    return propose


def _accept_first_proposal_respond():
    """Generator accepts whatever the evaluator proposes on round 1."""
    def respond(round_num, log, proposal):
        return True, None
    return respond


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
        evaluator_propose=_accept_first_proposal_propose(["criterion-A"]),
        generator_respond=_accept_first_proposal_respond(),
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    types = [e["type"] for e in result.events]
    assert types[0] == "planner.run.completed"
    assert types[1] == "handoff.planner_to_generator"


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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    types = [e["type"] for e in result.events]
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )
    for e in result.events:
        if e["type"].startswith("handoff."):
            assert e["artefacts"], f"empty artefacts in handoff: {e}"


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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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
    """The next sprint MUST re-attempt the same in_progress step and the
    new negotiation MUST see the prior eval's items as carry-overs."""
    from dream.runner import run_task
    from dream.sprint import EvaluationRecord

    sprint_carry_log: list[tuple[int, list[Any]]] = []

    def propose(round_num, log):
        sprint_carry_log.append((round_num, list(log)))
        # On the second sprint, the log will already contain carry entries —
        # we just keep proposing the same criterion.
        return ["c"]

    outcomes = iter(["needs-changes", "pass"])

    async def evaluator_run(task_id, sprint_n, contract, step):
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
        evaluator_propose=propose,
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=evaluator_run,
        max_sprints=5,
    )

    assert len(result.sprints) == 2
    assert [sp.step_id for sp in result.sprints] == ["s1", "s1"]
    assert [sp.outcome for sp in result.sprints] == ["needs-changes", "pass"]

    # Second sprint's first-round negotiation log must contain the carry
    # entry produced from sprint 1's needs-changes items.
    second_sprint_first_round_log = sprint_carry_log[1][1]
    messages = [entry.message for entry in second_sprint_first_round_log]
    assert any("carry-item-A" in m for m in messages), (
        f"carry items missing from second negotiation log: {messages}"
    )


# --- evaluator disabling ------------------------------------------------


async def test_run_task_evaluator_disabled_skips_contract_and_record(
    tmp_path: Path,
) -> None:
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import run_task
    from dream.sprint import evaluation_record_path, sprint_contract_path

    called: dict[str, int] = {"propose": 0, "evaluate": 0}

    def propose(round_num, log):
        called["propose"] += 1
        return ["c"]

    async def evaluator_run(task_id, sprint_n, contract, step):
        called["evaluate"] += 1
        raise AssertionError("evaluator should not run when disabled")

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1, evaluator_enabled=False),
        generator_execute=_noop_execute,
        evaluator_propose=propose,
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=evaluator_run,
    )

    assert called == {"propose": 0, "evaluate": 0}
    assert not sprint_contract_path(tmp_path, task_id="t1", sprint_number=1).exists()
    assert not evaluation_record_path(tmp_path, task_id="t1", sprint_number=1).exists()

    # The disabled path still needs to advance the step (criterion #6) so
    # the task can complete. Spec §"Disabling": "pass" is implicit.
    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t1"))
    assert final.steps[0].status == "done"
    assert result.sprints[0].outcome is None
    assert result.sprints[0].contract_path is None
    assert result.sprints[0].eval_path is None


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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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
    from dream.runner import _run

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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    # The initial post-planner load happens before the loop (no lock); every
    # in-loop selection load must occur while the generator lock is held.
    assert loads_under_lock.count(True) >= 2  # one per sprint, under lock
    # And there is at least one in-loop load under the lock (the selection).
    assert any(loads_under_lock)


# --- criterion #9: imposed negotiation surfaces warning ----------------


async def test_run_task_imposed_negotiation_emits_warning_event(tmp_path: Path) -> None:
    """When negotiation caps without agreement the evaluator's last proposal
    is committed with ``imposed: true`` and a warning event is emitted."""
    from dream.runner import run_task

    def propose(round_num, log):
        return [f"r{round_num}-criteria"]

    def respond(round_num, log, proposal):
        return False, ["counter"]  # never accept

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=propose,
        generator_respond=respond,
        evaluator_run=_make_evaluator_run(outcome="pass"),
    )

    warnings = [e for e in result.events if e.get("type") == "sprint.negotiation_imposed"]
    assert len(warnings) == 1
    assert warnings[0]["level"] == "warning"
    assert warnings[0]["rounds"] == 3


# --- 10-I: observer dispatch -------------------------------------------


async def test_run_task_dispatches_macro_events_in_order(tmp_path: Path) -> None:
    """A capturing observer sees task/planner/sprint/contract/generator/
    evaluator/negotiation lifecycle events in the order they fire."""
    from dream.runner import run_task
    from dream.runner._observer import _CapturingObserver

    observer = _CapturingObserver()

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    kinds = [e["kind"] for e in observer.events]
    assert kinds == [
        "task.started",
        "planner.started",
        "planner.completed",
        "sprint.started",
        "contract.written",
        "generator.started",
        "generator.completed",
        "evaluator.started",
        "evaluator.completed",
        "sprint.completed",
        "task.completed",
    ]


async def test_run_task_observer_carries_payloads(tmp_path: Path) -> None:
    from dream.runner import run_task
    from dream.runner._observer import _CapturingObserver

    observer = _CapturingObserver()

    await run_task(
        task_id="t1",
        intent="ship",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    by_kind = {e["kind"]: e for e in observer.events}
    assert by_kind["task.started"]["task_id"] == "t1"
    assert by_kind["task.started"]["intent"] == "ship"
    assert by_kind["planner.completed"]["step_count"] == 1
    assert by_kind["sprint.started"]["sprint_number"] == 1
    assert by_kind["sprint.started"]["step_id"] == "s1"
    assert "path" in by_kind["contract.written"]
    assert by_kind["generator.started"]["has_contract"] is True
    assert by_kind["evaluator.completed"]["outcome"] == "pass"
    assert by_kind["sprint.completed"]["outcome"] == "pass"
    assert by_kind["task.completed"]["sprint_count"] == 1


async def test_run_task_observer_marks_generator_without_contract_when_eval_off(
    tmp_path: Path,
) -> None:
    from dream.runner import run_task
    from dream.runner._observer import _CapturingObserver

    observer = _CapturingObserver()

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1, evaluator_enabled=False),
        generator_execute=_noop_execute,
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    kinds = [e["kind"] for e in observer.events]
    assert "contract.written" not in kinds
    assert "evaluator.started" not in kinds
    assert "evaluator.completed" not in kinds
    gen_start = next(e for e in observer.events if e["kind"] == "generator.started")
    assert gen_start["has_contract"] is False


async def test_run_task_observer_emits_negotiation_imposed_at_cap(
    tmp_path: Path,
) -> None:
    from dream.runner import run_task
    from dream.runner._observer import _CapturingObserver

    def propose(round_num, log):
        return [f"r{round_num}"]

    def respond(round_num, log, proposal):
        return False, ["counter"]

    observer = _CapturingObserver()
    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=propose,
        generator_respond=respond,
        evaluator_run=_make_evaluator_run(outcome="pass"),
        observer=observer,
    )

    imposed = [e for e in observer.events if e["kind"] == "negotiation.imposed"]
    assert len(imposed) == 1
    assert imposed[0]["sprint_number"] == 1
    assert imposed[0]["rounds"] == 3


async def test_run_task_with_no_observer_does_not_break(tmp_path: Path) -> None:
    """observer=None is the default — runner stays silent and works."""
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
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


async def test_two_needs_changes_blocks_step_without_burning_max_sprints(
    tmp_path: Path,
) -> None:
    """After NEEDS_CHANGES_LIMIT (2) needs-changes on the same step the step
    becomes blocked and the loop exits before hitting max_sprints."""
    from dream.planner import PlannerLedger, planner_ledger_path
    from dream.runner import run_task

    result = await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run_with_notes(
            outcome="needs-changes", notes="structural issue"
        ),
        max_sprints=20,
    )

    # Must not have used all 20 sprints — blocked after 2 needs-changes
    assert len(result.sprints) == 2
    final = PlannerLedger.load(planner_ledger_path(tmp_path, "t1"))
    assert final.steps[0].status == "blocked"


async def test_two_needs_changes_emits_sprint_escalated_event(
    tmp_path: Path,
) -> None:
    """When the second needs-changes triggers a block, a sprint.escalated event
    must be dispatched to the observer with needs_changes_count == 2."""
    from dream.runner import run_task
    from dream.runner._observer import _CapturingObserver

    observer = _CapturingObserver()

    await run_task(
        task_id="t1",
        intent="x",
        worktree_root=tmp_path,
        planner=_make_planner(steps=1),
        generator_execute=_noop_execute,
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=_make_evaluator_run_with_notes(
            outcome="needs-changes", notes="bad"
        ),
        max_sprints=20,
        observer=observer,
    )

    escalated = [e for e in observer.events if e.get("kind") == "sprint.escalated"]
    assert len(escalated) == 1
    assert escalated[0]["task_id"] == "t1"
    assert escalated[0]["step_id"] == "s1"
    assert escalated[0]["needs_changes_count"] == 2


async def test_sprint_escalated_not_emitted_on_first_needs_changes(
    tmp_path: Path,
) -> None:
    """The escalation event fires only when the step is blocked — the first
    needs-changes must not emit it."""
    from dream.runner import run_task
    from dream.runner._observer import _CapturingObserver
    from dream.sprint import EvaluationRecord

    observer = _CapturingObserver()
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
        evaluator_propose=_accept_first_proposal_propose(["c"]),
        generator_respond=_accept_first_proposal_respond(),
        evaluator_run=evaluator_run,
        max_sprints=10,
        observer=observer,
    )

    escalated = [e for e in observer.events if e.get("kind") == "sprint.escalated"]
    assert escalated == []
