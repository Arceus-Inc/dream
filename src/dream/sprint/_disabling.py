"""Evaluator on/off resolution.

Spec 10 §"Disabling the evaluator":

- task-level flag lives on the planner ledger (``PlannerLedger.evaluator_enabled``).
- sprint-level flag lives on the sprint contract; when set, it overrides
  the task-level *only in the disabling direction* — a sprint can opt
  out of evaluation but cannot re-arm it after the task disabled it.
"""

from __future__ import annotations

from dream.planner import PlannerLedger

__all__ = ["is_evaluator_enabled_for_sprint"]


def is_evaluator_enabled_for_sprint(
    ledger: PlannerLedger, *, sprint_override: bool | None
) -> bool:
    """Resolve the effective evaluator-enabled flag for a sprint.

    Truth table::

        task=True,  sprint=None  → True
        task=True,  sprint=True  → True
        task=True,  sprint=False → False   (sprint opts out)
        task=False, sprint=None  → False
        task=False, sprint=True  → False   (sprint cannot re-arm)
        task=False, sprint=False → False
    """
    if not ledger.evaluator_enabled:
        return False
    if sprint_override is False:
        return False
    return True
