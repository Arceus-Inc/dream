"""Generator-side ledger primitives.

Spec 10 acceptance criteria #5, #6:

- The generator picks the next ``pending`` step at sprint start (#5).
- A step transitions out of ``pending`` **exactly once** per sprint (#6).

These functions are pure ledger transitions. The runner (slice 10-G)
composes them with the contract writer and the LLM-driven code edits.
"""

from __future__ import annotations

from dataclasses import replace

from dream.planner import LedgerStep, PlannerLedger

from ._ledger_ops import replace_step_by_id

__all__ = [
    "StepNotPending",
    "pick_next_pending_step",
    "transition_step_to_in_progress",
]


class StepNotPending(RuntimeError):
    """Raised when a sprint tries to claim a non-pending step.

    Acceptance criterion #6 — once a step leaves ``pending`` it cannot
    re-enter; re-claiming it would mean two sprints overlapping on the
    same unit of work."""


def pick_next_pending_step(ledger: PlannerLedger) -> LedgerStep | None:
    """Return the first pending step in ledger order, or ``None`` if all done."""
    for step in ledger.steps:
        if step.status == "pending":
            return step
    return None


def transition_step_to_in_progress(
    ledger: PlannerLedger, step_id: str
) -> PlannerLedger:
    """Return a new ledger with ``step_id`` moved from ``pending`` → ``in_progress``."""

    def _claim(step: LedgerStep) -> LedgerStep:
        if step.status != "pending":
            raise StepNotPending(
                f"cannot transition step {step_id!r}: status is {step.status!r}, not 'pending'"
            )
        return replace(step, status="in_progress")

    return replace_step_by_id(ledger, step_id, _claim)
