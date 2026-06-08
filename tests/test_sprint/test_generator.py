"""Tests for the generator side: step picking + ledger transition.

Spec 10 acceptance criteria #5, #6:

- generator MUST pick the next ``pending`` ledger step at each sprint start (#5)
- generator MUST transition a step out of ``pending`` exactly once per sprint (#6)

The actual code execution is the caller's responsibility (10-G wires the
LLM); this slice ships the deterministic ledger primitives.
"""

from __future__ import annotations

import pytest


def _ledger_with(*statuses: str):
    from dream.planner import LedgerStep, PlannerLedger

    return PlannerLedger(
        task_id="t1",
        intent="x",
        created_at=0.0,
        steps=tuple(
            LedgerStep(id=f"s{i + 1}", description=f"d{i + 1}", status=s)
            for i, s in enumerate(statuses)
        ),
    )


def test_pick_next_pending_step_returns_first_pending() -> None:
    from dream.sprint import pick_next_pending_step

    ledger = _ledger_with("done", "pending", "pending")
    step = pick_next_pending_step(ledger)
    assert step is not None
    assert step.id == "s2"


def test_pick_next_pending_step_skips_in_progress_and_blocked() -> None:
    from dream.sprint import pick_next_pending_step

    ledger = _ledger_with("done", "in_progress", "blocked", "pending")
    step = pick_next_pending_step(ledger)
    assert step is not None
    assert step.id == "s4"


def test_pick_next_pending_step_returns_none_when_none_pending() -> None:
    from dream.sprint import pick_next_pending_step

    ledger = _ledger_with("done", "done", "blocked")
    assert pick_next_pending_step(ledger) is None


# --- transition exactly once ------------------------------------------


def test_transition_step_to_in_progress_moves_pending_to_in_progress() -> None:
    from dream.sprint import transition_step_to_in_progress

    ledger = _ledger_with("pending", "pending")
    updated = transition_step_to_in_progress(ledger, "s1")
    by_id = {s.id: s for s in updated.steps}
    assert by_id["s1"].status == "in_progress"
    assert by_id["s2"].status == "pending"


def test_transition_step_to_in_progress_refuses_non_pending() -> None:
    """Acceptance criterion #6 — a step exits ``pending`` exactly once. If a
    sprint tries to claim a step that's already in_progress / done / blocked,
    that's a double-claim bug; refuse rather than silently re-transition."""
    from dream.sprint import StepNotPending, transition_step_to_in_progress

    ledger = _ledger_with("done", "in_progress", "blocked")
    for step_id in ("s1", "s2", "s3"):
        with pytest.raises(StepNotPending):
            transition_step_to_in_progress(ledger, step_id)


def test_transition_step_to_in_progress_raises_on_unknown_id() -> None:
    from dream.sprint import transition_step_to_in_progress

    ledger = _ledger_with("pending")
    with pytest.raises(KeyError, match="nope"):
        transition_step_to_in_progress(ledger, "nope")
