"""Shared ledger-splice primitive for sprint transitions.

Both :func:`dream.sprint._outcome.apply_outcome` and
:func:`dream.sprint._generator.transition_step_to_in_progress` perform the
same immutable splice: find the step by id, replace it with a mutated copy,
and return a new ledger — raising ``KeyError`` when the id is absent. They
differ only in the per-step ``mutate`` rule (and the guard it enforces),
which is supplied as a callback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from dream.planner import LedgerStep, PlannerLedger

__all__ = ["replace_step_by_id"]


def replace_step_by_id(
    ledger: PlannerLedger,
    step_id: str,
    mutate: Callable[[LedgerStep], LedgerStep],
) -> PlannerLedger:
    """Return a new ledger with ``step_id`` replaced by ``mutate(step)``.

    ``mutate`` receives the matched step and returns its replacement; it may
    raise to reject the transition (e.g. a wrong-status guard). Raises
    ``KeyError`` if no step with ``step_id`` is present.
    """
    new_steps = list(ledger.steps)
    for i, step in enumerate(new_steps):
        if step.id == step_id:
            new_steps[i] = mutate(step)
            return replace(ledger, steps=tuple(new_steps))
    raise KeyError(f"step id not in ledger: {step_id!r}")
