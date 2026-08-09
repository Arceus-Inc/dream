"""Assemble a sprint contract from the plan.

Acceptance criteria are decided at plan time: naming the work and naming what
"done" means for it is one judgement, so the planner writes both onto the
ledger step and the sprint reads them here. Nothing is negotiated, and no role
session runs before the generator starts.

The one thing that still moves a contract after planning is a
``needs-changes`` verdict — its unresolved items become criteria the retry has
to meet, which is how the evaluator steers a repair without a second
conversation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._contract import SprintContract

if TYPE_CHECKING:
    from dream.planner import LedgerStep

__all__ = ["build_contract_from_step"]


def build_contract_from_step(
    step: LedgerStep,
    *,
    task_id: str,
    sprint_number: int,
    goal: str,
    verification_steps: tuple[dict[str, str], ...],
    carry_items: tuple[str, ...] = (),
    evaluator_enabled: bool = True,
    scope_includes: tuple[str, ...] = (),
    scope_excludes: tuple[str, ...] = (),
    rubric: str = "",
) -> SprintContract:
    """Build this sprint's contract from ``step`` plus any carried-over items."""
    return SprintContract(
        task_id=task_id,
        sprint_number=sprint_number,
        goal=goal,
        scope_includes=scope_includes,
        scope_excludes=scope_excludes,
        acceptance_criteria=_criteria_for(step, carry_items),
        verification_steps=verification_steps,
        evaluator_enabled=evaluator_enabled,
        rubric=rubric,
    )


def _criteria_for(step: LedgerStep, carry_items: tuple[str, ...]) -> tuple[str, ...]:
    """The step's criteria, extended by anything the last evaluation left open.

    A contract needs at least one criterion. A planner that left the step's
    criteria empty has still said what the work is, so the description stands
    in — a weak bar beats failing the sprint over a missing field.
    """
    criteria = list(step.acceptance_criteria)
    for item in carry_items:
        if item not in criteria:
            criteria.append(item)
    if not criteria:
        return (step.description,)
    return tuple(criteria)
