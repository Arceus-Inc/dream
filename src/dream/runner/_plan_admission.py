"""How ``run_task`` admits planning for a task id.

Hermes-simple recovery needs the same Dream ``task_id`` across Chorus ticks.
Planner-runs-once still holds: we never overwrite an existing plan. Instead
:attr:`PlanAdmission.RESUME` skips the planner when a ledger already exists and
continues the sprint loop (needs-changes / in_progress carry-forward).
"""

from __future__ import annotations

from enum import Enum


class PlanAdmission(str, Enum):
    """Admission policy for the planner phase of :func:`dream.runner.run_task`.

    Using a str Enum (not free-form strings) keeps call sites typed and
    serialisable without a parallel stringly vocabulary.
    """

    FRESH = "fresh"
    """Always invoke the planner. Raises ``PlannerAlreadyRan`` if artefacts exist."""

    RESUME = "resume"
    """Skip the planner when a ledger already exists; otherwise plan once."""


__all__ = ["PlanAdmission"]
