"""Regression: the planner runs-once guard must be race-safe.

Spec 10 criterion #1 — the planner runs exactly once per task. A plain
``exists()``-then-write leaves a TOCTOU window: two concurrent
``run_planner`` calls can both pass the existence check before either
writes, then both invoke the planner and overwrite the artefacts.

``run_planner`` lives in ``src/dream/planner`` but is composed by the
runner, so its concurrency contract is exercised here alongside the rest
of the run-task surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest


async def _stub_planner(task_id: str, intent: str):
    from dream.planner import LedgerStep, PlannerLedger, PlannerOutput

    return PlannerOutput(
        spec_markdown=f"# Plan\n\n{intent}\n",
        ledger=PlannerLedger(
            task_id="x",
            intent=intent,
            created_at=1.0,
            steps=(LedgerStep(id="s0", description="step 0"),),
        ),
    )


async def test_run_planner_guard_and_writes_are_lock_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existence check and both artefact writes must run inside a single
    exclusive-lock window so concurrent callers can't both claim the task."""
    import contextlib

    from dream.planner import _run

    events: list[str] = []

    @contextlib.contextmanager
    def fake_lock(lock_path, *args, **kwargs):  # type: ignore[no-untyped-def]
        events.append("lock-acquire")
        try:
            yield
        finally:
            events.append("lock-release")

    real_write = _run.atomic_write_text

    def spy_write(path, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        events.append("write-spec")
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(_run, "exclusive_file_lock", fake_lock)
    monkeypatch.setattr(_run, "atomic_write_text", spy_write)

    await _run.run_planner(
        task_id="abc-1",
        intent="add foo",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )

    assert events[0] == "lock-acquire"
    assert events[-1] == "lock-release"
    assert "write-spec" in events
    assert events.index("lock-acquire") < events.index("write-spec")
    assert events.index("write-spec") < events.index("lock-release")


async def test_run_planner_still_refuses_second_run(tmp_path: Path) -> None:
    """The lock must not weaken the runs-once refusal."""
    from dream.planner import PlannerAlreadyRan, run_planner

    await run_planner(
        task_id="abc-1",
        intent="add foo",
        worktree_root=tmp_path,
        planner=_stub_planner,
    )
    with pytest.raises(PlannerAlreadyRan):
        await run_planner(
            task_id="abc-1",
            intent="add foo again",
            worktree_root=tmp_path,
            planner=_stub_planner,
        )
