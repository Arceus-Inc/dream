"""Tests for the planner-runs-once orchestration.

Spec 10 acceptance criteria #1-#4:

- #1 planner runs exactly once per task
- #2 produces both the markdown spec and the json ledger
- #3 restricted to read-only access outside the exec-plan folder
- #4 emits a ``handoff.planner_to_generator`` event at end of run

Plus the gherkin scenario "Planner runs once and produces both artefacts"
which adds the requirement of exactly one ``planner.run.completed`` event.

The slice ships the orchestration shell; the actual LLM call is a
caller-supplied ``PlannerCallable`` (10-G wires the real one).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# --- helpers ------------------------------------------------------------


def _make_planner_output(*, intent: str = "do a thing", steps: int = 2):
    from dream.planner import LedgerStep, PlannerLedger, PlannerOutput

    return PlannerOutput(
        spec_markdown=f"# Plan\n\n{intent}\n",
        ledger=PlannerLedger(
            task_id="will-be-overwritten",
            intent=intent,
            created_at=1.0,
            steps=tuple(
                LedgerStep(id=f"s{i}", description=f"step {i}") for i in range(steps)
            ),
        ),
    )


async def _stub_planner(task_id: str, intent: str):
    """A planner_fn that just echoes intent into a fixed PlannerOutput."""
    out = _make_planner_output(intent=intent)
    return out


# --- artefact production -----------------------------------------------


async def test_planner_produces_spec_and_ledger(tmp_path: Path) -> None:
    from dream.planner import planner_ledger_path, planner_spec_path, run_planner

    result = await run_planner(
        task_id="abc-1",
        intent="add foo",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )

    spec = planner_spec_path(tmp_path, "abc-1")
    ledger = planner_ledger_path(tmp_path, "abc-1")
    assert spec.exists()
    assert ledger.exists()
    assert spec.read_text(encoding="utf-8").startswith("# Plan")
    assert json.loads(ledger.read_text(encoding="utf-8"))["intent"] == "add foo"
    assert result.spec_path == spec
    assert result.ledger_path == ledger


async def test_planner_overwrites_ledger_task_id_with_run_task_id(
    tmp_path: Path,
) -> None:
    """The ledger that lands on disk MUST carry the run's task_id, no matter
    what the planner callable put in its returned object — otherwise a typo
    in the callable would silently produce a ledger keyed to a different task.
    """
    from dream.planner import planner_ledger_path, run_planner

    await run_planner(
        task_id="abc-1",
        intent="x",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )
    data = json.loads(planner_ledger_path(tmp_path, "abc-1").read_text("utf-8"))
    assert data["task_id"] == "abc-1"


# --- planner-runs-once guard --------------------------------------------


async def test_planner_runs_once_at_task_start(tmp_path: Path) -> None:
    from dream.planner import PlannerAlreadyRan, run_planner

    await run_planner(
        task_id="abc-1",
        intent="x",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )
    with pytest.raises(PlannerAlreadyRan):
        await run_planner(
            task_id="abc-1",
            intent="x",
            worktree_root=tmp_path,
            planner=_stub_planner,
        )


async def test_planner_refuses_when_only_ledger_exists(tmp_path: Path) -> None:
    """A half-written prior run (ledger but not spec) is still a prior run —
    re-running would overwrite the partial ledger without an audit trail."""
    from dream.planner import (
        PlannerAlreadyRan,
        planner_ledger_path,
        run_planner,
    )

    p = planner_ledger_path(tmp_path, "abc-1")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")

    with pytest.raises(PlannerAlreadyRan):
        await run_planner(
            task_id="abc-1",
            intent="x",
            worktree_root=tmp_path,
            planner=_stub_planner,
        )


async def test_planner_refuses_when_only_spec_exists(tmp_path: Path) -> None:
    from dream.planner import PlannerAlreadyRan, planner_spec_path, run_planner

    p = planner_spec_path(tmp_path, "abc-1")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# stale", encoding="utf-8")

    with pytest.raises(PlannerAlreadyRan):
        await run_planner(
            task_id="abc-1",
            intent="x",
            worktree_root=tmp_path,
            planner=_stub_planner,
        )


async def test_planner_does_not_invoke_callable_on_runs_once_refusal(
    tmp_path: Path,
) -> None:
    from dream.planner import PlannerAlreadyRan, planner_spec_path, run_planner

    spec = planner_spec_path(tmp_path, "abc-1")
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# stale", encoding="utf-8")

    invoked = []

    async def tracer(task_id: str, intent: str):
        invoked.append((task_id, intent))
        return _make_planner_output()

    with pytest.raises(PlannerAlreadyRan):
        await run_planner(
            task_id="abc-1",
            intent="x",
            worktree_root=tmp_path,
            planner=tracer,
        )
    assert invoked == []


# --- read-only outside exec-plans --------------------------------------


async def test_planner_writes_only_under_exec_plans_active(tmp_path: Path) -> None:
    """Acceptance criterion #3 — the planner must not modify anything in the
    worktree outside ``docs/exec-plans/active``. The orchestrator's contract
    here is structural: it writes exactly the two known files; the role
    manifest's tool denylist (slice 10-A) enforces this at the tool layer."""
    from dream.planner import run_planner

    await run_planner(
        task_id="abc-1",
        intent="x",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )

    # Criterion #3 concerns the *worktree source* outside exec-plans/active.
    # Harness-internal coordination state under ``.dream`` (here: the
    # runs-once lockfile that serializes concurrent run_planner calls) is not
    # part of the worktree source and is excluded from this invariant.
    all_files = {
        p.relative_to(tmp_path).as_posix()
        for p in tmp_path.rglob("*")
        if p.is_file() and not p.relative_to(tmp_path).as_posix().startswith(".dream/")
    }
    assert all_files == {
        "docs/exec-plans/active/abc-1.md",
        "docs/exec-plans/active/abc-1.json",
    }


# --- events -------------------------------------------------------------


async def test_planner_emits_exactly_one_run_completed_event(tmp_path: Path) -> None:
    from dream.planner import run_planner

    result = await run_planner(
        task_id="abc-1",
        intent="x",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )
    completed = [
        e
        for e in result.events
        if (e.type) == "planner.run.completed"
    ]
    assert len(completed) == 1
    assert completed[0].task_id == "abc-1"


async def test_planner_emits_handoff_to_generator_with_both_pointers(
    tmp_path: Path,
) -> None:
    from dream.planner import (
        planner_ledger_path,
        planner_spec_path,
        run_planner,
    )

    result = await run_planner(
        task_id="abc-1",
        intent="x",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )
    handoffs = [
        e
        for e in result.events
        if (e.type) == "handoff.planner_to_generator"
    ]
    assert len(handoffs) == 1
    h = handoffs[0]
    assert h.from_role == "planner"
    assert h.to_role == "generator"
    kinds = {a.kind for a in h.artefacts}
    assert kinds == {"spec", "ledger"}
    paths = {a.path for a in h.artefacts if a.path is not None}
    spec_rel = planner_spec_path(tmp_path, "abc-1").relative_to(tmp_path).as_posix()
    ledger_rel = planner_ledger_path(tmp_path, "abc-1").relative_to(tmp_path).as_posix()
    assert paths == {spec_rel, ledger_rel}


async def test_planner_emits_completed_before_handoff(tmp_path: Path) -> None:
    """The run is only 'completed' once the artefacts are committed; the
    handoff event references those artefacts. Ordering is part of the
    audit-trail contract."""
    from dream.planner import run_planner

    result = await run_planner(
        task_id="abc-1",
        intent="x",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )
    types = [e.type for e in result.events]
    assert types == ["planner.run.completed", "handoff.planner_to_generator"]


# --- callable contract --------------------------------------------------


async def test_planner_callable_receives_task_id_and_intent(tmp_path: Path) -> None:
    from dream.planner import run_planner

    received: dict[str, str] = {}

    async def capturing(task_id: str, intent: str):
        received["task_id"] = task_id
        received["intent"] = intent
        return _make_planner_output(intent=intent)

    await run_planner(
        task_id="abc-1",
        intent="add foo",
        worktree_root=tmp_path,
        planner=capturing,
    )
    assert received == {"task_id": "abc-1", "intent": "add foo"}


async def test_planner_propagates_callable_exception_and_leaves_no_files(
    tmp_path: Path,
) -> None:
    from dream.planner import planner_ledger_path, planner_spec_path, run_planner

    class Boom(RuntimeError):
        pass

    async def failing(task_id: str, intent: str):
        raise Boom("planner exploded")

    with pytest.raises(Boom):
        await run_planner(
            task_id="abc-1",
            intent="x",
            worktree_root=tmp_path,
            planner=failing,
        )
    assert not planner_spec_path(tmp_path, "abc-1").exists()
    assert not planner_ledger_path(tmp_path, "abc-1").exists()


async def test_planner_rejects_invalid_task_id(tmp_path: Path) -> None:
    from dream.planner import run_planner

    with pytest.raises(ValueError, match=r"task_id|unsafe"):
        await run_planner(
            task_id="a/b",
            intent="x",
            worktree_root=tmp_path,
            planner=_stub_planner,
        )
