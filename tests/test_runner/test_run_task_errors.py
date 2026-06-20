"""The ``run_task`` failure contract (chorus spec 05 §5).

A fault inside the loop surfaces as a typed :class:`RunTaskError` naming the
``phase`` it broke in (``plan`` / ``sprint`` / ``evaluate``) with the original
``cause`` attached — never a bare provider/tool exception the consumer would
have to string-match. A cooperative :class:`TaskCancelled` and an
``asyncio.CancelledError` propagate untouched (they are not loop faults).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dream.errors import RunTaskError, TaskCancelled
from dream.runner import run_task


def _make_planner(*, steps: int = 1, evaluator_enabled: bool = True):
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


def _propose(criteria: list[str]):
    def propose(round_num, log):
        return list(criteria)

    return propose


def _respond():
    def respond(round_num, log, proposal):
        return True, None

    return respond


def _evaluator_run(*, outcome: str = "pass"):
    from dream.sprint import EvaluationRecord

    async def evaluator_run(task_id, sprint_n, contract, step):
        return EvaluationRecord(
            task_id=task_id,
            sprint_number=sprint_n,
            step_id=step.id,
            outcome=outcome,  # type: ignore[arg-type]
            notes="ok",
        )

    return evaluator_run


async def _noop_execute(task_id, sprint_n, contract, step) -> None:
    return None


def _run(tmp_path: Path, **overrides):
    kwargs = {
        "task_id": "t1",
        "intent": "x",
        "worktree_root": tmp_path,
        "planner": _make_planner(),
        "generator_execute": _noop_execute,
        "evaluator_propose": _propose(["c"]),
        "generator_respond": _respond(),
        "evaluator_run": _evaluator_run(),
    }
    kwargs.update(overrides)
    return run_task(**kwargs)


# --- a fault in each phase becomes a typed RunTaskError ------------------------


async def test_planner_fault_raises_run_task_error_phase_plan(tmp_path: Path) -> None:
    boom = RuntimeError("planner blew up")

    async def planner(task_id: str, intent: str):
        raise boom

    with pytest.raises(RunTaskError) as exc:
        await _run(tmp_path, planner=planner)
    assert exc.value.phase == "plan"
    assert exc.value.cause is boom


async def test_generator_fault_raises_run_task_error_phase_sprint(tmp_path: Path) -> None:
    boom = RuntimeError("tool crashed")

    async def execute(task_id, sprint_n, contract, step) -> None:
        raise boom

    with pytest.raises(RunTaskError) as exc:
        await _run(tmp_path, generator_execute=execute)
    assert exc.value.phase == "sprint"
    assert exc.value.cause is boom


async def test_evaluator_fault_raises_run_task_error_phase_evaluate(tmp_path: Path) -> None:
    boom = RuntimeError("evaluator crashed")

    async def evaluator_run(task_id, sprint_n, contract, step):
        raise boom

    with pytest.raises(RunTaskError) as exc:
        await _run(tmp_path, evaluator_run=evaluator_run)
    assert exc.value.phase == "evaluate"
    assert exc.value.cause is boom


# --- cooperative cancel + asyncio cancellation propagate untouched ------------


async def test_task_cancelled_propagates_not_wrapped(tmp_path: Path) -> None:
    async def execute(task_id, sprint_n, contract, step) -> None:
        raise TaskCancelled("operator cancelled")

    with pytest.raises(TaskCancelled):
        await _run(tmp_path, generator_execute=execute)


async def test_asyncio_cancellation_propagates_not_wrapped(tmp_path: Path) -> None:
    async def execute(task_id, sprint_n, contract, step) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _run(tmp_path, generator_execute=execute)
